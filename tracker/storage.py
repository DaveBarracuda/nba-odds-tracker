"""Persistencia en SQLite: corridas, snapshots de odds, lesiones y alertas."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
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
CREATE INDEX IF NOT EXISTS ix_quotes_key ON quotes(event_id, book, market, outcome, run_ts);
-- corridas en que se vio cada partido: la historia se reconstruye rellenando hacia adelante
CREATE TABLE IF NOT EXISTS event_runs (
    run_ts TEXT NOT NULL, event_id TEXT NOT NULL, PRIMARY KEY (event_id, run_ts)
);
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
QuoteKey = tuple[str, str, str, str]  # (event_id, book, market, outcome)

# Borra filas idénticas a la anterior del mismo mercado/casa/lado (bases que guardaban todo)
COMPACT_SQL = """
DELETE FROM quotes WHERE rowid IN (
    SELECT rowid FROM (
        SELECT rowid, point, price, commence_time,
               LAG(run_ts) OVER w AS prev_ts, LAG(point) OVER w AS prev_point,
               LAG(price) OVER w AS prev_price, LAG(commence_time) OVER w AS prev_commence
        FROM quotes WINDOW w AS (PARTITION BY event_id, book, market, outcome ORDER BY run_ts)
    )
    WHERE prev_ts IS NOT NULL AND price IS prev_price AND point IS prev_point
      AND commence_time IS prev_commence
)
"""


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
        self._migrate_to_changes_only()

    def _migrate_to_changes_only(self) -> None:
        """Las bases viejas guardaban cada snapshot completo; se pasan a solo cambios (una vez)."""
        if self.conn.execute("SELECT 1 FROM event_runs LIMIT 1").fetchone():
            return
        if not self.conn.execute("SELECT 1 FROM quotes LIMIT 1").fetchone():
            return
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO event_runs SELECT DISTINCT run_ts, event_id FROM quotes")
            self.conn.execute(COMPACT_SQL)
        self.conn.execute("VACUUM")

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

    def _latest_rows(self, event_ids: set[str], until_ts: str | None = None) -> list[tuple]:
        """Última fila guardada de cada (partido, casa, mercado, lado), hasta until_ts."""
        if not event_ids:
            return []
        marks = ",".join("?" * len(event_ids))
        limit = "AND run_ts <= ?" if until_ts else ""
        params = [*event_ids, *([until_ts] if until_ts else [])]
        return self.conn.execute(
            f"SELECT q.event_id, q.commence_time, q.home, q.away, q.book, q.market, q.outcome, q.point, q.price "
            f"FROM quotes q JOIN (SELECT event_id, book, market, outcome, MAX(run_ts) AS last_ts FROM quotes "
            f"WHERE event_id IN ({marks}) {limit} GROUP BY event_id, book, market, outcome) l "
            f"ON q.event_id = l.event_id AND q.book = l.book AND q.market = l.market "
            f"AND q.outcome = l.outcome AND q.run_ts = l.last_ts", params).fetchall()

    def save_quotes(self, run_ts: str, quotes: Iterable[Quote]) -> int:
        """Guarda solo las cotizaciones que cambiaron; devuelve cuántas."""
        quotes = list(quotes)
        event_ids = {q.event_id for q in quotes}
        latest = {(r[0], r[4], r[5], r[6]): (r[7], r[8], r[1]) for r in self._latest_rows(event_ids)}
        changed = [q for q in quotes
                   if latest.get((q.event_id, q.book, q.market, q.outcome)) != (q.point, q.price, q.commence_time)]
        with self.conn:
            self.conn.executemany(
                "INSERT INTO quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(run_ts, q.event_id, q.commence_time, q.home, q.away, q.book, q.market,
                  q.outcome, q.point, q.price) for q in changed],
            )
            self.conn.executemany("INSERT OR IGNORE INTO event_runs VALUES (?, ?)",
                                  [(run_ts, event_id) for event_id in event_ids])
        return len(changed)

    def book_series(self, event_id: str, market: str) -> dict[str, list[LinePoint]]:
        """Historia de la línea por casa, rellenando hacia adelante entre cambios."""
        runs = [r[0] for r in self.conn.execute(
            "SELECT run_ts FROM event_runs WHERE event_id = ? ORDER BY run_ts", (event_id,))]
        changes: dict[str, list[tuple]] = defaultdict(list)
        for book, run_ts, outcome, point, price in self.conn.execute(
                "SELECT book, run_ts, outcome, point, price FROM quotes "
                "WHERE event_id = ? AND market = ? ORDER BY run_ts", (event_id, market)):
            changes[book].append((run_ts, outcome, point, price))
        return {book: _forward_fill(runs or sorted({c[0] for c in rows}), rows, market)
                for book, rows in changes.items()}

    def line_history(self, event_id: str, book: str, market: str) -> list[LinePoint]:
        return self.book_series(event_id, market).get(book, [])

    def current_quotes(self, run_ts: str) -> list[Quote]:
        """Cotización vigente en run_ts de cada casa, para los partidos vistos en esa corrida."""
        event_ids = {r[0] for r in self.conn.execute(
            "SELECT event_id FROM event_runs WHERE run_ts = ?", (run_ts,))}
        rows = sorted(self._latest_rows(event_ids, run_ts), key=lambda r: (r[1], r[0], r[4]))
        return [Quote(*row) for row in rows]

    def prune(self, cutoff: datetime, injuries_cutoff: datetime | None = None) -> int:
        """Borra partidos que empezaron antes de cutoff, splits viejos y lesiones antes de injuries_cutoff
        (solo se compara contra la corrida anterior, así que basta conservar poco)."""
        cutoff_z = cutoff.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cutoff_ts = cutoff.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        injuries_ts = (injuries_cutoff or cutoff).astimezone(timezone.utc).replace(microsecond=0).isoformat()
        old = [r[0] for r in self.conn.execute(
            "SELECT event_id FROM quotes GROUP BY event_id HAVING MAX(commence_time) < ?", (cutoff_z,))]
        deleted = 0
        with self.conn:
            for event_id in old:
                deleted += self.conn.execute("DELETE FROM quotes WHERE event_id = ?", (event_id,)).rowcount
                self.conn.execute("DELETE FROM event_runs WHERE event_id = ?", (event_id,))
            deleted += self.conn.execute("DELETE FROM splits WHERE run_ts < ?", (cutoff_ts,)).rowcount
            deleted += self.conn.execute("DELETE FROM injuries WHERE run_ts < ?", (injuries_ts,)).rowcount
        if deleted:
            self.conn.execute("VACUUM")
        return deleted

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
        (last_ts,) = self.conn.execute("SELECT MAX(run_ts) FROM event_runs").fetchone()
        return last_ts

    def recent_alerts(self, since_ts: str) -> list[tuple[str, str, str, str, str]]:
        """(run_ts, event_id, kind, severity, message) desde since_ts, más recientes primero."""
        return list(self.conn.execute(
            "SELECT run_ts, event_id, kind, severity, message FROM alerts WHERE run_ts >= ? "
            "ORDER BY run_ts DESC",
            (since_ts,)))


def _forward_fill(runs: list[str], changes: list[tuple], market: str) -> list[LinePoint]:
    """Valor de la línea en cada corrida aplicando los cambios guardados hasta esa corrida."""
    sides: dict[str, tuple[float | None, int]] = {}
    points: list[LinePoint] = []
    i = 0
    for run_ts in runs:
        while i < len(changes) and changes[i][0] <= run_ts:
            _, outcome, point, price = changes[i]
            sides[outcome] = (point, price)
            i += 1
        point = line_point(datetime.fromisoformat(run_ts), market, sides) if sides else None
        if point is not None:
            points.append(point)
    return points
