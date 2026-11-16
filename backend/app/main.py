"""API backend AI Trading Dashboard (Langkah 1: data harga dari MT5)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import SYMBOLS, TIMEFRAMES, get_settings
from .data_sources import TIMEFRAME_MINUTES, DataSource, DataSourceError, create_source
from .models import CandleSet, HealthStatus, MultiTimeframe, Tick

log = logging.getLogger("trading")

MAX_CANDLES = 1000


@asynccontextmanager
async def lifespan(app: FastAPI):
    if getattr(app.state, "source", None) is None:
        settings = get_settings()
        source = create_source(settings)
        try:
            source.connect()
            log.info("Terhubung ke sumber data: %s", source.name)
        except DataSourceError as exc:
            log.error("%s", exc)
        app.state.source = source
    yield
    app.state.source.shutdown()


app = FastAPI(title="AI Trading Dashboard API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_source(request: Request) -> DataSource:
    return request.app.state.source


def _check_symbol(symbol: str) -> str:
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        raise HTTPException(404, f"Simbol '{symbol}' tidak didukung. Pilihan: {', '.join(SYMBOLS)}")
    return symbol


def _check_timeframe(timeframe: str) -> str:
    timeframe = timeframe.upper()
    if timeframe not in TIMEFRAME_MINUTES:
        raise HTTPException(400, f"Timeframe '{timeframe}' tidak dikenal. Pilihan: {', '.join(TIMEFRAME_MINUTES)}")
    return timeframe


def _require_connected(source: DataSource) -> None:
    if not source.is_connected():
        raise HTTPException(503, "Belum terhubung ke sumber data (MT5). Cek terminal MT5 dan file .env.")


@app.get("/api/health", response_model=HealthStatus)
def health(source: DataSource = Depends(get_source)):
    connected = source.is_connected()
    return HealthStatus(
        ok=connected,
        data_source=source.name,
        connected=connected,
        account=source.account_info() if connected else None,
        message=None if connected else "Tidak terhubung ke sumber data",
    )


@app.get("/api/symbols")
def symbols(source: DataSource = Depends(get_source)):
    return [{"symbol": s, "broker_symbol": source.settings.broker_symbol(s)} for s in SYMBOLS]


@app.get("/api/tick/{symbol}", response_model=Tick)
def tick(symbol: str, source: DataSource = Depends(get_source)):
    symbol = _check_symbol(symbol)
    _require_connected(source)
    try:
        return source.get_tick(symbol)
    except DataSourceError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/ticks", response_model=list[Tick])
def ticks(source: DataSource = Depends(get_source)):
    """Harga terkini untuk ke-7 pair."""
    _require_connected(source)
    try:
        return [source.get_tick(s) for s in SYMBOLS]
    except DataSourceError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/candles/{symbol}", response_model=CandleSet)
def candles(
    symbol: str,
    timeframe: str = Query("H1"),
    count: int = Query(300, ge=1, le=MAX_CANDLES),
    source: DataSource = Depends(get_source),
):
    symbol = _check_symbol(symbol)
    timeframe = _check_timeframe(timeframe)
    _require_connected(source)
    try:
        data = source.get_candles(symbol, timeframe, count)
    except DataSourceError as exc:
        raise HTTPException(502, str(exc)) from exc
    return CandleSet(symbol=symbol, broker_symbol=source.settings.broker_symbol(symbol), timeframe=timeframe, candles=data)


@app.get("/api/mtf/{symbol}", response_model=MultiTimeframe)
def multi_timeframe(
    symbol: str,
    count: int = Query(300, ge=1, le=MAX_CANDLES),
    source: DataSource = Depends(get_source),
):
    """Data H4, H1, dan M15 sekaligus: bahan untuk analisis confluence di langkah 2."""
    symbol = _check_symbol(symbol)
    _require_connected(source)
    try:
        return MultiTimeframe(
            symbol=symbol,
            broker_symbol=source.settings.broker_symbol(symbol),
            tick=source.get_tick(symbol),
            timeframes={tf: source.get_candles(symbol, tf, count) for tf in TIMEFRAMES},
        )
    except DataSourceError as exc:
        raise HTTPException(502, str(exc)) from exc
