"""Splits de tickets/dinero del público desde Action Network (consenso, book 15).

Endpoint JSON público que usa su web: /web/v2/scoreboard/publicbetting/nba
Cada partido trae markets["15"]["event"]["spread"|"total"|"moneyline"] con
bet_info.tickets.percent y bet_info.money.percent por lado, y markets["30"] con la
línea de apertura del mercado ("Open").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Iterable

import requests

from .lines import implied_prob

URL = "https://api.actionnetwork.com/web/v2/scoreboard/publicbetting/nba"
CONSENSUS_BOOK_ID = "15"
OPEN_BOOK_ID = "30"  # línea de apertura del mercado
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


@dataclass(frozen=True)
class Opener:
    away: str
    home: str
    start_time: str
    market: str   # spreads: puntos del local | totals: total | h2h: prob. sin vig del local (%)
    value: float


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


def _side(outcomes: list[dict[str, Any]], side: str) -> dict[str, Any] | None:
    return next((o for o in outcomes if o.get("side") == side), None)


def _opener_value(an_key: str, outcomes: list[dict[str, Any]]) -> float | None:
    if an_key == "moneyline":
        home, away = _side(outcomes, "home"), _side(outcomes, "away")
        if not home or not away or not home.get("odds") or not away.get("odds"):
            return None
        p_home, p_away = implied_prob(int(home["odds"])), implied_prob(int(away["odds"]))
        return round(100 * p_home / (p_home + p_away), 2)
    first = _side(outcomes, MARKET_MAP[an_key][1])
    return float(first["value"]) if first and first.get("value") is not None else None


def parse_openers(payload: dict[str, Any]) -> list[Opener]:
    openers: list[Opener] = []
    for game in payload.get("games", []):
        teams = _team_names(game)
        event = ((game.get("markets") or {}).get(OPEN_BOOK_ID) or {}).get("event") or {}
        if teams is None:
            continue
        for an_key, (market, _first_side) in MARKET_MAP.items():
            value = _opener_value(an_key, event.get(an_key, []))
            if value is not None:
                openers.append(Opener(teams[0], teams[1], game.get("start_time", ""), market, value))
    return openers


def fetch_board(days: Iterable[date]) -> tuple[list[Split], list[Opener]]:
    """Splits de tickets/dinero y líneas de apertura para los días dados (ET)."""
    splits: list[Split] = []
    openers: list[Opener] = []
    for day in days:
        resp = requests.get(URL, params={"bookIds": f"{CONSENSUS_BOOK_ID},{OPEN_BOOK_ID}",
                                         "date": day.strftime("%Y%m%d"), "periods": "event"},
                            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S)
        resp.raise_for_status()
        payload = resp.json()
        splits.extend(parse_splits(payload))
        openers.extend(parse_openers(payload))
    return splits, openers
