import sqlite3
from datetime import datetime, timedelta, timezone

from tracker.action_network import Opener, parse_openers
from tracker.dashboard import describe_move, line_label
from tracker.detectors import RLM, analyze
from tracker.game_keys import lookup
from tracker.lines import LinePoint
from tracker.storage import Store


def side(name, value, odds):
    return {"side": name, "value": value, "odds": odds}


AN_OPEN_PAYLOAD = {"games": [{
    "start_time": "2026-10-26T23:30:00.000Z", "home_team_id": 1, "away_team_id": 2, "num_bets": 100,
    "teams": [{"id": 1, "full_name": "Boston Celtics"}, {"id": 2, "full_name": "Chicago Bulls"}],
    "markets": {"30": {"event": {
        "spread": [side("away", 13.5, -110), side("home", -13.5, -110)],
        "total": [side("over", 232.5, -110), side("under", 232.5, -110)],
        "moneyline": [side("home", 0, -900), side("away", 0, 600)],
    }}},
}]}


def test_parse_openers_reads_market_open_book():
    openers = {o.market: o.value for o in parse_openers(AN_OPEN_PAYLOAD)}
    assert openers["spreads"] == -13.5
    assert openers["totals"] == 232.5
    assert 85 < openers["h2h"] < 90


# Casos reales del 26/10: apertura del mercado -> cierre DraftKings
def test_favorite_line_growing_reads_as_sube():
    text, verdict = describe_move("spreads", -13.5, -14.5, "Boston Celtics", "Chicago Bulls")
    assert text == "Celtics -13.5 → Celtics -14.5" and verdict == "sube 1.0"


def test_favorite_line_shrinking_reads_as_baja():
    assert describe_move("spreads", -5.5, -4.5, "Houston Rockets", "Atlanta Hawks")[1] == "baja 1.0"


def test_away_favorite_and_flip():
    assert describe_move("spreads", 3.5, 4.5, "Utah Jazz", "Denver Nuggets") == (
        "Nuggets -3.5 → Nuggets -4.5", "sube 1.0")
    assert describe_move("spreads", -1.5, 1.5, "Atlanta Hawks", "Houston Rockets")[1] == "cambio de favorito"


def test_totals_verdict():
    assert describe_move("totals", 218.5, 221.5, "a", "b") == ("218.5 → 221.5", "sube 3.0")


def series(values):
    t0 = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    return [LinePoint(t0 + timedelta(hours=i), v, -110) for i, v in enumerate(values)]


def test_rlm_measured_against_market_open_not_first_snapshot():
    # DK abrió OKC -9.5 pero el mercado abrió -8.5: cerrar en -8.5 no es movimiento en contra del público
    line = series([-9.5, -8.5])
    assert RLM in [s.kind for s in analyze(line, "spreads", public_pct=70)]
    assert RLM not in [s.kind for s in analyze(line, "spreads", public_pct=70, open_value=-8.5)]


def test_openers_keyed_by_game_date(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.save_openers([Opener("Chicago Bulls", "Boston Celtics", "2026-10-26T23:30:00Z", "spreads", -13.5),
                        Opener("Chicago Bulls", "Boston Celtics", "2026-10-30T23:30:00Z", "spreads", -11.5)])
    openers = store.openers()
    store.close()
    assert lookup(openers, "Chicago Bulls", "Boston Celtics", "spreads", "2026-10-26") == -13.5
    assert lookup(openers, "Chicago Bulls", "Boston Celtics", "spreads", "2026-10-30") == -11.5


def test_old_splits_table_is_migrated(tmp_path):
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE splits (run_ts TEXT NOT NULL, away TEXT, home TEXT, market TEXT, "
                 "tickets_pct REAL, money_pct REAL, num_bets INTEGER)")
    conn.execute("INSERT INTO splits VALUES ('2026-10-20T00:00:00+00:00', 'A', 'B', 'spreads', 60, 50, 10)")
    conn.commit()
    conn.close()
    store = Store(path)
    assert len(store.latest_splits()) == 1
    store.close()


def test_dk_open_note_uses_favorite_notation():
    # DEN @ NOP: el local (Pelicans) +4.5 es Nuggets -4.5
    assert line_label("spreads", 4.5, "New Orleans Pelicans", "Denver Nuggets") == "Nuggets -4.5"
    assert line_label("totals", 238.5, "a", "b") == "238.5"
