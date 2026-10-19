"""Splits de tickets/dinero del público desde Action Network (consenso, book 15).

Endpoint JSON público que usa su web: /web/v2/scoreboard/publicbetting/nba
Cada partido trae markets["15"]["event"]["spread"|"total"|"moneyline"] con
bet_info.tickets.percent y bet_info.money.percent por lado.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Iterable

import requests

URL = "https://api.actionnetwork.com/web/v2/scoreboard/publicbetting/nba"
CONSENSUS_BOOK_ID = "15"
TIMEOUT_S = 20
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"
ET = ZoneInfo("America/New_York")  # Action Network agrupa por fecha de EE.UU.
# mercado AN -> (mercado Odds API, lado "primero": local u over)
MARKET_MAP = {"spread": ("spreads", "home"), "moneyline": ("h2h", "home"), "total": ("totals", "over")}
TEAM_ALIASES = {"LA Clippers": "Los Angeles Clippers"}


@dataclass(frozen=True)
class Split:
    away: str
    home: str
    start_time: str
    market: str            # spreads / totals / h2h
    tickets_pct: float     # % de tickets en el local (o el over)
    money_pct: float | None
    num_bets: int | None


def dates_to_query(now: datetime, days: int) -> list[date]:
    first = now.astimezone(ET).date()
    return [first + timedelta(days=i) for i in range(days)]


def _team_names(game: dict[str, Any]) -> tuple[str, str] | None:
    names = {t["id"]: TEAM_ALIASES.get(t["full_name"], t["full_name"]) for t in game.get("teams", [])}
    away, home = names.get(game.get("away_team_id")), names.get(game.get("home_team_id"))
    return (away, home) if away and home else None


def _percent(outcome: dict[str, Any], key: str) -> float | None:
    value = (outcome.get("bet_info") or {}).get(key, {}).get("percent")
    return float(value) if value is not None else None


def parse_splits(payload: dict[str, Any]) -> list[Split]:
    splits: list[Split] = []
    for game in payload.get("games", []):
        teams = _team_names(game)
        event = ((game.get("markets") or {}).get(CONSENSUS_BOOK_ID) or {}).get("event") or {}
        if teams is None:
            continue
        for an_key, (market, first_side) in MARKET_MAP.items():
            first = next((o for o in event.get(an_key, []) if o.get("side") == first_side), None)
            tickets = _percent(first, "tickets") if first else None
            if tickets is None or not game.get("num_bets"):
                continue  # sin apuestas registradas todavía
            splits.append(Split(teams[0], teams[1], game.get("start_time", ""), market,
                                tickets, _percent(first, "money"), game.get("num_bets")))
    return splits


def fetch_splits(days: Iterable[date]) -> list[Split]:
    splits: list[Split] = []
    for day in days:
        resp = requests.get(URL, params={"bookIds": CONSENSUS_BOOK_ID, "date": day.strftime("%Y%m%d"),
                                         "periods": "event"},
                            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S)
        resp.raise_for_status()
        splits.extend(parse_splits(resp.json()))
    return splits
