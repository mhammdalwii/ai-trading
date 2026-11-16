"""Eksekusi order dari dashboard: validasi, kirim ke MT5, dan catat ke log."""
from __future__ import annotations

import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .data_sources import DataSource


class TradeRejected(ValueError):
    """Order ditolak oleh pengaman dashboard (bukan oleh broker)."""


_log_lock = threading.Lock()


def normalize_volume(volume: float, spec: dict, max_lot: float) -> float:
    step = spec["volume_step"] or 0.01
    vol = math.floor(round(volume / step, 6)) * step
    vol = round(vol, max(0, -int(math.floor(math.log10(step)))))
    if vol < spec["volume_min"]:
        raise TradeRejected(f"Lot {volume} di bawah minimum broker ({spec['volume_min']})")
    if vol > spec["volume_max"]:
        raise TradeRejected(f"Lot {volume} di atas maksimum broker ({spec['volume_max']})")
    if vol > max_lot:
        raise TradeRejected(f"Lot {volume} melebihi batas MAX_LOT di pengaturan ({max_lot})")
    return vol


def validate_stops(side: str, price: float, sl: float, tp: float, stops_level: float) -> None:
    if side == "BUY" and not (sl < price < tp):
        raise TradeRejected("Untuk BUY, SL harus di bawah harga dan TP di atas harga")
    if side == "SELL" and not (tp < price < sl):
        raise TradeRejected("Untuk SELL, SL harus di atas harga dan TP di bawah harga")
    if abs(price - sl) < stops_level or abs(tp - price) < stops_level:
        raise TradeRejected(f"Jarak SL/TP terlalu dekat dengan harga (minimal {stops_level} menurut broker)")


def log_order(settings: Settings, entry: dict) -> None:
    path = Path(settings.order_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"logged_at": datetime.now(timezone.utc).isoformat(), **entry}
    with _log_lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def read_order_log(settings: Settings, limit: int = 50) -> list[dict]:
    path = Path(settings.order_log_path)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    return [json.loads(line) for line in reversed(lines) if line.strip()]


def execute_signal(source: DataSource, settings: Settings, analysis: dict, volume: float,
                   take_profit: str = "tp2", trigger: str = "manual") -> dict:
    """Buka posisi berdasarkan hasil analisis yang BARU dihitung di server."""
    symbol = analysis["symbol"]
    side = analysis["signal"]
    base = {"symbol": symbol, "side": side, "trigger": trigger, "confidence": analysis["confidence"]}
    try:
        if side not in ("BUY", "SELL"):
            raise TradeRejected(f"Sinyal saat ini {side}, bukan BUY/SELL. Order tidak dikirim.")
        spec = source.symbol_spec(symbol)
        vol = normalize_volume(volume, spec, settings.max_lot)
        tp = analysis["take_profit_1"] if take_profit == "tp1" else analysis["take_profit_2"]
        sl = analysis["stop_loss"]
        tick = source.get_tick(symbol)
        price = tick.ask if side == "BUY" else tick.bid
        validate_stops(side, price, sl, tp, spec["stops_level"])
        comment = f"AI {side} {analysis['confidence']:.0f}%"
        result = source.send_market_order(symbol, side, vol, sl, tp, comment)
    except Exception as exc:
        log_order(settings, {**base, "status": "rejected", "reason": str(exc)})
        raise
    entry = {**base, "status": "filled", "volume": vol, "sl": sl, "tp": tp, "result": result}
    log_order(settings, entry)
    return entry
