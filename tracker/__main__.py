"""CLI: python -m tracker [run|loop|report]"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from .config import get_settings
from .storage import Store
from .tracker import report, run_once
from .dashboard import build_dashboard

log = logging.getLogger("tracker")
LOOP_START_SECOND = 5  # segundos después de la hora en punto


def _setup_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(path, encoding="utf-8")]
    if sys.stdout is not None:  # pythonw (tarea programada) no tiene consola
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=handlers)


def _sleep_until_next_hour() -> None:
    now = datetime.now()
    nxt = (now + timedelta(hours=1)).replace(minute=0, second=LOOP_START_SECOND, microsecond=0)
    time.sleep((nxt - now).total_seconds())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracker", description="Tracker de movimiento de odds NBA")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "loop", "report", "dashboard"],
                        help="run: una corrida | loop: corre cada hora en primer plano | report: consumo | dashboard: regenera el HTML")
    args = parser.parse_args(argv)
    settings = get_settings()
    _setup_logging(settings.tracker_log)

    if args.command == "dashboard":
        store = Store(settings.db_path)
        try:
            log.info("Dashboard: %s", build_dashboard(store, settings))
        finally:
            store.close()
        return 0
    if args.command == "report":
        report(settings)
        return 0
    if args.command == "loop":
        while True:
            try:
                run_once(settings)
            except Exception:  # el loop debe sobrevivir a fallos puntuales
                log.exception("Fallo inesperado en la corrida")
            _sleep_until_next_hour()
    try:
        run_once(settings)
    except Exception:
        log.exception("Fallo inesperado en la corrida")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
