from datetime import datetime, timezone

import tracker.tracker as tracker_mod
from tracker.odds_client import Usage, parse_odds
from tracker.storage import Store

from test_action_network_dashboard import make_settings
from test_storage_and_parsing import EVENT

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def stub_apis(monkeypatch, fresh_remaining):
    events = [{"commence_time": EVENT["commence_time"]}]
    monkeypatch.setattr(tracker_mod, "fetch_events", lambda key: (events, Usage(fresh_remaining, 0, 0)))
    monkeypatch.setattr(tracker_mod, "fetch_odds",
                        lambda *args: (parse_odds([EVENT]), Usage(fresh_remaining - 3, 3, 3)))
    monkeypatch.setattr(tracker_mod, "_safe_injuries", lambda: None)
    monkeypatch.setattr(tracker_mod, "_safe_splits", lambda now: [])


def last_status(settings):
    store = Store(settings.db_path)
    try:
        return store.runs()[-1].status
    finally:
        store.close()


def seed_low_balance(settings):
    store = Store(settings.db_path)
    store.record_run("2026-10-20T08:00:00+00:00", "ok", 1, Usage(remaining=5, used=495, last=3))
    store.close()


def test_new_key_balance_overrides_stale_low_balance(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    seed_low_balance(settings)          # saldo viejo < MIN_CREDITS guardado en la base
    stub_apis(monkeypatch, fresh_remaining=8828)
    tracker_mod.run_once(settings, now=NOW)
    assert last_status(settings) == "ok"


def test_skips_when_current_key_is_low(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    stub_apis(monkeypatch, fresh_remaining=10)
    tracker_mod.run_once(settings, now=NOW)
    assert last_status(settings) == "skipped_low_credits"
