"""API backend AI Trading Dashboard (Langkah 1: data harga dari MT5)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from datetime import datetime, timedelta, timezone

from .analysis import analyze
from .backtest import run_backtest
from .config import SYMBOLS, TIMEFRAMES, Settings, get_settings
from .data_sources import TIMEFRAME_MINUTES, DataSource, DataSourceError, create_source
from .models import CandleSet, HealthStatus, MultiTimeframe, Tick
from .news import ForexFactoryProvider, MyfxbookProvider, NewsFilter, NewsProvider

log = logging.getLogger("trading")

MAX_CANDLES = 1000
ANALYSIS_BARS = 300


def create_news_filter(settings: Settings) -> NewsFilter:
    available = {"myfxbook": MyfxbookProvider, "forexfactory": ForexFactoryProvider}
    providers: list[NewsProvider] = [
        available[name.strip()]() for name in settings.news_sources.split(",") if name.strip() in available
    ]
    return NewsFilter(providers, window_minutes=settings.news_window_minutes)


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
    if getattr(app.state, "news", None) is None:
        app.state.news = create_news_filter(get_settings())
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


def get_news(request: Request) -> NewsFilter:
    return request.app.state.news


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


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


# ---------------------------------------------------------------------------
# Langkah 2: analisis, berita, backtest
# ---------------------------------------------------------------------------
def _analyze_symbol(symbol: str, source: DataSource, news: NewsFilter) -> dict:
    frames = {tf: source.get_frame(symbol, tf, ANALYSIS_BARS) for tf in TIMEFRAMES}
    tk = source.get_tick(symbol)
    result = analyze(symbol, frames, tk.bid, tk.ask, news.check(symbol, tk.time))
    out = result.as_dict()
    out["time"] = tk.time.isoformat()
    out["broker_symbol"] = tk.broker_symbol
    return out


@app.get("/api/analysis/{symbol}")
def analysis(symbol: str, source: DataSource = Depends(get_source), news: NewsFilter = Depends(get_news)):
    """Sinyal lengkap satu pair: arah, Entry, SL, TP1/TP2, Confidence Score, dan alasan tiap skor."""
    symbol = _check_symbol(symbol)
    _require_connected(source)
    try:
        return _analyze_symbol(symbol, source, news)
    except DataSourceError as exc:
        raise HTTPException(502, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/analysis")
def analysis_all(source: DataSource = Depends(get_source), news: NewsFilter = Depends(get_news)):
    """Ringkasan sinyal ke-7 pair, diurutkan dari Confidence Score tertinggi."""
    _require_connected(source)
    rows = []
    for s in SYMBOLS:
        try:
            r = _analyze_symbol(s, source, news)
            rows.append({k: r[k] for k in ("symbol", "signal", "direction", "confidence", "entry", "stop_loss",
                                           "take_profit_1", "take_profit_2", "auto_trade_eligible", "warnings")})
        except (DataSourceError, ValueError) as exc:
            rows.append({"symbol": s, "signal": "ERROR", "confidence": 0, "warnings": [str(exc)]})
    return sorted(rows, key=lambda r: r["confidence"], reverse=True)


@app.get("/api/news")
def news_calendar(news: NewsFilter = Depends(get_news), hours: int = Query(24, ge=1, le=168)):
    """Jadwal berita high impact untuk mata uang 7 pair dalam beberapa jam ke depan."""
    now = now_utc()
    currencies = {"USD", "EUR", "GBP", "JPY", "AUD"}
    events = [
        e.as_dict() for e in sorted(news.events(), key=lambda e: e.time)
        if e.impact == "High" and e.currency in currencies and now - news.window <= e.time <= now + timedelta(hours=hours)
    ]
    return {"source": news.source, "error": news.error, "events": events,
            "blocked_symbols": [s for s in SYMBOLS if news.check(s, now)["blocked"]]}


@app.get("/api/backtest/{symbol}")
def backtest(symbol: str, days: int = Query(30, ge=1, le=60), source: DataSource = Depends(get_source)):
    """Uji strategi di data historis (bisa butuh beberapa detik)."""
    symbol = _check_symbol(symbol)
    _require_connected(source)
    m15_bars = days * 96 + 400
    try:
        h4 = source.get_frame(symbol, "H4", m15_bars // 16 + 400)
        h1 = source.get_frame(symbol, "H1", m15_bars // 4 + 400)
        m15 = source.get_frame(symbol, "M15", m15_bars)
    except DataSourceError as exc:
        raise HTTPException(502, str(exc)) from exc
    return run_backtest(symbol, h4, h1, m15, days=days)
