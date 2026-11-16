"""Backtest sederhana untuk mesin analisis.

Cara kerja: mundur ke masa lalu, lalu tiap jam (setiap 4 candle M15) jalankan `analyze` hanya dengan
candle yang SUDAH tutup pada saat itu (tanpa melihat masa depan). Jika keluar sinyal BUY/SELL, posisi
disimulasikan di candle M15 berikutnya sampai kena SL atau TP2.

Batasan: berita historis tidak dipakai, satu posisi dalam satu waktu, jika SL dan TP tersentuh
di candle yang sama dianggap kena SL (konservatif).
"""
from __future__ import annotations

from datetime import timedelta

import pandas as pd

from .analysis import DIGITS, RR_TP2, analyze

WINDOW = 300  # candle per timeframe yang diberikan ke analyze
MAX_HOLD_BARS = 96  # 1 hari M15; lewat itu posisi ditutup di harga pasar


def run_backtest(symbol: str, h4: pd.DataFrame, h1: pd.DataFrame, m15: pd.DataFrame, days: int = 30,
                 step: int = 4, min_confidence: float = 0.0) -> dict:
    point = 10 ** -DIGITS.get(symbol, 5)
    m15 = m15.reset_index(drop=True)
    h4_close = h4["time"] + timedelta(hours=4)
    h1_close = h1["time"] + timedelta(hours=1)

    start = max(WINDOW, len(m15) - days * 96)
    trades: list[dict] = []
    i = start
    while i < len(m15) - 1:
        now = m15["time"].iloc[i] + timedelta(minutes=15)  # saat candle M15 ke-i tutup
        f4 = h4[h4_close <= now].tail(WINDOW)
        f1 = h1[h1_close <= now].tail(WINDOW)
        f15 = m15.iloc[: i + 1].tail(WINDOW)
        if len(f4) < WINDOW or len(f1) < WINDOW:
            i += step
            continue
        close = float(m15["close"].iloc[i])
        spread = float(m15["spread"].iloc[i]) * point
        res = analyze(symbol, {"H4": f4.reset_index(drop=True), "H1": f1.reset_index(drop=True),
                               "M15": f15.reset_index(drop=True)}, bid=close, ask=close + spread)
        if res.signal not in ("BUY", "SELL") or res.confidence < min_confidence:
            i += step
            continue

        buy = res.signal == "BUY"
        entry, sl, tp = res.entry, res.stop_loss, res.take_profit_2
        risk = abs(entry - sl)
        outcome, r_mult, exit_i = "timeout", None, None
        for j in range(i + 1, min(i + 1 + MAX_HOLD_BARS, len(m15))):
            hi, lo = m15["high"].iloc[j], m15["low"].iloc[j]
            hit_sl = lo <= sl if buy else hi + spread >= sl
            hit_tp = hi >= tp if buy else lo + spread <= tp
            if hit_sl:
                outcome, r_mult, exit_i = "sl", -1.0, j
                break
            if hit_tp:
                outcome, r_mult, exit_i = "tp", RR_TP2, j
                break
        if r_mult is None:
            exit_i = min(i + MAX_HOLD_BARS, len(m15) - 1)
            px = float(m15["close"].iloc[exit_i])
            r_mult = ((px - entry) if buy else (entry - px - spread)) / risk
        trades.append({
            "time": now.isoformat(), "signal": res.signal, "confidence": res.confidence,
            "entry": entry, "stop_loss": sl, "take_profit": tp, "outcome": outcome, "r": round(r_mult, 2),
        })
        i = exit_i + 1  # satu posisi dalam satu waktu

    wins = sum(1 for t in trades if t["r"] > 0)
    total_r = sum(t["r"] for t in trades)
    return {
        "symbol": symbol,
        "days": days,
        "trades": len(trades),
        "wins": wins,
        "win_rate": round(wins / len(trades) * 100, 1) if trades else None,
        "total_r": round(total_r, 2),
        "avg_r": round(total_r / len(trades), 2) if trades else None,
        "notes": [
            "Berita historis tidak ikut dihitung",
            "Hasil masa lalu tidak menjamin hasil di masa depan",
        ],
        "history": trades[-50:],
    }
