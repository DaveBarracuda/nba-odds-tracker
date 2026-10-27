"""Llave de un mercado de un partido: equipos + mercado + fecha ET.

El mismo cruce se repite en la temporada (p.ej. CHI @ BOS el 26 y el 30 de octubre),
así que la fecha es parte de la llave.
"""
from __future__ import annotations

from datetime import datetime
from typing import Mapping, TypeVar

from .schedule import ET

GameKey = tuple[str, str, str, str]  # (away, home, market, fecha ET 'YYYY-MM-DD' o '' = cualquier fecha)
V = TypeVar("V")


def game_day(ts_iso: str) -> str:
    """Fecha ET del partido a partir de su hora de inicio ISO."""
    return datetime.fromisoformat(ts_iso).astimezone(ET).date().isoformat()


def make_key(away: str, home: str, market: str, day: str = "") -> GameKey:
    return away.strip().lower(), home.strip().lower(), market.strip().lower(), day


def lookup(table: Mapping[GameKey, V], away: str, home: str, market: str, day: str) -> V | None:
    """Valor del partido de esa fecha; si no hay, uno cargado sin fecha (CSV manual)."""
    value = table.get(make_key(away, home, market, day))
    return value if value is not None else table.get(make_key(away, home, market))
