"""Sumber data harga: MetaTrader 5 (asli) atau Mock (simulasi untuk pengembangan)."""
from __future__ import annotations

import hashlib
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np
import pandas as pd

from .config import SYMBOLS, Settings
from .models import Candle, Tick

TIMEFRAME_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


class DataSourceError(RuntimeError):
    """Kesalahan saat mengambil data dari sumber harga."""


class DataSource(ABC):
    name: str

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def shutdown(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def account_info(self) -> dict | None: ...

    @abstractmethod
    def get_candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]: ...

    @abstractmethod
    def get_tick(self, symbol: str) -> Tick: ...

    # --- Trading -------------------------------------------------------------
    @abstractmethod
    def symbol_spec(self, symbol: str) -> dict:
        """volume_min, volume_max, volume_step, stops_level (dalam harga), digits."""

    @abstractmethod
    def send_market_order(self, symbol: str, side: str, volume: float, sl: float, tp: float,
                          comment: str) -> dict: ...

    @abstractmethod
    def positions(self) -> list[dict]: ...

    @abstractmethod
    def close_position(self, ticket: int) -> dict: ...

    def get_frame(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        """Candle sebagai DataFrame (untuk analisis & backtest)."""
        rows = [c.model_dump() for c in self.get_candles(symbol, timeframe, count)]
        return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# MetaTrader 5
# ---------------------------------------------------------------------------
class MT5Source(DataSource):
    """Koneksi ke terminal MT5 via library resmi `MetaTrader5` (Windows saja)."""

    name = "mt5"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError as exc:  # pragma: no cover - tergantung OS
            raise DataSourceError(
                "Library MetaTrader5 tidak ditemukan. Library ini hanya berjalan di Windows. "
                "Jalankan `pip install MetaTrader5` di Windows, atau pakai DATA_SOURCE=mock."
            ) from exc
        self._mt5 = mt5
        self._lock = threading.Lock()  # library MT5 tidak thread-safe
        self._connected = False

    def _tf(self, timeframe: str):
        mapping = {
            "M1": self._mt5.TIMEFRAME_M1,
            "M5": self._mt5.TIMEFRAME_M5,
            "M15": self._mt5.TIMEFRAME_M15,
            "M30": self._mt5.TIMEFRAME_M30,
            "H1": self._mt5.TIMEFRAME_H1,
            "H4": self._mt5.TIMEFRAME_H4,
            "D1": self._mt5.TIMEFRAME_D1,
        }
        if timeframe not in mapping:
            raise DataSourceError(f"Timeframe tidak dikenal: {timeframe}")
        return mapping[timeframe]

    def connect(self) -> None:
        s = self.settings
        kwargs: dict = {}
        if s.mt5_path:
            kwargs["path"] = s.mt5_path
        if s.mt5_login:
            kwargs.update(login=int(s.mt5_login), password=s.mt5_password or "", server=s.mt5_server or "")
        with self._lock:
            if not self._mt5.initialize(**kwargs):
                code, msg = self._mt5.last_error()
                raise DataSourceError(f"Gagal terhubung ke MT5 ({code}): {msg}")
            self._connected = True

    def shutdown(self) -> None:
        with self._lock:
            self._mt5.shutdown()
            self._connected = False

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected and self._mt5.terminal_info() is not None

    def account_info(self) -> dict | None:
        with self._lock:
            info = self._mt5.account_info()
        if info is None:
            return None
        d = info._asdict()
        return {k: d.get(k) for k in ("login", "server", "currency", "balance", "equity", "leverage", "trade_mode")}

    def _ensure_symbol(self, broker_symbol: str) -> None:
        if not self._mt5.symbol_select(broker_symbol, True):
            raise DataSourceError(
                f"Simbol '{broker_symbol}' tidak tersedia di broker. "
                "Cek nama persisnya di Market Watch lalu atur SYMBOL_SUFFIX / SYMBOL_MAP."
            )

    def get_candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]:
        bsym = self.settings.broker_symbol(symbol)
        tf = self._tf(timeframe)
        with self._lock:
            self._ensure_symbol(bsym)
            rates = self._mt5.copy_rates_from_pos(bsym, tf, 0, count)
            if rates is None:
                code, msg = self._mt5.last_error()
                raise DataSourceError(f"Gagal mengambil candle {bsym} {timeframe} ({code}): {msg}")
        return [
            Candle(
                time=datetime.fromtimestamp(int(r["time"]), tz=timezone.utc),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                tick_volume=int(r["tick_volume"]),
                spread=int(r["spread"]),
            )
            for r in rates
        ]

    def get_tick(self, symbol: str) -> Tick:
        bsym = self.settings.broker_symbol(symbol)
        with self._lock:
            self._ensure_symbol(bsym)
            t = self._mt5.symbol_info_tick(bsym)
            info = self._mt5.symbol_info(bsym)
        if t is None or info is None:
            raise DataSourceError(f"Tidak ada harga untuk {bsym}")
        return Tick(
            symbol=symbol,
            broker_symbol=bsym,
            time=datetime.fromtimestamp(int(t.time), tz=timezone.utc),
            bid=float(t.bid),
            ask=float(t.ask),
            spread_points=round((t.ask - t.bid) / info.point, 1),
        )

    # --- Trading -------------------------------------------------------------
    def symbol_spec(self, symbol: str) -> dict:
        bsym = self.settings.broker_symbol(symbol)
        with self._lock:
            self._ensure_symbol(bsym)
            info = self._mt5.symbol_info(bsym)
        if info is None:
            raise DataSourceError(f"Info simbol {bsym} tidak tersedia")
        return {
            "volume_min": float(info.volume_min),
            "volume_max": float(info.volume_max),
            "volume_step": float(info.volume_step),
            "stops_level": float(info.trade_stops_level) * float(info.point),
            "digits": int(info.digits),
        }

    def _filling(self, info) -> int:
        mode = int(getattr(info, "filling_mode", 0))
        if mode & 1:
            return self._mt5.ORDER_FILLING_FOK
        if mode & 2:
            return self._mt5.ORDER_FILLING_IOC
        return self._mt5.ORDER_FILLING_RETURN

    def _send(self, request: dict) -> dict:
        result = self._mt5.order_send(request)
        if result is None:
            code, msg = self._mt5.last_error()
            raise DataSourceError(f"order_send gagal ({code}): {msg}")
        if result.retcode not in (self._mt5.TRADE_RETCODE_DONE, self._mt5.TRADE_RETCODE_PLACED):
            raise DataSourceError(f"Order ditolak broker (retcode {result.retcode}): {result.comment}")
        return {"order": int(result.order), "deal": int(result.deal), "volume": float(result.volume),
                "price": float(result.price), "retcode": int(result.retcode), "comment": result.comment}

    def _check_trade_allowed(self) -> None:
        term = self._mt5.terminal_info()
        if term is not None and not term.trade_allowed:
            raise DataSourceError(
                "Algo Trading di terminal MT5 belum aktif. Klik tombol 'Algo Trading' di toolbar MT5."
            )

    def send_market_order(self, symbol: str, side: str, volume: float, sl: float, tp: float,
                          comment: str) -> dict:
        bsym = self.settings.broker_symbol(symbol)
        with self._lock:
            self._check_trade_allowed()
            self._ensure_symbol(bsym)
            info = self._mt5.symbol_info(bsym)
            tick = self._mt5.symbol_info_tick(bsym)
            if info is None or tick is None:
                raise DataSourceError(f"Tidak ada harga untuk {bsym}")
            buy = side == "BUY"
            request = {
                "action": self._mt5.TRADE_ACTION_DEAL,
                "symbol": bsym,
                "volume": float(volume),
                "type": self._mt5.ORDER_TYPE_BUY if buy else self._mt5.ORDER_TYPE_SELL,
                "price": float(tick.ask if buy else tick.bid),
                "sl": float(sl),
                "tp": float(tp),
                "deviation": int(self.settings.trade_deviation_points),
                "magic": int(self.settings.magic_number),
                "comment": comment[:31],
                "type_time": self._mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(info),
            }
            return self._send(request)

    def positions(self) -> list[dict]:
        with self._lock:
            rows = self._mt5.positions_get() or ()
        reverse = {self.settings.broker_symbol(s): s for s in SYMBOLS}
        out = []
        for p in rows:
            out.append({
                "ticket": int(p.ticket),
                "symbol": reverse.get(p.symbol, p.symbol),
                "broker_symbol": p.symbol,
                "side": "BUY" if p.type == 0 else "SELL",
                "volume": float(p.volume),
                "price_open": float(p.price_open),
                "price_current": float(p.price_current),
                "sl": float(p.sl),
                "tp": float(p.tp),
                "profit": float(p.profit),
                "time": datetime.fromtimestamp(int(p.time), tz=timezone.utc).isoformat(),
                "from_dashboard": int(p.magic) == int(self.settings.magic_number),
            })
        return out

    def close_position(self, ticket: int) -> dict:
        with self._lock:
            self._check_trade_allowed()
            found = self._mt5.positions_get(ticket=int(ticket))
            if not found:
                raise DataSourceError(f"Posisi {ticket} tidak ditemukan")
            p = found[0]
            info = self._mt5.symbol_info(p.symbol)
            tick = self._mt5.symbol_info_tick(p.symbol)
            buy = p.type == 0
            request = {
                "action": self._mt5.TRADE_ACTION_DEAL,
                "symbol": p.symbol,
                "volume": float(p.volume),
                "type": self._mt5.ORDER_TYPE_SELL if buy else self._mt5.ORDER_TYPE_BUY,
                "position": int(ticket),
                "price": float(tick.bid if buy else tick.ask),
                "deviation": int(self.settings.trade_deviation_points),
                "magic": int(self.settings.magic_number),
                "comment": "close from dashboard",
                "type_time": self._mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(info),
            }
            return self._send(request)


# ---------------------------------------------------------------------------
# Mock (simulasi)
# ---------------------------------------------------------------------------
_MOCK_BASE = {
    # symbol: (harga awal, volatilitas per candle M15 relatif, digit, spread harga)
    "XAUUSD": (2650.0, 0.0015, 2, 0.25),
    "BTCUSD": (90000.0, 0.004, 2, 15.0),
    "ETHUSD": (3200.0, 0.005, 2, 1.5),
    "EURUSD": (1.0550, 0.0007, 5, 0.00008),
    "GBPUSD": (1.2650, 0.0008, 5, 0.00012),
    "USDJPY": (154.50, 0.0007, 3, 0.012),
    "AUDUSD": (0.6480, 0.0008, 5, 0.0001),
}


# Panjang histori M15 simulasi (~250 hari). H1/H4/D1 dibentuk dari agregasi M15,
# sehingga semua timeframe konsisten satu sama lain (seperti data broker asli).
MOCK_M15_HISTORY = 24_000


@lru_cache(maxsize=32)
def _mock_m15(symbol: str, last_open_ts: int) -> "pd.DataFrame":
    price, vol, digits, spread = _MOCK_BASE[symbol]
    seed = int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    n = MOCK_M15_HISTORY
    # drift berganti tiap ~200 candle supaya ada fase tren naik/turun
    regimes = rng.uniform(-0.12, 0.12, size=n // 200 + 1) * vol
    drift = np.repeat(regimes, 200)[:n]
    rets = drift + rng.normal(0, vol, size=n)
    close = np.exp(np.cumsum(rets))
    close *= price / close[-1]  # harga terakhir = harga acuan, agar angkanya realistis
    open_ = np.concatenate([[close[0] * np.exp(-rets[0])], close[:-1]])
    wick_hi = np.abs(rng.normal(0, vol / 2, size=n))
    wick_lo = np.abs(rng.normal(0, vol / 2, size=n))
    high = np.maximum(open_, close) * (1 + wick_hi)
    low = np.minimum(open_, close) * (1 - wick_lo)
    times = pd.to_datetime(last_open_ts - 900 * np.arange(n - 1, -1, -1), unit="s", utc=True)
    return pd.DataFrame({
        "time": times,
        "open": open_.round(digits), "high": high.round(digits),
        "low": low.round(digits), "close": close.round(digits),
        "tick_volume": rng.integers(100, 5000, size=n),
        "spread": int(round(spread * 10 ** digits)),
    })


class MockSource(DataSource):
    """Data harga simulasi yang deterministik, agar dashboard bisa dicoba tanpa MT5."""

    name = "mock"

    def __init__(self, settings: Settings, now: datetime | None = None):
        super().__init__(settings)
        self._connected = False
        self._now = now
        self._positions: dict[int, dict] = {}
        self._ticket = 1000

    def _current_time(self) -> datetime:
        return self._now or datetime.now(timezone.utc)

    def connect(self) -> None:
        self._connected = True

    def shutdown(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def account_info(self) -> dict | None:
        return {"login": 0, "server": "MOCK-Demo", "currency": "USD", "balance": 10000.0,
                "equity": 10000.0, "leverage": 100, "trade_mode": 0}

    def _base(self, symbol: str):
        if symbol not in _MOCK_BASE:
            raise DataSourceError(f"Simbol tidak dikenal: {symbol}")
        return _MOCK_BASE[symbol]

    def frame(self, symbol: str, timeframe: str) -> pd.DataFrame:
        self._base(symbol)
        minutes = TIMEFRAME_MINUTES.get(timeframe)
        if minutes is None or minutes < 15:
            raise DataSourceError(f"Timeframe {timeframe} tidak didukung di mode simulasi")
        ts = int(self._current_time().timestamp())
        m15 = _mock_m15(symbol, ts - ts % 900)
        if minutes == 15:
            return m15
        key = m15["time"].dt.floor(f"{minutes}min")
        g = m15.groupby(key, sort=True)
        out = pd.DataFrame({
            "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
            "close": g["close"].last(), "tick_volume": g["tick_volume"].sum(), "spread": g["spread"].first(),
        }).reset_index(names="time")
        return out.iloc[1:].reset_index(drop=True)  # candle pertama bisa tidak lengkap

    def get_frame(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        return self.frame(symbol, timeframe).tail(count).reset_index(drop=True)

    def get_candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]:
        df = self.frame(symbol, timeframe).tail(count)
        return [Candle(**r) for r in df.to_dict("records")]

    def get_tick(self, symbol: str) -> Tick:
        _, _, digits, spread = self._base(symbol)
        last = self.get_candles(symbol, "M15", 1)[-1]
        bid = last.close
        return Tick(
            symbol=symbol,
            broker_symbol=self.settings.broker_symbol(symbol),
            time=self._current_time(),
            bid=bid,
            ask=round(bid + spread, digits),
            spread_points=round(spread * 10 ** digits, 1),
        )

    # --- Trading (simulasi) ---------------------------------------------------
    _CONTRACT = {"XAUUSD": 100, "BTCUSD": 1, "ETHUSD": 1, "EURUSD": 100_000, "GBPUSD": 100_000,
                 "USDJPY": 100_000, "AUDUSD": 100_000}

    def symbol_spec(self, symbol: str) -> dict:
        _, _, digits, spread = self._base(symbol)
        return {"volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
                "stops_level": spread, "digits": digits}

    def send_market_order(self, symbol: str, side: str, volume: float, sl: float, tp: float,
                          comment: str) -> dict:
        tk = self.get_tick(symbol)
        price = tk.ask if side == "BUY" else tk.bid
        self._ticket += 1
        self._positions[self._ticket] = {
            "ticket": self._ticket, "symbol": symbol, "broker_symbol": tk.broker_symbol, "side": side,
            "volume": volume, "price_open": price, "sl": sl, "tp": tp,
            "time": self._current_time().isoformat(), "comment": comment,
        }
        return {"order": self._ticket, "deal": self._ticket, "volume": volume, "price": price,
                "retcode": 10009, "comment": "Simulasi"}

    def _with_profit(self, p: dict) -> dict:
        tk = self.get_tick(p["symbol"])
        current = tk.bid if p["side"] == "BUY" else tk.ask
        diff = (current - p["price_open"]) * (1 if p["side"] == "BUY" else -1)
        profit = diff * p["volume"] * self._CONTRACT[p["symbol"]]
        if p["symbol"] == "USDJPY":
            profit /= current
        return {**p, "price_current": current, "profit": round(profit, 2), "from_dashboard": True}

    def positions(self) -> list[dict]:
        return [self._with_profit(p) for p in self._positions.values()]

    def close_position(self, ticket: int) -> dict:
        p = self._positions.pop(int(ticket), None)
        if p is None:
            raise DataSourceError(f"Posisi {ticket} tidak ditemukan")
        closed = self._with_profit(p)
        return {"order": int(ticket), "deal": int(ticket), "volume": p["volume"],
                "price": closed["price_current"], "retcode": 10009, "comment": "Simulasi", "profit": closed["profit"]}


def create_source(settings: Settings) -> DataSource:
    if settings.data_source == "mt5":
        return MT5Source(settings)
    return MockSource(settings)
