from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import SYMBOLS, Settings
from app.data_sources import MockSource
from app.main import app

FIXED_NOW = datetime(2026, 11, 16, 15, 7, tzinfo=timezone.utc)


@pytest.fixture
def client():
    source = MockSource(Settings(data_source="mock", symbol_suffix=".m", symbol_map={"XAUUSD": "GOLD"}), now=FIXED_NOW)
    source.connect()
    app.state.source = source
    with TestClient(app) as c:
        yield c
    app.state.source = None


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True and body["data_source"] == "mock"


def test_symbols_mapping(client):
    rows = {r["symbol"]: r["broker_symbol"] for r in client.get("/api/symbols").json()}
    assert set(rows) == set(SYMBOLS)
    assert rows["XAUUSD"] == "GOLD"
    assert rows["EURUSD"] == "EURUSD.m"


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_candles_valid(client, symbol):
    r = client.get(f"/api/candles/{symbol}", params={"timeframe": "M15", "count": 200})
    assert r.status_code == 200
    candles = r.json()["candles"]
    assert len(candles) == 200
    for c in candles:
        assert c["low"] <= min(c["open"], c["close"]) <= max(c["open"], c["close"]) <= c["high"]
    times = [c["time"] for c in candles]
    assert times == sorted(times)


def test_last_candle_aligned_to_timeframe(client):
    last = client.get("/api/candles/EURUSD", params={"timeframe": "H4", "count": 5}).json()["candles"][-1]
    assert last["time"].startswith("2026-11-16T12:00:00")


def test_tick_matches_last_close(client):
    tick = client.get("/api/tick/xauusd").json()
    last = client.get("/api/candles/XAUUSD", params={"timeframe": "M15", "count": 300}).json()["candles"][-1]
    assert tick["bid"] == last["close"]
    assert tick["ask"] > tick["bid"]


def test_all_ticks(client):
    assert len(client.get("/api/ticks").json()) == 7


def test_mtf(client):
    body = client.get("/api/mtf/BTCUSD", params={"count": 100}).json()
    assert set(body["timeframes"]) == {"H4", "H1", "M15"}
    assert all(len(v) == 100 for v in body["timeframes"].values())


def test_unknown_symbol_and_timeframe(client):
    assert client.get("/api/tick/DOGEUSD").status_code == 404
    assert client.get("/api/candles/EURUSD", params={"timeframe": "W1"}).status_code == 400
    assert client.get("/api/candles/EURUSD", params={"count": 5000}).status_code == 422


def test_disconnected_returns_503(client):
    app.state.source.shutdown()
    assert client.get("/api/tick/EURUSD").status_code == 503
    assert client.get("/api/health").json()["connected"] is False


# --- Langkah 2 ---------------------------------------------------------------
from datetime import timedelta  # noqa: E402

from app.news import NewsEvent, NewsFilter, StaticProvider  # noqa: E402


@pytest.fixture
def client_news():
    source = MockSource(Settings(data_source="mock"), now=FIXED_NOW)
    source.connect()
    app.state.source = source
    app.state.news = NewsFilter([StaticProvider([NewsEvent(FIXED_NOW + timedelta(minutes=10), "JPY", "BoJ Rate", "High")])])
    with TestClient(app) as c:
        yield c
    app.state.source = None
    app.state.news = None


def test_analysis_one(client_news):
    r = client_news.get("/api/analysis/EURUSD")
    assert r.status_code == 200
    body = r.json()
    assert body["signal"] in ("BUY", "SELL", "WAIT_AND_SEE", "NO_TRADE")
    assert 0 <= body["confidence"] <= 100
    assert len(body["factors"]) == 7


def test_analysis_news_blocks_usdjpy(client_news):
    body = client_news.get("/api/analysis/USDJPY").json()
    assert body["context"]["news"]["blocked"] is True
    assert body["signal"] in ("WAIT_AND_SEE", "NO_TRADE")
    assert body["auto_trade_eligible"] is False


def test_analysis_all_sorted(client_news):
    rows = client_news.get("/api/analysis").json()
    assert len(rows) == 7
    scores = [r["confidence"] for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_news_endpoint(client_news, monkeypatch):
    monkeypatch.setattr("app.main.now_utc", lambda: FIXED_NOW)
    body = client_news.get("/api/news").json()
    assert body["events"][0]["title"] == "BoJ Rate"
    assert body["blocked_symbols"] == ["USDJPY"]


def test_backtest_endpoint(client_news):
    r = client_news.get("/api/backtest/XAUUSD", params={"days": 5})
    assert r.status_code == 200
    assert r.json()["symbol"] == "XAUUSD"
