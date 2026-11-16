import numpy as np
import pandas as pd
import pytest

from app import indicators as ind
from helpers import make_frame


def test_ema_matches_definition():
    s = pd.Series(np.arange(1, 101, dtype=float))
    e = ind.ema(s, 10)
    assert e.iloc[:9].isna().all()
    alpha = 2 / 11
    manual = s.iloc[0]
    for v in s.iloc[1:]:
        manual = alpha * v + (1 - alpha) * manual
    assert e.iloc[-1] == pytest.approx(manual)


def test_rsi_extremes_and_range():
    up = pd.Series(np.linspace(1, 2, 50))
    assert ind.rsi(up).iloc[-1] == pytest.approx(100.0)
    down = pd.Series(np.linspace(2, 1, 50))
    assert ind.rsi(down).iloc[-1] == pytest.approx(0.0)
    rnd = pd.Series(100 + np.random.default_rng(0).normal(0, 1, 300).cumsum())
    r = ind.rsi(rnd).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_rsi_wilder_reference():
    # Contoh klasik Wilder (14 periode): avg gain 3.34/14, avg loss 1.40/14 -> RSI 70.46
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    assert ind.rsi(pd.Series(closes)).iloc[-1] == pytest.approx(70.46, abs=0.01)


def test_macd_hist_sign_follows_trend():
    up = pd.Series(np.exp(np.linspace(0, 0.1, 100) ** 2))
    m = ind.macd(up)
    assert m["hist"].iloc[-1] > 0 and m["macd"].iloc[-1] > 0


def test_atr_constant_range():
    df = pd.DataFrame({"open": [10] * 50, "high": [11] * 50, "low": [9] * 50, "close": [10] * 50})
    assert ind.atr(df).iloc[-1] == pytest.approx(2.0)


def test_swings_and_sr_levels():
    closes = np.concatenate([np.linspace(1, 1.1, 20), np.linspace(1.1, 1.0, 20)] * 3)
    df = make_frame(closes, 60, noise=0)
    sw = ind.swings(df)
    assert {s.kind for s in sw} == {"high", "low"}
    levels = ind.support_resistance(df, tolerance=0.005)
    prices = [lv["price"] for lv in levels]
    assert any(abs(p - 1.1) < 0.005 for p in prices) and any(abs(p - 1.0) < 0.01 for p in prices)


def test_fibonacci_bullish_levels():
    closes = np.concatenate([np.linspace(1.05, 1.0, 5), np.linspace(1.0, 1.2, 30), np.linspace(1.2, 1.12, 10), np.linspace(1.12, 1.13, 5)])
    fib = ind.fibonacci_leg(make_frame(closes, 60, noise=0), "bullish")
    assert fib is not None
    assert fib["swing_high"] == pytest.approx(1.2, abs=1e-3)
    assert fib["levels"]["0.618"] == pytest.approx(1.2 - 0.618 * (1.2 - fib["swing_low"]))
    lo, hi = fib["golden_zone"]
    assert lo < fib["levels"]["0.618"] < hi


def test_bullish_order_block_detected():
    closes = list(np.full(30, 1.0))
    closes += [0.995, 1.01, 1.02, 1.03, 1.031, 1.032]  # candle bearish lalu impuls naik
    df = make_frame(closes, 60, noise=0.0001)
    obs = ind.order_blocks(df, ind.atr(df), impulse_atr=1.5)
    assert any(ob["type"] == "bullish" and ob["bottom"] < 0.996 for ob in obs)
