"""Clasificación de movimientos de línea a partir de la serie de snapshots.

Unidades: spreads y totals en puntos; h2h en puntos porcentuales de
probabilidad implícita sin vig del local.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import median
from typing import Mapping, Sequence

from .config import book_name
from .lines import LinePoint, format_value

RLM = "RLM"
STEAM = "STEAM"
GRADUAL = "GRADUAL"
INFO = "INFO"
NOISE = "NOISE"
OFF_MARKET = "OFF_MARKET"

HIGH, MEDIUM, LOW = "high", "medium", "low"


@dataclass(frozen=True)
class Thresholds:
    noise: float          # movimiento máximo en un intervalo considerado ruido
    steam: float          # salto mínimo en un intervalo para steam
    info: float           # salto que se trata como informativo aunque no se detecte noticia
    gradual_total: float  # acumulado mínimo de una tendencia gradual
    rlm_min_move: float   # movimiento mínimo contra el lado público para RLM


THRESHOLDS = {
    "spreads": Thresholds(noise=0.5, steam=1.0, info=2.0, gradual_total=1.0, rlm_min_move=0.5),
    "totals": Thresholds(noise=0.5, steam=1.5, info=3.0, gradual_total=1.5, rlm_min_move=1.0),
    "h2h": Thresholds(noise=1.0, steam=3.0, info=6.0, gradual_total=3.0, rlm_min_move=1.5),
}

# Dirección en que se mueve el valor cuando el primer lado (local / over) se encarece
EXPENSIVE_DIRECTION = {"spreads": -1, "totals": 1, "h2h": 1}
SIDE_NAMES = {
    "spreads": {1: "el local", -1: "el visitante"},
    "totals": {1: "el over", -1: "el under"},
    "h2h": {1: "el local", -1: "el visitante"},
}

MAX_JUMP_GAP_HOURS = 2.5   # más tiempo entre snapshots ya no es un movimiento "brusco"
GRADUAL_WINDOW_STEPS = 8   # intervalos hacia atrás para buscar una tendencia
GRADUAL_MIN_STEPS = 3      # movimientos mínimos en la misma dirección
RLM_PUBLIC_MIN_PCT = 60.0  # % de tickets para considerar un lado "público"
# Una casa se separa del consenso al menos esto: precio de valor en un lado
OFF_MARKET_MIN = {"spreads": 1.0, "totals": 1.5, "h2h": 3.0}
MIN_BOOKS_FOR_CONSENSUS = 4
# Por debajo de esto un cambio del consenso (mediana de varias casas) no se reporta ni como ruido
NOISE_FLOOR = {"spreads": 0.25, "totals": 0.25, "h2h": 0.5}
JUICE_NOISE_MIN = 10  # centavos
EPS = 1e-9


@dataclass(frozen=True)
class Signal:
    kind: str
    severity: str
    detail: str


def _deltas(series: Sequence[LinePoint]) -> list[float]:
    return [b.value - a.value for a, b in zip(series, series[1:])]


def detect_jump(series: Sequence[LinePoint], market: str, th: Thresholds,
                injury_news: Sequence[str]) -> Signal | None:
    """Steam o información: salto grande entre dos snapshots consecutivos."""
    prev, last = series[-2], series[-1]
    delta = last.value - prev.value
    gap_h = (last.ts - prev.ts).total_seconds() / 3600
    if abs(delta) < th.steam - EPS or gap_h > MAX_JUMP_GAP_HOURS:
        return None
    move = f"{format_value(market, prev.value)} -> {format_value(market, last.value)} en {gap_h:.1f}h"
    if injury_news:
        return Signal(INFO, HIGH, f"{move}; noticias: {'; '.join(injury_news)}")
    if abs(delta) >= th.info - EPS:
        return Signal(INFO, HIGH, f"{move}; salto grande sin noticia detectada (revisar lesiones/descansos)")
    return Signal(STEAM, HIGH, f"{move}; movimiento brusco sin noticia pública detectada")


def detect_gradual(series: Sequence[LinePoint], market: str, th: Thresholds) -> Signal | None:
    """Varios movimientos pequeños en la misma dirección (se permiten pausas)."""
    deltas = _deltas(series)[-GRADUAL_WINDOW_STEPS:]
    if not deltas or abs(deltas[-1]) < EPS:
        return None
    direction = 1 if deltas[-1] > 0 else -1
    steps: list[float] = []
    for delta in reversed(deltas):
        if abs(delta) < EPS:
            continue
        if delta * direction < 0:
            break
        steps.append(delta)
    total = sum(steps)
    if len(steps) < GRADUAL_MIN_STEPS or abs(total) < th.gradual_total - EPS:
        return None
    if any(abs(step) >= th.steam - EPS for step in steps):
        return None
    last = series[-1]
    start = last.value - total
    return Signal(GRADUAL, MEDIUM, f"{format_value(market, start)} -> {format_value(market, last.value)} "
                                   f"en {len(steps)} movimientos consecutivos")


def detect_noise(series: Sequence[LinePoint], market: str, th: Thresholds) -> Signal | None:
    """Cambios de juice, ajustes menores o rebotes que se revierten."""
    prev, last = series[-2], series[-1]
    delta = last.value - prev.value
    if abs(delta) < NOISE_FLOOR.get(market, EPS) - EPS:
        if abs(last.price - prev.price) >= JUICE_NOISE_MIN:
            return Signal(NOISE, LOW, f"solo cambio de juice {prev.price:+d} -> {last.price:+d}")
        return None
    if len(series) >= 3 and abs(last.value - series[-3].value) < EPS:
        values = " -> ".join(format_value(market, p.value) for p in series[-3:])
        return Signal(NOISE, LOW, f"rebote {values}")
    if abs(delta) <= th.noise + EPS:
        return Signal(NOISE, LOW, f"ajuste menor {format_value(market, prev.value)} -> "
                                  f"{format_value(market, last.value)}")
    return None


def detect_rlm(series: Sequence[LinePoint], market: str, th: Thresholds,
               public_pct: float | None, open_value: float | None = None) -> Signal | None:
    """La línea se mueve en contra del lado con mayoría de tickets.

    public_pct = % de tickets en el local (spreads/h2h) o en el over (totals).
    open_value = apertura del mercado; si falta se usa el primer snapshot.
    """
    if public_pct is None:
        return None
    if public_pct >= RLM_PUBLIC_MIN_PCT:
        side_sign, side_pct = 1, public_pct
    elif public_pct <= 100 - RLM_PUBLIC_MIN_PCT:
        side_sign, side_pct = -1, 100 - public_pct
    else:
        return None
    opening = open_value if open_value is not None else series[0].value
    last = series[-1]
    adverse = -EXPENSIVE_DIRECTION[market] * side_sign * (last.value - opening)
    if adverse < th.rlm_min_move - EPS:
        return None
    side = SIDE_NAMES[market][side_sign]
    return Signal(RLM, HIGH, f"{side_pct:.0f}% de tickets en {side} pero la línea se movió en contra: "
                             f"apertura {format_value(market, opening)} -> {format_value(market, last.value)}")


def detect_off_market(current: Mapping[str, float], market: str) -> list[tuple[str, float, Signal]]:
    """Casas cuya línea actual se separa del consenso: (casa, valor, señal)."""
    if market not in OFF_MARKET_MIN or len(current) < MIN_BOOKS_FOR_CONSENSUS:
        return []
    consensus = median(current.values())
    found = []
    for book, value in sorted(current.items()):
        diff = value - consensus
        if abs(diff) < OFF_MARKET_MIN[market] - EPS:
            continue
        # spreads: más puntos al local favorecen al local; totals: total bajo favorece al over;
        # h2h: prob. del local más baja = mejor precio para el local
        first_side = diff > 0 if market == "spreads" else diff < 0
        side = SIDE_NAMES[market][1 if first_side else -1]
        found.append((book, value, Signal(OFF_MARKET, MEDIUM,
                                          f"{book_name(book)} {format_value(market, value)} vs consenso "
                                          f"{format_value(market, consensus)} ({diff:+.1f}): valor en {side}")))
    return found


def analyze(series: Sequence[LinePoint], market: str, injury_news: Sequence[str] = (),
            public_pct: float | None = None, open_value: float | None = None,
            books_moved: tuple[int, int] | None = None) -> list[Signal]:
    th = THRESHOLDS.get(market)
    if th is None or len(series) < 2:
        return []
    movement = (detect_jump(series, market, th, injury_news)
                or detect_gradual(series, market, th)
                or detect_noise(series, market, th))
    if movement is not None and movement.kind in (STEAM, INFO) and books_moved and books_moved[1] > 1:
        movement = replace(movement, detail=f"{movement.detail}; {books_moved[0]}/{books_moved[1]} casas "
                                            "se movieron en la misma dirección")
    rlm = detect_rlm(series, market, th, public_pct, open_value)
    return [signal for signal in (movement, rlm) if signal is not None]
