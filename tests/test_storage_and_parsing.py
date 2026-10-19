from datetime import datetime, timezone

import pytest

from tracker.injuries import news_for_teams
from tracker.lines import implied_prob, line_point
from tracker.odds_client import parse_odds
from tracker.public_betting import load_public_pct, make_key
from tracker.storage import Store

EVENT = {
    "id": "ev1", "commence_time": "2026-10-20T23:00:00Z",
    "home_team": "New York Knicks", "away_team": "Philadelphia 76ers",
    "bookmakers": [{"key": "draftkings", "markets": [
        {"key": "h2h", "outcomes": [{"name": "New York Knicks", "price": -150},
                                    {"name": "Philadelphia 76ers", "price": 130}]},
        {"key": "spreads", "outcomes": [{"name": "New York Knicks", "price": -110, "point": -3.5},
                                        {"name": "Philadelphia 76ers", "price": -110, "point": 3.5}]},
        {"key": "totals", "outcomes": [{"name": "Over", "price": -108, "point": 224.5},
                                       {"name": "Under", "price": -112, "point": 224.5}]},
    ]}],
}


def test_implied_prob():
    assert implied_prob(-150) == pytest.approx(0.6)
    assert implied_prob(150) == pytest.approx(0.4)


def test_parse_odds_maps_sides():
    quotes = parse_odds([EVENT])
    assert {(q.market, q.outcome) for q in quotes} == {
        ("h2h", "home"), ("h2h", "away"), ("spreads", "home"), ("spreads", "away"),
        ("totals", "over"), ("totals", "under")}


def test_h2h_line_point_removes_vig():
    point = line_point(datetime.now(timezone.utc), "h2h", {"home": (None, -150), "away": (None, 130)})
    assert point.value == pytest.approx(100 * 0.6 / (0.6 + 100 / 230), abs=0.01)


def test_store_builds_history_and_dedupes_alerts(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    quotes = parse_odds([EVENT])
    store.save_quotes("2026-10-20T12:00:00+00:00", quotes)
    store.save_quotes("2026-10-20T13:00:00+00:00", quotes)
    history = store.line_history("ev1", "draftkings", "spreads")
    assert [p.value for p in history] == [-3.5, -3.5]
    args = ("sig", "ts", "STEAM", "high", "ev1", "draftkings", "spreads", "msg")
    assert store.add_alert(*args) is True
    assert store.add_alert(*args) is False
    store.close()


def test_previous_injuries_and_news(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    assert store.previous_injuries("2026-10-20T12:00:00+00:00") is None
    store.save_injuries("2026-10-20T12:00:00+00:00", [("New York Knicks", "A", "Questionable")])
    prev = store.previous_injuries("2026-10-20T13:00:00+00:00")
    curr = {("New York Knicks", "A", "Out"), ("Boston Celtics", "B", "Out")}
    assert news_for_teams(prev, curr, ["New York Knicks", "Philadelphia 76ers"]) == ["A (New York Knicks): Out"]
    store.close()


def test_load_public_pct_skips_invalid_rows(tmp_path):
    path = tmp_path / "public.csv"
    path.write_text("away,home,market,pct\n"
                    "Philadelphia 76ers,New York Knicks,spreads,68\n"
                    "Philadelphia 76ers,New York Knicks,props,50\n"
                    "Philadelphia 76ers,New York Knicks,totals,abc\n", encoding="utf-8")
    data = load_public_pct(path)
    assert data == {make_key("Philadelphia 76ers", "New York Knicks", "spreads"): 68.0}
