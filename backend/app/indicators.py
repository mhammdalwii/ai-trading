"""Indikator teknikal. Semua fungsi menerima DataFrame OHLC (kolom open/high/low/close)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _wilder(values: np.ndarray, period: int) -> np.ndarray:
    """Smoothing Wilder: nilai awal = rata-rata `period` data pertama, lalu rekursif."""
    out = np.full(len(values), np.nan)
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid) < period:
        return out
    start = valid[0] + period - 1
    out[start] = np.nanmean(values[valid[0] : start + 1])
    for i in range(start + 1, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI metode Wilder (sama dengan MT5)."""
    delta = close.diff().to_numpy()
    gain = _wilder(np.where(np.isnan(delta), np.nan, np.clip(delta, 0, None)), period)
    loss = _wilder(np.where(np.isnan(delta), np.nan, np.clip(-delta, 0, None)), period)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(loss == 0, 100.0, 100 - 100 / (1 + gain / loss))
    out[np.isnan(gain)] = np.nan
    return pd.Series(out, index=close.index)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR metode Wilder."""
    prev_close = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return pd.Series(_wilder(tr.to_numpy(dtype=float), period), index=df.index)


# ---------------------------------------------------------------------------
# Struktur harga
# ---------------------------------------------------------------------------
@dataclass
class Swing:
    index: int
    price: float
    kind: str  # "high" atau "low"


def swings(df: pd.DataFrame, left: int = 3, right: int = 3) -> list[Swing]:
    """Swing high/low (fractal): titik tertinggi/terendah di antara `left` dan `right` candle."""
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    out: list[Swing] = []
    for i in range(left, len(df) - right):
        # Untuk nilai kembar (equal highs/lows), yang diambil adalah candle pertama.
        if highs[i] > highs[i - left : i].max() and highs[i] >= highs[i + 1 : i + right + 1].max():
            out.append(Swing(i, float(highs[i]), "high"))
        if lows[i] < lows[i - left : i].min() and lows[i] <= lows[i + 1 : i + right + 1].min():
            out.append(Swing(i, float(lows[i]), "low"))
    return out


def support_resistance(df: pd.DataFrame, tolerance: float, max_levels: int = 6) -> list[dict]:
    """Kelompokkan swing yang berdekatan (dalam `tolerance` harga) menjadi level S/R.

    Level dengan sentuhan lebih banyak dianggap lebih kuat.
    """
    points = sorted(s.price for s in swings(df))
    clusters: list[list[float]] = []
    for p in points:
        if clusters and p - clusters[-1][-1] <= tolerance:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    levels = [{"price": float(np.mean(c)), "touches": len(c)} for c in clusters]
    levels.sort(key=lambda lv: lv["touches"], reverse=True)
    return sorted(levels[:max_levels], key=lambda lv: lv["price"])


def order_blocks(df: pd.DataFrame, atr_series: pd.Series, impulse_atr: float = 1.5, lookback: int = 120) -> list[dict]:
    """Order Block sederhana ala SMC.

    Bullish OB: candle bearish terakhir sebelum impuls naik yang kuat (>= impulse_atr x ATR dalam 3 candle)
    dan menembus high candle tersebut. Bearish OB kebalikannya. OB yang sudah ditembus (mitigated
    ditutup melewati sisi seberangnya) dibuang.
    """
    o, h, low, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    a = atr_series.to_numpy()
    n = len(df)
    start = max(1, n - lookback)
    blocks: list[dict] = []
    for i in range(start, n - 3):
        if np.isnan(a[i]):
            continue
        move_up = c[i + 3] - c[i]
        move_dn = c[i] - c[i + 3]
        if c[i] < o[i] and move_up >= impulse_atr * a[i] and c[i + 3] > h[i]:
            later = c[i + 4 :]
            if not (later < low[i]).any():
                blocks.append({"type": "bullish", "top": float(h[i]), "bottom": float(low[i]), "index": i})
        elif c[i] > o[i] and move_dn >= impulse_atr * a[i] and c[i + 3] < low[i]:
            later = c[i + 4 :]
            if not (later > h[i]).any():
                blocks.append({"type": "bearish", "top": float(h[i]), "bottom": float(low[i]), "index": i})
    return blocks[-5:]


def fibonacci_leg(df: pd.DataFrame, direction: str) -> dict | None:
    """Level Fibonacci retracement dari swing leg terakhir searah tren.

    direction="bullish": leg dari swing low ke swing high setelahnya (retracement turun).
    direction="bearish": leg dari swing high ke swing low setelahnya (retracement naik).
    """
    sw = swings(df)
    lows = [s for s in sw if s.kind == "low"]
    highs = [s for s in sw if s.kind == "high"]
    if not lows or not highs:
        return None
    if direction == "bullish":
        hi = highs[-1]
        prior = [s for s in lows if s.index < hi.index]
        if not prior:
            return None
        lo = min(prior[-3:], key=lambda s: s.price)
        rng = hi.price - lo.price
        level = lambda r: hi.price - rng * r  # noqa: E731
    else:
        lo = lows[-1]
        prior = [s for s in highs if s.index < lo.index]
        if not prior:
            return None
        hi = max(prior[-3:], key=lambda s: s.price)
        rng = hi.price - lo.price
        level = lambda r: lo.price + rng * r  # noqa: E731
    if rng <= 0:
        return None
    return {
        "direction": direction,
        "swing_high": hi.price,
        "swing_low": lo.price,
        "levels": {str(r): float(level(r)) for r in (0.236, 0.382, 0.5, 0.618, 0.786)},
        "golden_zone": sorted([float(level(0.5)), float(level(0.65))]),
    }
