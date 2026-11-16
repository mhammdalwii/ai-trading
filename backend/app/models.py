from datetime import datetime

from pydantic import BaseModel


class Candle(BaseModel):
    time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int = 0


class Tick(BaseModel):
    symbol: str
    broker_symbol: str
    time: datetime
    bid: float
    ask: float
    spread_points: float


class CandleSet(BaseModel):
    symbol: str
    broker_symbol: str
    timeframe: str
    candles: list[Candle]


class MultiTimeframe(BaseModel):
    """Data H4, H1, M15 sekaligus untuk satu pair (bahan analisis confluence)."""

    symbol: str
    broker_symbol: str
    tick: Tick
    timeframes: dict[str, list[Candle]]


class HealthStatus(BaseModel):
    ok: bool
    data_source: str
    connected: bool
    account: dict | None = None
    message: str | None = None
