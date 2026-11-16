from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.data_sources import MockSource
from app.main import app
from app.news import NewsFilter, StaticProvider
from app.trading import TradeRejected, normalize_volume, validate_stops

NOW = datetime(2026, 11, 16, 15, 7, tzinfo=timezone.utc)
SPEC = {"volume_min": 0.01, "volume_max": 50, "volume_step": 0.01, "stops_level": 0.0, "digits": 5}


def test_normalize_volume():
    assert normalize_volume(0.057, SPEC, 1.0) == 0.05
    assert normalize_volume(0.1, SPEC, 1.0) == 0.1
    with pytest.raises(TradeRejected, match="minimum"):
        normalize_volume(0.001, SPEC, 1.0)
    with pytest.raises(TradeRejected, match="MAX_LOT"):
        normalize_volume(2, SPEC, 1.0)


def test_validate_stops():
    validate_stops("BUY", 1.1, 1.09, 1.12, 0.0)
    validate_stops("SELL", 1.1, 1.11, 1.08, 0.0)
    with pytest.raises(TradeRejected):
        validate_stops("BUY", 1.1, 1.11, 1.12, 0.0)
    with pytest.raises(TradeRejected, match="terlalu dekat"):
        validate_stops("BUY", 1.1, 1.0999, 1.12, 0.001)


def _fake_analysis(signal="BUY"):
    buy = signal == "BUY"
    return {"symbol": "EURUSD", "signal": signal, "confidence": 72.5, "direction": "bullish" if buy else "bearish",
            "entry": 1.05508, "stop_loss": 1.05 if buy else 1.06, "take_profit_1": 1.064 if buy else 1.046,
            "take_profit_2": 1.07 if buy else 1.04, "time": NOW.isoformat(), "warnings": [], "factors": []}


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(data_source="mock", order_log_path=str(tmp_path / "orders.jsonl"), max_lot=1.0)
    source = MockSource(settings, now=NOW)
    source.connect()
    app.state.source, app.state.settings = source, settings
    app.state.news = NewsFilter([StaticProvider([])])
    state = {"signal": "BUY"}
    monkeypatch.setattr("app.main._analyze_symbol", lambda sym, src, news: _fake_analysis(state["signal"]))
    with TestClient(app) as c:
        c.state = state
        yield c
    app.state.source = app.state.settings = app.state.news = None


def test_execute_opens_position_and_logs(client):
    r = client.post("/api/execute/EURUSD", json={"volume": 0.1, "expected_signal": "BUY", "take_profit": "tp1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "filled" and body["volume"] == 0.1 and body["tp"] == 1.064
    pos = client.get("/api/positions").json()
    assert len(pos) == 1 and pos[0]["side"] == "BUY" and pos[0]["sl"] == 1.05
    log = client.get("/api/orders/log").json()
    assert log[0]["status"] == "filled"

    ticket = pos[0]["ticket"]
    assert client.post(f"/api/positions/{ticket}/close").status_code == 200
    assert client.get("/api/positions").json() == []
    assert client.get("/api/orders/log").json()[0]["action"] == "close"


def test_execute_rejected_when_signal_changed(client):
    client.state["signal"] = "SELL"
    r = client.post("/api/execute/EURUSD", json={"expected_signal": "BUY"})
    assert r.status_code == 409 and "berubah" in r.json()["detail"]
    assert client.get("/api/positions").json() == []
    assert client.get("/api/orders/log").json()[0]["status"] == "rejected"


def test_execute_rejected_when_no_trade(client):
    client.state["signal"] = "WAIT_AND_SEE"
    r = client.post("/api/execute/EURUSD", json={"expected_signal": "BUY"})
    assert r.status_code == 409


def test_execute_rejects_oversized_lot(client):
    r = client.post("/api/execute/EURUSD", json={"volume": 5, "expected_signal": "BUY"})
    assert r.status_code == 409 and "MAX_LOT" in r.json()["detail"]
    assert client.get("/api/positions").json() == []


def test_sell_execution(client):
    client.state["signal"] = "SELL"
    r = client.post("/api/execute/EURUSD", json={"expected_signal": "SELL"})
    assert r.status_code == 200 and r.json()["volume"] == 0.01  # DEFAULT_LOT


def test_token_required(client):
    app.state.settings.dashboard_token = "rahasia"
    assert client.post("/api/execute/EURUSD", json={"expected_signal": "BUY"}).status_code == 401
    ok = client.post("/api/execute/EURUSD", json={"expected_signal": "BUY"}, headers={"X-Dashboard-Token": "rahasia"})
    assert ok.status_code == 200
    assert client.get("/api/config").json()["token_required"] is True


def test_close_unknown_position(client):
    assert client.post("/api/positions/999/close").status_code == 502


def test_dashboard_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Execute to MT5" in r.text
    assert client.get("/static/app.js").status_code == 200
