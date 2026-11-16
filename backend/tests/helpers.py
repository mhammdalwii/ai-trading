import numpy as np
import pandas as pd


def make_frame(closes, minutes, end="2026-11-16 15:00", noise=0.0002, seed=1):
    closes = np.asarray(closes, dtype=float)
    rng = np.random.default_rng(seed)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    wick = np.abs(rng.normal(0, noise, len(closes))) * closes
    times = pd.date_range(end=pd.Timestamp(end, tz="UTC"), periods=len(closes), freq=f"{minutes}min")
    return pd.DataFrame({
        "time": times, "open": opens, "high": np.maximum(opens, closes) + wick,
        "low": np.minimum(opens, closes) - wick, "close": closes, "tick_volume": 100, "spread": 10,
    })


def trend_with_bounce(n, start, slope, direction=1, dip=30, bounce=12, seed=1):
    """Tren naik (direction=1) / turun (-1) lalu koreksi `dip` candle dan pantulan `bounce` candle searah tren."""
    rng = np.random.default_rng(seed)
    steps = np.full(n, slope * direction) + rng.normal(0, slope * 0.3, n)
    steps[n - dip - bounce : n - bounce] = -slope * direction * 1.2
    steps[n - bounce :] = slope * direction * 1.0
    return start * np.exp(np.cumsum(steps))


def bullish_frames():
    return {
        "H4": make_frame(start_trend(300, 1.0, 0.002, 1), 240),
        "H1": make_frame(start_trend(300, 1.0, 0.001, 1), 60),
        "M15": make_frame(trend_with_bounce(300, 1.0, 0.0004, 1), 15),
    }


def bearish_frames():
    return {
        "H4": make_frame(start_trend(300, 1.0, 0.002, -1), 240),
        "H1": make_frame(start_trend(300, 1.0, 0.001, -1), 60),
        "M15": make_frame(trend_with_bounce(300, 1.0, 0.0004, -1), 15),
    }


def start_trend(n, start, slope, direction, seed=2):
    rng = np.random.default_rng(seed)
    steps = np.full(n, slope * direction) + rng.normal(0, slope * 0.3, n)
    return start * np.exp(np.cumsum(steps))
