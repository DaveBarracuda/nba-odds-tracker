from datetime import datetime, timezone
from pathlib import Path

from tracker.action_network import parse_splits
from tracker.config import Settings
from tracker.dashboard import build_dashboard
from tracker.odds_client import parse_odds
from tracker.storage import Store

from test_storage_and_parsing import EVENT


def outcome(side, tickets, money, team_id=None):
    return {"side": side, "team_id": team_id, "bet_info": {"tickets": {"percent": tickets},
                                                           "money": {"percent": money}}}


AN_PAYLOAD = {"games": [{
    "start_time": "2026-10-20T23:00:00.000Z", "home_team_id": 1, "away_team_id": 2, "num_bets": 5000,
    "teams": [{"id": 1, "full_name": "New York Knicks"}, {"id": 2, "full_name": "Philadelphia 76ers"}],
    "markets": {"15": {"event": {
        "spread": [outcome("home", 70, 45, 1), outcome("away", 30, 55, 2)],
        "total": [outcome("over", 62, 66), outcome("under", 38, 34)],
        "moneyline": [outcome("away", 40, 41, 2), outcome("home", 60, 59, 1)],
    }}},
}, {"home_team_id": 3, "away_team_id": 4, "num_bets": 0, "teams": [
    {"id": 3, "full_name": "A"}, {"id": 4, "full_name": "B"}], "markets": {}}]}


def test_parse_splits_reads_home_and_over_side():
    splits = {s.market: s for s in parse_splits(AN_PAYLOAD)}
    assert set(splits) == {"spreads", "totals", "h2h"}
    assert (splits["spreads"].tickets_pct, splits["spreads"].money_pct) == (70, 45)
    assert splits["totals"].tickets_pct == 62
    assert splits["h2h"].tickets_pct == 60
    assert splits["spreads"].away == "Philadelphia 76ers"


def make_settings(tmp_path: Path) -> Settings:
    return Settings(api_key="x", bookmakers="draftkings", markets="h2h,spreads,totals", lookahead_hours=48,
                    min_credits=20, active_hours=None, telegram_token=None, telegram_chat_id=None,
                    db_path=tmp_path / "db.sqlite3", public_betting_csv=tmp_path / "p.csv",
                    alerts_log=tmp_path / "alerts.log", tracker_log=tmp_path / "t.log",
                    dashboard_html=tmp_path / "dashboard.html")


def test_dashboard_renders_lines_splits_and_divergence(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(settings.db_path)
    store.save_quotes("2026-10-20T12:00:00+00:00", parse_odds([EVENT]))
    store.save_splits("2026-10-20T12:00:00+00:00", parse_splits(AN_PAYLOAD))
    path = build_dashboard(store, settings, now=datetime(2026, 10, 20, 12, 30, tzinfo=timezone.utc))
    store.close()
    html = path.read_text(encoding="utf-8")
    assert "Philadelphia 76ers" in html and "Knicks -3.5 (-110)" in html
    assert "70 / 30" in html                       # tickets spread
    assert "Dinero +25 pp sobre tickets en <b>76ers</b>" in html
