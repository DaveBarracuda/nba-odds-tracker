"""Construcción y envío de alertas (consola/log, archivo y Telegram opcional)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

import requests

from .detectors import HIGH, LOW, MEDIUM, RLM, Signal

log = logging.getLogger(__name__)

KIND_NAMES = {
    "RLM": "Movimiento de Línea Inverso (RLM)",
    "STEAM": "Steam Move",
    "GRADUAL": "Movimiento Gradual",
    "INFO": "Movimiento por Información",
    "NOISE": "Ruido de mercado",
}
MARKET_NAMES = {"spreads": "Spread", "totals": "Total", "h2h": "Moneyline"}
SEVERITY_RANK = {LOW: 0, MEDIUM: 1, HIGH: 2}
SEVERITY_LABEL = {HIGH: "ALTA", MEDIUM: "MEDIA", LOW: "BAJA"}
TELEGRAM_MIN_SEVERITY = MEDIUM  # el ruido solo se registra, no se notifica
TELEGRAM_TIMEOUT_S = 15


@dataclass(frozen=True)
class Alert:
    run_ts: str
    event_id: str
    matchup: str
    commence: datetime
    book: str
    market: str
    value: float
    signal: Signal

    @property
    def signature(self) -> str:
        # RLM se re-alerta solo si la línea cambia; el resto, una vez por corrida
        anchor = f"{self.value:.2f}" if self.signal.kind == RLM else self.run_ts
        return "|".join((self.event_id, self.book, self.market, self.signal.kind, anchor))

    def text(self) -> str:
        start = self.commence.astimezone().strftime("%d/%m %H:%M")
        return (f"[{SEVERITY_LABEL[self.signal.severity]}] {KIND_NAMES[self.signal.kind]} | "
                f"{self.matchup} ({start}) | {self.book} {MARKET_NAMES.get(self.market, self.market)} | "
                f"{self.signal.detail}")


def _send_telegram(token: str, chat_id: str, lines: Sequence[str]) -> None:
    try:
        resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                             json={"chat_id": chat_id, "text": "\n\n".join(lines)},
                             timeout=TELEGRAM_TIMEOUT_S)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("No se pudo enviar a Telegram: %s", type(exc).__name__)


def dispatch(alerts: Sequence[Alert], alerts_log: Path,
             telegram_token: str | None = None, telegram_chat_id: str | None = None) -> None:
    ordered = sorted(alerts, key=lambda a: SEVERITY_RANK[a.signal.severity], reverse=True)
    if not ordered:
        log.info("Sin alertas nuevas.")
        return
    lines = [a.text() for a in ordered]
    for line in lines:
        log.info(line)
    alerts_log.parent.mkdir(parents=True, exist_ok=True)
    with alerts_log.open("a", encoding="utf-8") as fh:
        fh.writelines(f"{ordered[0].run_ts} {line}\n" for line in lines)
    if telegram_token and telegram_chat_id:
        push = [a.text() for a in ordered
                if SEVERITY_RANK[a.signal.severity] >= SEVERITY_RANK[TELEGRAM_MIN_SEVERITY]]
        if push:
            _send_telegram(telegram_token, telegram_chat_id, push)
