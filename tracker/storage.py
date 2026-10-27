"""Persistencia en SQLite: corridas, snapshots de odds, lesiones y alertas."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, NamedTuple

from .action_network import Opener, Split
from .game_keys import GameKey, game_day, make_key
from .lines import LinePoint, line_point
from .odds_client import Quote, Usage

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_ts TEXT PRIMARY KEY, status TEXT NOT NULL, n_events INTEGER,
    credits_last INTEGER, credits_used INTEGER, credits_remaining INTEGER
);
CREATE TABLE IF NOT EXISTS quotes (
    run_ts TEXT NOT NULL, event_id TEXT NOT NULL, commence_time TEXT, home TEXT, away TEXT,
    book TEXT NOT NULL, market TEXT NOT NULL, outcome TEXT NOT NULL, point REAL, price INTEGER
);
CREATE INDEX IF NOT EXISTS ix_quotes_line ON quotes(event_id, book, market, run_ts);
CREATE TABLE IF NOT EXISTS injuries (run_ts TEXT NOT NULL, team TEXT, player TEXT, status TEXT);
CREATE INDEX IF NOT EXISTS ix_injuries_ts ON injuries(run_ts);
CREATE TABLE IF NOT EXISTS splits (
    run_ts TEXT NOT NULL, away TEXT, home TEXT, market TEXT,
    tickets_pct REAL, money_pct REAL, num_bets INTEGER
);
CREATE INDEX IF NOT EXISTS ix_splits_ts ON splits(run_ts);
CREATE TABLE IF NOT EXISTS openers (
    away TEXT, home TEXT, market TEXT, game_day TEXT, value REAL,
    PRIMARY KEY (away, home, market, game_day)
);
CREATE TABLE IF NOT EXISTS alerts (
    signature TEXT PRIMARY KEY, run_ts TEXT, kind TEXT, severity TEXT,
    event_id TEXT, book TEXT, market TEXT, message TEXT
);
"""

InjuryKey = tuple[str, str, str]  # (equipo, jugador, estatus)


class RunRow(NamedTuple):
    run_ts: str
    status: str
    n_events: int | None
    credits_last: int | None
    credits_used: int | None
    credits_remaining: int | None


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self._add_column_if_missing("splits", "game_day", "TEXT")

    def _add_column_if_missing(self, table: str, column: str, sql_type: str) -> None:
        """Migración para bases creadas con versiones anteriores del esquema."""
        columns = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            with self.conn:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")

    def close(self) -> None:
        self.conn.close()

    def record_run(self, run_ts: str, status: str, n_events: int = 0, usage: Usage | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?, ?, ?)",
                (run_ts, status, n_events,
                 usage.last if usage else None,
                 usage.used if usage else None,
                 usage.remaining if usage else None),
            )

    def last_remaining(self) -> int | None:
        row = self.conn.execute(
            "SELECT credits_remaining FROM runs WHERE credits_remaining IS NOT NULL "
            "ORDER BY run_ts DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def last_paid_run(self) -> datetime | None:
        (last_ts,) = self.conn.execute(
            "SELECT MAX(run_ts) FROM runs WHERE credits_last IS NOT NULL").fetchone()
        return datetime.fromisoformat(last_ts) if last_ts else None

    def commence_times_since(self, since: datetime) -> list[str]:
        """Horarios de inicio (ISO) de partidos vistos que empiezan desde `since`."""
        since_iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self.conn.execute(
            "SELECT DISTINCT commence_time FROM quotes WHERE commence_time >= ?", (since_iso,))
        return [row[0] for row in rows]

    def runs(self) -> list[RunRow]:
        return [RunRow(*row) for row in self.conn.execute("SELECT * FROM runs ORDER BY run_ts")]

    def save_quotes(self, run_ts: str, quotes: Iterable[Quote]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(run_ts, q.event_id, q.commence_time, q.home, q.away, q.book, q.market,
                  q.outcome, q.point, q.price) for q in quotes],
            )

    def line_history(self, event_id: str, book: str, market: str) -> list[LinePoint]:
        rows = self.conn.execute(
            "SELECT run_ts, outcome, point, price FROM quotes "
            "WHERE event_id = ? AND book = ? AND market = ? ORDER BY run_ts",
            (event_id, book, market),
        )
        grouped: dict[str, dict[str, tuple[float | None, int]]] = {}
        for run_ts, outcome, point, price in rows:
            grouped.setdefault(run_ts, {})[outcome] = (point, price)
        points = (line_point(datetime.fromisoformat(ts), market, sides) for ts, sides in grouped.items())
        return [p for p in points if p is not None]

    def save_injuries(self, run_ts: str, injuries: Iterable[InjuryKey]) -> None:
        with self.conn:
            self.conn.executemany("INSERT INTO injuries VALUES (?, ?, ?, ?)",
                                  [(run_ts, *key) for key in injuries])

    def previous_injuries(self, before_ts: str) -> set[InjuryKey] | None:
        """Reporte de lesiones de la corrida anterior; None si no existe."""
        (prev_ts,) = self.conn.execute(
            "SELECT MAX(run_ts) FROM injuries WHERE run_ts < ?", (before_ts,)).fetchone()
        if prev_ts is None:
            return None
        rows = self.conn.execute("SELECT team, player, status FROM injuries WHERE run_ts = ?", (prev_ts,))
        return {tuple(row) for row in rows}

    def add_alert(self, signature: str, run_ts: str, kind: str, severity: str,
                  event_id: str, book: str, market: str, message: str) -> bool:
        """Guarda la alerta; devuelve False si ya se había emitido (dedupe)."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO alerts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (signature, run_ts, kind, severity, event_id, book, market, message),
            )
        return cur.rowcount == 1

    def save_splits(self, run_ts: str, splits: Iterable[Split]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO splits (run_ts, away, home, market, tickets_pct, money_pct, num_bets, game_day) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(run_ts, s.away, s.home, s.market, s.tickets_pct, s.money_pct, s.num_bets,
                  game_day(s.start_time) if s.start_time else "") for s in splits],
            )

    def latest_splits(self) -> dict[GameKey, Split]:
        (last_ts,) = self.conn.execute("SELECT MAX(run_ts) FROM splits").fetchone()
        if last_ts is None:
            return {}
        rows = self.conn.execute(
            "SELECT away, home, market, tickets_pct, money_pct, num_bets, COALESCE(game_day, '') "
            "FROM splits WHERE run_ts = ?", (last_ts,))
        return {make_key(a, h, m, d): Split(a, h, "", m, t, mo, n) for a, h, m, t, mo, n, d in rows}

    def save_openers(self, openers: Iterable[Opener]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO openers VALUES (?, ?, ?, ?, ?)",
                [(o.away, o.home, o.market, game_day(o.start_time), o.value) for o in openers if o.start_time],
            )

    def openers(self) -> dict[GameKey, float]:
        rows = self.conn.execute("SELECT away, home, market, game_day, value FROM openers")
        return {make_key(a, h, m, d): v for a, h, m, d, v in rows}

    def latest_quotes_run(self) -> str | None:
        (last_ts,) = self.conn.execute("SELECT MAX(run_ts) FROM quotes").fetchone()
        return last_ts

    def quotes_for_run(self, run_ts: str) -> list[Quote]:
        rows = self.conn.execute(
            "SELECT event_id, commence_time, home, away, book, market, outcome, point, price "
            "FROM quotes WHERE run_ts = ? ORDER BY commence_time, event_id", (run_ts,))
        return [Quote(*row) for row in rows]

    def recent_alerts(self, since_ts: str) -> list[tuple[str, str, str, str, str]]:
        """(run_ts, event_id, kind, severity, message) desde since_ts, más recientes primero."""
        return list(self.conn.execute(
            "SELECT run_ts, event_id, kind, severity, message FROM alerts WHERE run_ts >= ? "
            "ORDER BY run_ts DESC",
            (since_ts,)))
