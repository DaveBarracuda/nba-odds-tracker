"""Configuración leída de variables de entorno / archivo .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

DEFAULT_BOOKMAKERS = "draftkings"
DEFAULT_MARKETS = "h2h,spreads,totals"
DEFAULT_LOOKAHEAD_HOURS = 48
DEFAULT_MIN_CREDITS = 20
VALID_MARKETS = {"h2h", "spreads", "totals"}


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Carga KEY=VALUE de .env sin pisar variables ya definidas."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    api_key: str
    bookmakers: str
    markets: str
    lookahead_hours: int
    min_credits: int
    active_hours: tuple[int, int] | None
    telegram_token: str | None
    telegram_chat_id: str | None
    db_path: Path
    public_betting_csv: Path
    alerts_log: Path
    tracker_log: Path
    dashboard_html: Path

    @property
    def credits_per_call(self) -> int:
        # The Odds API: costo = mercados x regiones (cada 10 bookmakers = 1 región)
        books = len([b for b in self.bookmakers.split(",") if b.strip()])
        return len(self.markets.split(",")) * max(1, -(-books // 10))


def _parse_active_hours(raw: str) -> tuple[int, int] | None:
    """'8-23' -> (8, 23): corre solo si 8 <= hora local < 23. Vacío = 24h."""
    if not raw.strip():
        return None
    try:
        start, end = (int(part) for part in raw.split("-", 1))
    except ValueError as exc:
        raise SystemExit(f"ACTIVE_HOURS inválido '{raw}', usa formato 8-23") from exc
    if not (0 <= start < end <= 24):
        raise SystemExit(f"ACTIVE_HOURS fuera de rango: '{raw}'")
    return start, end


def _parse_markets(raw: str) -> str:
    markets = [m.strip() for m in raw.split(",") if m.strip()]
    invalid = set(markets) - VALID_MARKETS
    if not markets or invalid:
        raise SystemExit(f"MARKETS inválido: {raw}. Opciones: {sorted(VALID_MARKETS)}")
    return ",".join(markets)


def get_settings() -> Settings:
    load_dotenv()
    api_key = os.environ.get("ODDS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Falta ODDS_API_KEY (defínela en .env)")
    return Settings(
        api_key=api_key,
        bookmakers=os.environ.get("BOOKMAKERS", DEFAULT_BOOKMAKERS).strip(),
        markets=_parse_markets(os.environ.get("MARKETS", DEFAULT_MARKETS)),
        lookahead_hours=int(os.environ.get("LOOKAHEAD_HOURS", DEFAULT_LOOKAHEAD_HOURS)),
        min_credits=int(os.environ.get("MIN_CREDITS_REMAINING", DEFAULT_MIN_CREDITS)),
        active_hours=_parse_active_hours(os.environ.get("ACTIVE_HOURS", "")),
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID") or None,
        db_path=DATA_DIR / "odds.sqlite3",
        public_betting_csv=DATA_DIR / "public_betting.csv",
        alerts_log=DATA_DIR / "alerts.log",
        tracker_log=DATA_DIR / "tracker.log",
        dashboard_html=DATA_DIR / "dashboard.html",
    )
