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
