"""Frecuencia de snapshots de pago.

Cada hora; desde 2 h antes del primer juego del día (fecha ET) hasta que
arranca el último juego de ese día, cada 30 min. El cron externo dispara cada
30 min y esta lógica decide si toca gastar créditos.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PRE_GAME_WINDOW = timedelta(hours=2)
BASE_INTERVAL = timedelta(hours=1)
DENSE_INTERVAL = timedelta(minutes=30)
CRON_SLACK = timedelta(minutes=10)  # tolera retrasos del cron de GitHub Actions
TICK = timedelta(minutes=30)


def is_dense(starts: Iterable[datetime], now: datetime) -> bool:
    """True si estamos en la ventana de 30 min del slate de hoy (ET)."""
    today = now.astimezone(ET).date()
    slate = [s for s in starts if s.astimezone(ET).date() == today]
    return bool(slate) and min(slate) - PRE_GAME_WINDOW <= now < max(slate)


def snapshot_due(last_paid: datetime | None, now: datetime, dense: bool) -> bool:
    if last_paid is None:
        return True
    interval = DENSE_INTERVAL if dense else BASE_INTERVAL
    return now - last_paid >= interval - CRON_SLACK


def estimate_paid_runs(starts: Iterable[datetime], begin: datetime, end: datetime) -> int:
    """Simula el cron cada 30 min entre begin y end y cuenta snapshots de pago."""
    starts = list(starts)
    tick = begin.replace(minute=0 if begin.minute < 30 else 30, second=0, microsecond=0)
    last_paid: datetime | None = None
    paid = 0
    while tick < end:
        if snapshot_due(last_paid, tick, is_dense(starts, tick)):
            paid += 1
            last_paid = tick
        tick += TICK
    return paid
