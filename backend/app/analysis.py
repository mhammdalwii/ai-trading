"""Mesin analisis Multi-Timeframe Confluence.

H4 & H1 : arah tren (EMA 50/200) dan key level (S/R, Order Block, Fibonacci golden zone)
M15     : trigger entry (RSI & MACD)
Risk    : SL/TP dinamis dari ATR M15 dengan rasio Risk:Reward minimal 1:1.5 (TP1) dan 1:2 (TP2)
News    : rilis berita high impact ±30 menit -> WAIT & SEE dan Confidence Score diturunkan
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import indicators as ind

DIGITS = {"XAUUSD": 2, "BTCUSD": 2, "ETHUSD": 2, "EURUSD": 5, "GBPUSD": 5, "USDJPY": 3, "AUDUSD": 5}

MIN_BARS = 210  # EMA 200 butuh minimal 200 candle

# Bobot Confidence Score (total 100)
W_H4_TREND = 20
W_H1_TREND = 15
W_FIB = 15
W_SR = 10
W_OB = 10
W_MACD = 15
W_RSI = 15

NEWS_PENALTY = 40
NEWS_UNAVAILABLE_PENALTY = 10
MIN_SCORE_TO_SIGNAL = 60
AUTO_TRADE_SCORE = 80

SL_ATR_MULT = 1.5
RR_TP1 = 1.5
RR_TP2 = 2.0


@dataclass
class Factor:
    name: str
    points: float
    max_points: float
    note: str

    def as_dict(self):
        return {"name": self.name, "points": self.points, "max_points": self.max_points, "note": self.note}


@dataclass
class AnalysisResult:
    symbol: str
    signal: str  # BUY | SELL | WAIT_AND_SEE | NO_TRADE
    direction: str  # bullish | bearish | neutral
    confidence: float
    entry: float | None
    stop_loss: float | None
    take_profit_1: float | None
    take_profit_2: float | None
    risk_reward: dict | None
    auto_trade_eligible: bool
    factors: list[Factor] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["factors"] = [f.as_dict() for f in self.factors]
        return d


def to_frame(candles) -> pd.DataFrame:
    """list[Candle] atau list[dict] -> DataFrame."""
    rows = [c.model_dump() if hasattr(c, "model_dump") else c for c in candles]
    return pd.DataFrame(rows)


def trend(df: pd.DataFrame) -> dict:
    close = df["close"]
    e50 = ind.ema(close, 50).iloc[-1]
    e200 = ind.ema(close, 200).iloc[-1]
    last = close.iloc[-1]
    if last > e50 > e200:
        state = "bullish"
    elif last < e50 < e200:
        state = "bearish"
    else:
        state = "neutral"
    return {"state": state, "close": float(last), "ema50": float(e50), "ema200": float(e200)}


def _direction(h4: str, h1: str) -> str:
    if h4 != "neutral" and h1 in (h4, "neutral"):
        return h4
    if h4 == "neutral" and h1 != "neutral":
        return h1
    return "neutral"  # netral semua, atau H4 dan H1 berlawanan


def analyze(
    symbol: str,
    frames: dict[str, pd.DataFrame],
    bid: float,
    ask: float,
    news: dict | None = None,
) -> AnalysisResult:
    digits = DIGITS.get(symbol, 5)
    rnd = lambda x: None if x is None else round(float(x), digits)  # noqa: E731
    factors: list[Factor] = []
    warnings: list[str] = []

    for tf in ("H4", "H1", "M15"):
        if len(frames[tf]) < MIN_BARS:
            raise ValueError(f"Data {tf} kurang: butuh minimal {MIN_BARS} candle, ada {len(frames[tf])}")

    h4, h1, m15 = frames["H4"], frames["H1"], frames["M15"]
    price = (bid + ask) / 2

    # --- 1. Tren H4 & H1 -----------------------------------------------------
    t4, t1 = trend(h4), trend(h1)
    direction = _direction(t4["state"], t1["state"])
    bull = direction == "bullish"

    factors.append(Factor("Tren H4 (EMA 50/200)", W_H4_TREND if t4["state"] == direction != "neutral" else 0,
                          W_H4_TREND, f"H4 {t4['state']}"))
    factors.append(Factor("Tren H1 (EMA 50/200)", W_H1_TREND if t1["state"] == direction != "neutral" else 0,
                          W_H1_TREND, f"H1 {t1['state']}"))

    atr_h1 = float(ind.atr(h1).iloc[-1])
    atr_m15 = float(ind.atr(m15).iloc[-1])

    # --- 2. Key level ----------------------------------------------------------
    fib = ind.fibonacci_leg(h1, direction) if direction != "neutral" else None
    fib_pts, fib_note = 0.0, "Tidak ada leg Fibonacci searah tren"
    if fib:
        lo, hi = fib["golden_zone"]
        l382, l786 = sorted([fib["levels"]["0.382"], fib["levels"]["0.786"]])
        if lo <= price <= hi:
            fib_pts, fib_note = W_FIB, "Harga di golden zone (0.5 sampai 0.65, sekitar 0.618)"
        elif l382 <= price <= l786:
            fib_pts, fib_note = W_FIB / 2, "Harga di area retracement 0.382 sampai 0.786"
        else:
            fib_note = "Harga di luar area retracement"
    factors.append(Factor("Fibonacci H1", fib_pts, W_FIB, fib_note))

    levels = ind.support_resistance(h4, tolerance=0.5 * atr_h1) + ind.support_resistance(h1, tolerance=0.5 * atr_h1)
    sr_pts, sr_note = 0.0, "Tidak dekat level S/R yang relevan"
    if direction != "neutral":
        if bull:
            near = [lv for lv in levels if price - atr_h1 <= lv["price"] <= price + 0.25 * atr_h1]
            if near:
                sr_pts, sr_note = W_SR, f"Dekat support {rnd(max(near, key=lambda lv: lv['price'])['price'])}"
        else:
            near = [lv for lv in levels if price - 0.25 * atr_h1 <= lv["price"] <= price + atr_h1]
            if near:
                sr_pts, sr_note = W_SR, f"Dekat resistance {rnd(min(near, key=lambda lv: lv['price'])['price'])}"
    factors.append(Factor("Support/Resistance H4/H1", sr_pts, W_SR, sr_note))

    obs = ind.order_blocks(h1, ind.atr(h1)) + ind.order_blocks(h4, ind.atr(h4))
    ob_pts, ob_note = 0.0, "Tidak ada Order Block searah di dekat harga"
    want = "bullish" if bull else "bearish"
    for ob in obs:
        if direction == "neutral" or ob["type"] != want:
            continue
        if ob["bottom"] - 0.5 * atr_h1 <= price <= ob["top"] + 0.5 * atr_h1:
            ob_pts, ob_note = W_OB, f"Di area {want} Order Block {rnd(ob['bottom'])} sampai {rnd(ob['top'])}"
            break
    factors.append(Factor("Order Block (SMC)", ob_pts, W_OB, ob_note))

    # --- 3. Trigger M15 --------------------------------------------------------
    m = ind.macd(m15["close"])
    hist = m["hist"]
    r = ind.rsi(m15["close"])
    rsi_now, rsi_prev = float(r.iloc[-1]), float(r.iloc[-2])
    sign = 1 if bull else -1
    crossed = any(
        sign * hist.iloc[-k] > 0 and sign * hist.iloc[-k - 1] <= 0 for k in range(1, 4)
    )
    macd_pts, macd_note = 0.0, "MACD belum mendukung"
    if direction != "neutral" and sign * hist.iloc[-1] > 0:
        if crossed or sign * (hist.iloc[-1] - hist.iloc[-2]) > 0:
            macd_pts, macd_note = W_MACD, "MACD cross / histogram menguat searah tren"
        else:
            macd_pts, macd_note = W_MACD / 2, "Histogram MACD searah tren tapi melemah"
    factors.append(Factor("MACD M15", macd_pts, W_MACD, macd_note))

    rsi_pts, rsi_note = 0.0, f"RSI {rsi_now:.1f} belum mendukung"
    if direction != "neutral":
        r_adj, r_prev_adj = (rsi_now, rsi_prev) if bull else (100 - rsi_now, 100 - rsi_prev)
        if 50 <= r_adj <= 70:
            rsi_pts, rsi_note = W_RSI, f"RSI {rsi_now:.1f} momentum searah tren"
        elif 40 <= r_adj < 50 and r_adj > r_prev_adj:
            rsi_pts, rsi_note = W_RSI / 2, f"RSI {rsi_now:.1f} mulai berbalik searah tren"
        elif r_adj > 70:
            rsi_note = f"RSI {rsi_now:.1f} sudah jenuh ({'overbought' if bull else 'oversold'}), rawan koreksi"
    factors.append(Factor("RSI M15", rsi_pts, W_RSI, rsi_note))

    trigger = macd_pts > 0 and rsi_pts > 0
    score = sum(f.points for f in factors)

    # --- 4. News guardrail -----------------------------------------------------
    news = news or {}
    if news.get("blocked"):
        score -= NEWS_PENALTY
        titles = ", ".join(f"{e['currency']} {e['title']}" for e in news["events"][:3])
        warnings.append(f"Berita high impact dalam ±30 menit: {titles}")
    elif news.get("error") and not news.get("source"):
        score -= NEWS_UNAVAILABLE_PENALTY
        warnings.append("Kalender berita tidak bisa diambil, cek jadwal berita manual")
    score = max(0.0, min(100.0, score))

    # --- 5. Keputusan & risk management ---------------------------------------
    if direction == "neutral":
        signal = "NO_TRADE"
        if t4["state"] != "neutral" and t1["state"] != "neutral":
            warnings.append("Tren H4 dan H1 berlawanan")
    elif news.get("blocked"):
        signal = "WAIT_AND_SEE"
    elif trigger and score >= MIN_SCORE_TO_SIGNAL:
        signal = "BUY" if bull else "SELL"
    else:
        signal = "NO_TRADE"

    entry = sl = tp1 = tp2 = None
    if direction != "neutral":
        spread = ask - bid
        dist = max(SL_ATR_MULT * atr_m15, 3 * spread)
        entry = ask if bull else bid
        sl = entry - sign * dist
        tp1 = entry + sign * dist * RR_TP1
        tp2 = entry + sign * dist * RR_TP2

    return AnalysisResult(
        symbol=symbol,
        signal=signal,
        direction=direction,
        confidence=round(score, 1),
        entry=rnd(entry), stop_loss=rnd(sl), take_profit_1=rnd(tp1), take_profit_2=rnd(tp2),
        risk_reward={"tp1": RR_TP1, "tp2": RR_TP2} if entry is not None else None,
        auto_trade_eligible=signal in ("BUY", "SELL") and score > AUTO_TRADE_SCORE,
        factors=factors,
        warnings=warnings,
        context={
            "price": rnd(price),
            "trend": {"H4": t4, "H1": t1},
            "atr": {"H1": rnd(atr_h1), "M15": rnd(atr_m15)},
            "rsi_m15": round(rsi_now, 1),
            "macd_m15": {k: rnd(v) for k, v in m.iloc[-1].items()},
            "fibonacci": fib,
            "levels": [{"price": rnd(lv["price"]), "touches": lv["touches"]} for lv in levels],
            "order_blocks": [{**ob, "top": rnd(ob["top"]), "bottom": rnd(ob["bottom"])} for ob in obs],
            "news": news,
        },
    )
