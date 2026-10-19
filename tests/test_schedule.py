from datetime import datetime, timedelta

from tracker.schedule import ET, estimate_paid_runs, is_dense, snapshot_due

# Slate de prueba: juegos a las 19:00 y 22:00 ET
SLATE = [datetime(2026, 10, 21, 19, tzinfo=ET), datetime(2026, 10, 21, 22, tzinfo=ET)]


def et(hour, minute=0, day=21):
    return datetime(2026, 10, day, hour, minute, tzinfo=ET)


def test_hourly_until_two_hours_before_first_game():
    assert not is_dense(SLATE, et(16, 59))
    assert is_dense(SLATE, et(17, 0))


def test_dense_between_games_until_last_tip():
    assert is_dense(SLATE, et(20, 30))   # el primer juego ya empezó
    assert not is_dense(SLATE, et(22, 0))


def test_other_days_games_do_not_trigger_dense():
    assert not is_dense(SLATE, et(18, 0, day=20))


def test_snapshot_due_respects_interval_with_cron_slack():
    last = et(17, 0)
    assert not snapshot_due(last, et(17, 15), dense=True)
    assert snapshot_due(last, et(17, 25), dense=True)
    assert not snapshot_due(last, et(17, 30), dense=False)
    assert snapshot_due(last, et(17, 55), dense=False)
    assert snapshot_due(None, et(3, 0), dense=False)


def test_estimate_paid_runs_one_day():
    # 00:00-17:00 cada hora = 17; 17:00-22:00 cada 30 min = 10; 22:00-24:00 cada hora = 2
    assert estimate_paid_runs(SLATE, et(0), et(0) + timedelta(days=1)) == 29
