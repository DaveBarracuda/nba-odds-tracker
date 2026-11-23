import sqlite3
from datetime import datetime, timedelta, timezone

from tracker.dashboard import best_prices
from tracker.detectors import OFF_MARKET, detect_off_market
from tracker.lines import LinePoint, consensus_series
from tracker.odds_client import Quote
from tracker.storage import Store
from tracker.tracker import books_moved

T0 = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
START = "2026-11-24T00:30:00Z"


def spread(book, home_point, price=-110):
    return [Quote("ev1", START, "Boston Celtics", "Chicago Bulls", book, "spreads", "home", home_point, price),
            Quote("ev1", START, "Boston Celtics", "Chicago Bulls", book, "spreads", "away", -home_point, price)]


def ts(hour):
    return (T0 + timedelta(hours=hour)).isoformat()


def test_consensus_is_median_across_books():
    by_book = {b: [LinePoint(T0, v, -110)] for b, v in {"a": -5.5, "b": -6.0, "c": -6.5}.items()}
    assert consensus_series(by_book)[0].value == -6.0


def test_only_changes_are_stored_and_history_is_forward_filled(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    assert store.save_quotes(ts(0), spread("dk", -5.5) + spread("fd", -6.0)) == 4
    assert store.save_quotes(ts(1), spread("dk", -5.5) + spread("fd", -6.0)) == 0   # nada cambió
    assert store.save_quotes(ts(2), spread("dk", -6.5) + spread("fd", -6.0)) == 2   # solo DK
    series = store.book_series("ev1", "spreads")
    assert [p.value for p in series["dk"]] == [-5.5, -5.5, -6.5]
    assert [p.value for p in series["fd"]] == [-6.0, -6.0, -6.0]
    current = {(q.book, q.outcome): q.point for q in store.current_quotes(ts(2))}
    assert current[("dk", "home")] == -6.5 and current[("fd", "home")] == -6.0
    store.close()


def test_old_full_snapshot_db_is_compacted_without_losing_history(tmp_path):
    path = tmp_path / "old.sqlite3"
    Store(path).close()
    conn = sqlite3.connect(path)   # simula una base vieja: snapshots completos, sin event_runs
    for hour, point in enumerate([-5.5, -5.5, -5.5, -6.5]):
        for q in spread("dk", point):
            conn.execute("INSERT INTO quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (ts(hour), q.event_id, q.commence_time, q.home, q.away, q.book, q.market,
                          q.outcome, q.point, q.price))
    conn.commit()
    conn.close()
    store = Store(path)
    assert store.conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 4   # 8 filas -> 4
    assert [p.value for p in store.line_history("ev1", "dk", "spreads")] == [-5.5, -5.5, -5.5, -6.5]
    store.close()


def test_off_market_book_flags_value_side():
    current = {"dk": -6.5, "fd": -6.5, "mgm": -6.5, "czr": -6.5, "circa": -5.0}
    found = detect_off_market(current, "spreads")
    assert [(book, value) for book, value, _ in found] == [("circa", -5.0)]
    signal = found[0][2]
    assert signal.kind == OFF_MARKET and "valor en el local" in signal.detail


def test_off_market_needs_enough_books():
    assert detect_off_market({"dk": -6.5, "circa": -4.5}, "spreads") == []


def test_books_moved_counts_same_direction():
    t0, t1 = T0, T0 + timedelta(hours=1)
    by_book = {"a": [LinePoint(t0, -5.5, -110), LinePoint(t1, -6.5, -110)],
               "b": [LinePoint(t0, -5.5, -110), LinePoint(t1, -6.0, -110)],
               "c": [LinePoint(t0, -5.5, -110), LinePoint(t1, -5.5, -110)]}
    assert books_moved(by_book, consensus_series(by_book)) == (2, 3)


def test_best_prices_per_side():
    by_book = {
        "dk": {"spreads": {"home": (-6.5, -110), "away": (6.5, -110)},
               "totals": {"over": (230.5, -110), "under": (230.5, -110)},
               "h2h": {"home": (None, -250), "away": (None, 205)}},
        "fd": {"spreads": {"home": (-6.0, -112), "away": (6.0, -108)},
               "totals": {"over": (231.5, -110), "under": (231.5, -105)},
               "h2h": {"home": (None, -240), "away": (None, 200)}},
    }
    best = best_prices(by_book)
    assert best[("spreads", "home")] == {"fd"}      # Celtics -6.0 da menos puntos que -6.5
    assert best[("spreads", "away")] == {"dk"}      # Bulls +6.5
    assert best[("totals", "over")] == {"dk"}       # over más bajo
    assert best[("totals", "under")] == {"fd"}      # under más alto
    assert best[("h2h", "home")] == {"fd"} and best[("h2h", "away")] == {"dk"}


def test_prune_drops_old_games(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.save_quotes(ts(0), spread("dk", -5.5))
    assert store.prune(datetime(2026, 12, 31, tzinfo=timezone.utc)) > 0
    assert store.book_series("ev1", "spreads") == {}
    store.close()
