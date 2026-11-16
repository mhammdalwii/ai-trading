"""Auto-Trigger: eksekusi otomatis ke MT5 ketika Confidence Score di atas ambang (default 80%).

Pengaman (semua harus lolos sebelum order dikirim):
- Saklar harus ON. Saklar selalu OFF setiap kali aplikasi baru dijalankan.
- Akun REAL ditolak kecuali ALLOW_AUTO_ON_REAL_ACCOUNT=true.
- Sinyal BUY/SELL, skor > AUTO_MIN_CONFIDENCE, dan tidak ada berita high impact (WAIT & SEE).
- Maksimal AUTO_MAX_OPEN_POSITIONS posisi terbuka, dan hanya 1 posisi per pair.
- Cooldown per pair setelah auto-trade, dan batas jumlah auto-trade per hari.
- Spread tidak boleh lebih dari AUTO_MAX_SPREAD_RATIO x jarak SL.
- Kill switch: jika equity turun AUTO_MAX_DAILY_LOSS_PERCENT dari awal hari, saklar dimatikan otomatis.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from .config import SYMBOLS, Settings
from .data_sources import DataSource
from .trading import execute_signal, log_order

log = logging.getLogger("trading.auto")

ACCOUNT_MODE_REAL = 2  # ACCOUNT_TRADE_MODE_REAL di MT5


class AutoTrader:
    def __init__(self, source: DataSource, settings: Settings, analyze_fn: Callable[[str], dict],
                 clock: Callable[[], datetime] | None = None):
        self.source = source
        self.settings = settings
        self.analyze_fn = analyze_fn
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.enabled = False
        self.disabled_reason: str | None = None
        self.last_run: datetime | None = None
        self.last_decisions: dict[str, str] = {}
        self.activity: deque[dict] = deque(maxlen=50)
        self._last_trade_at: dict[str, datetime] = {}
        self._day: date | None = None
        self._day_start_equity: float | None = None
        self._trades_today = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ kontrol
    @property
    def symbols(self) -> list[str]:
        wanted = {s.strip().upper() for s in self.settings.auto_symbols.split(",")}
        return [s for s in SYMBOLS if s in wanted]

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            if on:
                problem = self._account_problem()
                if problem:
                    raise PermissionError(problem)
                self.disabled_reason = None
            self.enabled = on
            self._note("ON" if on else "OFF", "Auto-Trigger dinyalakan" if on else "Auto-Trigger dimatikan")

    def _account_problem(self) -> str | None:
        acc = self.source.account_info() if self.source.is_connected() else None
        if acc is None:
            return "Tidak terhubung ke akun MT5"
        if acc.get("trade_mode") == ACCOUNT_MODE_REAL and not self.settings.allow_auto_on_real_account:
            return ("Akun ini akun REAL. Auto-Trigger hanya diizinkan di akun demo, kecuali "
                    "ALLOW_AUTO_ON_REAL_ACCOUNT=true di file .env.")
        return None

    def _kill(self, reason: str) -> None:
        self.enabled = False
        self.disabled_reason = reason
        self._note("KILL", reason)
        log_order(self.settings, {"trigger": "auto", "status": "auto_disabled", "reason": reason})
        log.warning("Auto-Trigger dimatikan: %s", reason)

    def _note(self, kind: str, message: str, symbol: str | None = None) -> None:
        self.activity.appendleft({"time": self.clock().isoformat(), "kind": kind, "symbol": symbol, "message": message})

    def status(self) -> dict:
        s = self.settings
        return {
            "enabled": self.enabled,
            "disabled_reason": self.disabled_reason,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "trades_today": self._trades_today,
            "day_start_equity": self._day_start_equity,
            "decisions": self.last_decisions,
            "activity": list(self.activity)[:20],
            "rules": {
                "min_confidence": s.auto_min_confidence, "lot": s.auto_lot, "interval_seconds": s.auto_interval_seconds,
                "max_open_positions": s.auto_max_open_positions, "cooldown_minutes": s.auto_cooldown_minutes,
                "max_trades_per_day": s.auto_max_trades_per_day,
                "max_daily_loss_percent": s.auto_max_daily_loss_percent,
                "max_spread_ratio": s.auto_max_spread_ratio, "symbols": self.symbols,
            },
        }

    # ------------------------------------------------------------------ siklus
    def _roll_day(self, now: datetime, equity: float | None) -> None:
        if self._day != now.date():
            self._day = now.date()
            self._trades_today = 0
            self._day_start_equity = equity

    def run_once(self) -> list[dict]:
        """Satu putaran pengecekan semua pair. Mengembalikan order yang dieksekusi."""
        with self._lock:
            now = self.clock()
            self.last_run = now
            if not self.enabled or not self.source.is_connected():
                return []
            acc = self.source.account_info() or {}
            equity = acc.get("equity")
            self._roll_day(now, equity)

            problem = self._account_problem()
            if problem:
                self._kill(problem)
                return []
            if equity is not None and self._day_start_equity:
                loss_pct = (self._day_start_equity - equity) / self._day_start_equity * 100
                if loss_pct >= self.settings.auto_max_daily_loss_percent:
                    self._kill(f"Batas rugi harian tercapai ({loss_pct:.1f}% dari equity awal hari)")
                    return []

            executed = []
            for symbol in self.symbols:
                decision, order = self._consider(symbol, now)
                self.last_decisions[symbol] = decision
                if order:
                    executed.append(order)
            return executed

    def _consider(self, symbol: str, now: datetime) -> tuple[str, dict | None]:
        s = self.settings
        if self._trades_today >= s.auto_max_trades_per_day:
            return f"Batas {s.auto_max_trades_per_day} auto-trade per hari tercapai", None
        last = self._last_trade_at.get(symbol)
        if last and now - last < timedelta(minutes=s.auto_cooldown_minutes):
            left = s.auto_cooldown_minutes - int((now - last).total_seconds() // 60)
            return f"Cooldown, {left} menit lagi", None

        positions = self.source.positions()
        if any(p["symbol"] == symbol for p in positions):
            return "Sudah ada posisi terbuka di pair ini", None
        mine = [p for p in positions if p.get("from_dashboard")]
        if len(mine) >= s.auto_max_open_positions:
            return f"Maksimal {s.auto_max_open_positions} posisi terbuka", None

        try:
            a = self.analyze_fn(symbol)
        except Exception as exc:  # noqa: BLE001
            return f"Analisis gagal: {exc}", None
        if a["signal"] not in ("BUY", "SELL"):
            return f"Sinyal {a['signal']} ({a['confidence']:.0f}%)", None
        if a["confidence"] <= s.auto_min_confidence:
            return f"{a['signal']} {a['confidence']:.0f}%, belum di atas {s.auto_min_confidence:.0f}%", None
        if a.get("context", {}).get("news", {}).get("blocked"):
            return "Ada berita high impact", None

        tick = self.source.get_tick(symbol)
        sl_dist = abs(a["entry"] - a["stop_loss"])
        if sl_dist <= 0 or (tick.ask - tick.bid) > s.auto_max_spread_ratio * sl_dist:
            return "Spread terlalu lebar dibanding jarak SL", None

        try:
            order = execute_signal(self.source, s, a, s.auto_lot, "tp2", trigger="auto")
        except Exception as exc:  # noqa: BLE001 - sudah dicatat di log order
            self._note("ERROR", f"Order gagal: {exc}", symbol)
            return f"Order gagal: {exc}", None
        self._last_trade_at[symbol] = now
        self._trades_today += 1
        self._note("TRADE", f"{a['signal']} {order['volume']} lot, skor {a['confidence']:.0f}%", symbol)
        return f"Dieksekusi: {a['signal']} skor {a['confidence']:.0f}%", order

    # ------------------------------------------------------------------ thread
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="auto-trigger", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.settings.auto_interval_seconds):
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - loop tidak boleh mati
                log.exception("Error di siklus Auto-Trigger")
