"""Convierte cotizaciones de un snapshot en un valor de línea comparable."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class LinePoint:
    ts: datetime
    value: float  # spreads: puntos del local | totals: total | h2h: prob. sin vig del local (%)
    price: int    # precio americano del local / over


def implied_prob(american: int) -> float:
    if american >= 100:
        return 100 / (american + 100)
    return -american / (-american + 100)


def line_point(ts: datetime, market: str, sides: dict[str, tuple[float | None, int]]) -> LinePoint | None:
    if market == "spreads":
        home = sides.get("home")
        if home is None or home[0] is None:
            return None
        return LinePoint(ts, home[0], home[1])
    if market == "totals":
        over = sides.get("over")
        if over is None or over[0] is None:
            return None
        return LinePoint(ts, over[0], over[1])
    if market == "h2h":
        home, away = sides.get("home"), sides.get("away")
        if home is None or away is None:
            return None
        p_home, p_away = implied_prob(home[1]), implied_prob(away[1])
        return LinePoint(ts, round(100 * p_home / (p_home + p_away), 2), home[1])
    return None


def format_value(market: str, value: float) -> str:
    if market == "spreads":
        return f"{value:+g}"
    if market == "totals":
        return f"{value:g}"
    return f"{value:.1f}%"
