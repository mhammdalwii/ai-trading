"""Konfigurasi aplikasi, dibaca dari environment / file .env."""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 7 pair utama sesuai spesifikasi
SYMBOLS: tuple[str, ...] = (
    "XAUUSD",
    "BTCUSD",
    "ETHUSD",
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
)

# Timeframe yang dipakai strategi multi-timeframe
TIMEFRAMES: tuple[str, ...] = ("H4", "H1", "M15")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    data_source: Literal["mt5", "mock"] = "mock"

    mt5_login: int | None = None
    mt5_password: str | None = None
    mt5_server: str | None = None
    mt5_path: str | None = None

    # Urutan sumber kalender berita, dipisah koma: myfxbook, forexfactory, none
    news_sources: str = "myfxbook,forexfactory"
    news_window_minutes: int = 30

    # Eksekusi order
    default_lot: float = 0.01
    max_lot: float = 1.0  # batas keras: order di atas ini selalu ditolak
    trade_deviation_points: int = 20  # toleransi slippage
    magic_number: int = 20261116  # penanda order dari dashboard ini di MT5
    # Jika diisi, semua aksi trading wajib menyertakan token ini (disarankan saat dipasang di VPS)
    dashboard_token: str = ""
    order_log_path: str = "data/orders.jsonl"

    # Auto-Trigger (langkah 4). Saklar selalu OFF saat aplikasi baru dijalankan.
    auto_min_confidence: float = 80.0  # eksekusi hanya jika skor DI ATAS nilai ini
    auto_lot: float = 0.01
    auto_interval_seconds: int = 60
    auto_max_open_positions: int = 3
    auto_cooldown_minutes: int = 60  # jeda per pair setelah auto-trade
    auto_max_trades_per_day: int = 5
    auto_max_daily_loss_percent: float = 3.0  # kill switch: auto mati jika equity turun segini dari awal hari
    auto_max_spread_ratio: float = 0.25  # lewati jika spread > 25% jarak SL
    auto_symbols: str = ",".join(SYMBOLS)
    allow_auto_on_real_account: bool = False  # akun real ditolak kecuali diizinkan eksplisit

    symbol_suffix: str = ""
    symbol_map: dict[str, str] = Field(default_factory=dict)

    def broker_symbol(self, symbol: str) -> str:
        """Nama simbol standar (XAUUSD) -> nama simbol di broker (XAUUSD.m)."""
        if symbol in self.symbol_map:
            return self.symbol_map[symbol]
        return f"{symbol}{self.symbol_suffix}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
