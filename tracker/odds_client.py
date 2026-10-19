"""Cliente de The Odds API v4 para NBA."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

import requests

BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"
TIMEOUT_S = 20


class OddsApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class Usage:
    remaining: int | None
    used: int | None
    last: int | None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "Usage":
        def num(name: str) -> int | None:
            value = headers.get(name)
            return int(float(value)) if value not in (None, "") else None

        return cls(num("x-requests-remaining"), num("x-requests-used"), num("x-requests-last"))


@dataclass(frozen=True)
class Quote:
    event_id: str
    commence_time: str
    home: str
    away: str
    book: str
    market: str
    outcome: str  # home / away / over / under
    point: float | None
    price: int


def _get(path: str, params: dict[str, str]) -> tuple[Any, Usage]:
    try:
        resp = requests.get(f"{BASE_URL}/{path}", params=params, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        # No propagar el mensaje original: incluye la URL con la apiKey
        raise OddsApiError(f"Error de red consultando {path}: {type(exc).__name__}") from None
    if resp.status_code != 200:
        raise OddsApiError(f"HTTP {resp.status_code} en {path}: {resp.text[:200]}")
    return resp.json(), Usage.from_headers(resp.headers)


def fetch_events(api_key: str) -> list[dict[str, Any]]:
    """Lista de partidos próximos. Este endpoint NO consume créditos."""
    data, _ = _get("events", {"apiKey": api_key})
    return data


def fetch_odds(api_key: str, bookmakers: str, markets: str, now: datetime) -> tuple[list[Quote], Usage]:
    params = {
        "apiKey": api_key,
        "bookmakers": bookmakers,
        "markets": markets,
        "oddsFormat": "american",
        "dateFormat": "iso",
        "commenceTimeFrom": now.strftime("%Y-%m-%dT%H:%M:%SZ"),  # solo pre-partido
    }
    data, usage = _get("odds", params)
    return parse_odds(data), usage


def _side(market: str, name: str, home: str, away: str) -> str | None:
    if market == "totals":
        lowered = name.lower()
        return lowered if lowered in ("over", "under") else None
    if name == home:
        return "home"
    if name == away:
        return "away"
    return None


def parse_odds(events: list[dict[str, Any]]) -> list[Quote]:
    quotes: list[Quote] = []
    for ev in events:
        home, away = ev["home_team"], ev["away_team"]
        for book in ev.get("bookmakers", []):
            for market in book.get("markets", []):
                for outcome in market.get("outcomes", []):
                    side = _side(market["key"], outcome["name"], home, away)
                    if side is None:
                        continue
                    point = outcome.get("point")
                    quotes.append(Quote(
                        event_id=ev["id"],
                        commence_time=ev["commence_time"],
                        home=home,
                        away=away,
                        book=book["key"],
                        market=market["key"],
                        outcome=side,
                        point=float(point) if point is not None else None,
                        price=int(round(float(outcome["price"]))),
                    ))
    return quotes
