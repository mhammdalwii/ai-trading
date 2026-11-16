import pytest

from app.analysis import AUTO_TRADE_SCORE, NEWS_PENALTY, RR_TP1, RR_TP2, analyze
from helpers import bearish_frames, bullish_frames, make_frame, start_trend


def _run(frames, news=None, spread=0.0001):
    px = frames["M15"]["close"].iloc[-1]
    return analyze("EURUSD", frames, px, px + spread, news)


def test_bullish_confluence_gives_buy():
    r = _run(bullish_frames())
    assert r.signal == "BUY" and r.direction == "bullish"
    assert r.confidence >= 60
    assert r.stop_loss < r.entry < r.take_profit_1 < r.take_profit_2


def test_bearish_confluence_gives_sell():
    r = _run(bearish_frames())
    assert r.signal == "SELL"
    assert r.take_profit_2 < r.take_profit_1 < r.entry < r.stop_loss


@pytest.mark.parametrize("frames", [bullish_frames, bearish_frames])
def test_risk_reward_ratios(frames):
    r = _run(frames())
    risk = abs(r.entry - r.stop_loss)
    assert abs(r.take_profit_1 - r.entry) == pytest.approx(risk * RR_TP1, abs=2e-5)
    assert abs(r.take_profit_2 - r.entry) == pytest.approx(risk * RR_TP2, abs=2e-5)


def test_confidence_is_sum_of_factors():
    r = _run(bullish_frames())
    assert r.confidence == pytest.approx(sum(f.points for f in r.factors))
    assert sum(f.max_points for f in r.factors) == 100


def test_news_forces_wait_and_see():
    base = _run(bullish_frames())
    news = {"blocked": True, "events": [{"currency": "USD", "title": "Non-Farm Payrolls"}], "source": "static"}
    r = _run(bullish_frames(), news)
    assert r.signal == "WAIT_AND_SEE"
    assert r.confidence == pytest.approx(max(0, base.confidence - NEWS_PENALTY))
    assert not r.auto_trade_eligible
    assert "Non-Farm Payrolls" in r.warnings[0]


def test_news_unavailable_lowers_score_with_warning():
    base = _run(bullish_frames())
    r = _run(bullish_frames(), {"blocked": False, "events": [], "source": None, "error": "timeout"})
    assert r.confidence < base.confidence
    assert any("manual" in w for w in r.warnings)


def test_conflicting_trends_no_trade():
    frames = bullish_frames()
    frames["H1"] = make_frame(start_trend(300, 1.0, 0.001, -1), 60)
    r = _run(frames)
    assert r.signal == "NO_TRADE" and r.direction == "neutral"
    assert r.entry is None and any("berlawanan" in w for w in r.warnings)


def test_auto_trade_flag_threshold():
    r = _run(bullish_frames())
    assert r.auto_trade_eligible == (r.confidence > AUTO_TRADE_SCORE)


def test_not_enough_data():
    frames = {k: v.tail(100) for k, v in bullish_frames().items()}
    with pytest.raises(ValueError, match="kurang"):
        _run(frames)


def test_sl_at_least_three_spreads():
    r = _run(bullish_frames(), spread=0.01)
    assert r.entry - r.stop_loss >= 3 * 0.01 - 1e-9
