"""% de tickets del público, necesario para detectar RLM.

The Odds API no publica porcentajes de apuestas; se cargan desde un CSV
(manual o de otra fuente) con columnas: away,home,market,pct[,date]
pct = % de tickets en el LOCAL (spreads/h2h) o en el OVER (totals).
date (opcional, YYYY-MM-DD en hora ET) distingue cruces repetidos; sin fecha aplica a cualquiera.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from .config import VALID_MARKETS
from .game_keys import GameKey, make_key

log = logging.getLogger(__name__)

PublicKey = GameKey


def load_public_pct(path: Path) -> dict[PublicKey, float]:
    if not path.exists():
        return {}
    data: dict[PublicKey, float] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for line_no, row in enumerate(csv.DictReader(fh), start=2):
            try:
                key = make_key(row["away"], row["home"], row["market"], (row.get("date") or "").strip())
                pct = float(row["pct"])
            except (KeyError, TypeError, ValueError, AttributeError):
                log.warning("public_betting.csv línea %d inválida, se omite: %s", line_no, row)
                continue
            if key[2] not in VALID_MARKETS or not 0 <= pct <= 100:
                log.warning("public_betting.csv línea %d fuera de rango, se omite: %s", line_no, row)
                continue
            data[key] = pct
    return data
