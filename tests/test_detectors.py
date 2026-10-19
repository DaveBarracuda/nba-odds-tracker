from datetime import datetime, timedelta, timezone

from tracker.detectors import GRADUAL, INFO, NOISE, RLM, STEAM, analyze
from tracker.lines import LinePoint

T0 = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def make_series(values, prices=None, step_h=1.0):
    prices = prices or [-110] * len(values)
    return [LinePoint(T0 + timedelta(hours=i * step_h), v, p) for i, (v, p) in enumerate(zip(values, prices))]


def kinds(signals):
    return [s.kind for s in signals]


def test_steam_on_one_point_spread_jump_without_news():
    assert kinds(analyze(make_series([-5, -5, -6]), "spreads")) == [STEAM]


def test_info_when_jump_coincides_with_injury_news():
    signals = analyze(make_series([-5, -6.5]), "spreads", injury_news=["Jugador X (Team): Out"])
    assert kinds(signals) == [INFO]
    assert "Jugador X" in signals[0].detail


def test_info_probable_on_big_jump_without_news():
    assert kinds(analyze(make_series([-5, -7.5]), "spreads")) == [INFO]


def test_jump_ignored_when_snapshots_too_far_apart():
    assert STEAM not in kinds(analyze(make_series([-5, -6], step_h=6), "spreads"))


def test_gradual_on_consecutive_small_moves_with_pause():
    assert kinds(analyze(make_series([-3, -3.5, -3.5, -4, -4.5]), "spreads")) == [GRADUAL]


def test_no_gradual_after_reversal_is_noise_bounce():
    assert kinds(analyze(make_series([-3, -3.5, -3, -3.5]), "spreads")) == [NOISE]


def test_noise_on_juice_only_change():
    signals = analyze(make_series([220.5, 220.5], prices=[-110, -115]), "totals")
    assert kinds(signals) == [NOISE]
    assert "juice" in signals[0].detail


def test_noise_on_half_point_total_move():
    assert kinds(analyze(make_series([220, 220.5]), "totals")) == [NOISE]


def test_no_signal_when_line_unchanged():
    assert analyze(make_series([-5, -5]), "spreads") == []


def test_rlm_spread_public_on_home_line_moves_toward_home():
    assert RLM in kinds(analyze(make_series([-5, -4.5, -4.5]), "spreads", public_pct=70))


def test_no_rlm_when_line_moves_with_public():
    assert RLM not in kinds(analyze(make_series([-5, -5.5]), "spreads", public_pct=70))


def test_rlm_totals_public_on_over_total_drops():
    assert RLM in kinds(analyze(make_series([221, 220, 220]), "totals", public_pct=72))


def test_rlm_public_on_away_moneyline_home_prob_rises():
    assert RLM in kinds(analyze(make_series([55, 57]), "h2h", public_pct=30))


def test_no_rlm_when_public_split_is_balanced():
    assert RLM not in kinds(analyze(make_series([-5, -4]), "spreads", public_pct=55))
