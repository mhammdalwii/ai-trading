from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.autotrade import AutoTrader
from app.config import Settings
from app.data_sources import MockSource
from app.main import app
from app.news import NewsFilter, StaticProvider

NOW = datetime(2026, 11, 16, 15, 7, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def analysis(symbol, signal="BUY", confidence=85.0, blocked=False, source=None):
    tk = source.get_tick(symbol)
    buy = signal == "BUY"
    entry = tk.ask if buy else tk.bid
    dist = entry * 0.004
    sign = 1 if buy else -1
    return {"symbol": symbol, "signal": signal, "confidence": confidence, "entry": entry,
            "stop_loss": entry - sign * dist, "take_profit_1": entry + sign * dist * 1.5,
            "take_profit_2": entry + sign * dist * 2, "context": {"news": {"blocked": blocked}}}


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_source="mock", order_log_path=str(tmp_path / "o.jsonl"), auto_symbols="EURUSD,XAUUSD,GBPUSD",
                        auto_max_open_positions=2, auto_max_trades_per_day=3)
    clock = Clock()
    source = MockSource(settings, now=NOW)
    source.connect()
    plan = {}  # symbol -> kwargs untuk analysis()
    fn = lambda sym: analysis(sym, source=source, **plan.get(sym, {"signal": "NO_TRADE", "confidence": 20}))  # noqa: E731
    auto = AutoTrader(source, settings, fn, clock=clock)
    return auto, source, plan, clock


def test_off_by_default_does_nothing(env):
    auto, source, plan, _ = env
    plan["EURUSD"] = {}
    assert auto.enabled is False
    assert auto.run_once() == []
    assert source.positions() == []


def test_executes_above_threshold_only(env):
    auto, source, plan, _ = env
    plan["EURUSD"] = {"confidence": 85}
    plan["XAUUSD"] = {"confidence": 80}  # tepat 80 -> tidak (harus DI ATAS 80)
    plan["GBPUSD"] = {"signal": "WAIT_AND_SEE", "confidence": 90}
    auto.set_enabled(True)
    orders = auto.run_once()
    assert [o["symbol"] for o in orders] == ["EURUSD"]
    assert orders[0]["trigger"] == "auto" and orders[0]["volume"] == 0.01
    assert "belum di atas" in auto.last_decisions["XAUUSD"]


def test_news_blocks(env):
    auto, source, plan, _ = env
    plan["EURUSD"] = {"blocked": True}
    auto.set_enabled(True)
    assert auto.run_once() == []
    assert "berita" in auto.last_decisions["EURUSD"]


def test_one_position_per_symbol_and_cooldown(env):
    auto, source, plan, clock = env
    plan["EURUSD"] = {}
    auto.set_enabled(True)
    assert len(auto.run_once()) == 1
    assert auto.run_once() == [] and "Cooldown" in auto.last_decisions["EURUSD"]
    auto.settings.auto_cooldown_minutes = 0
    assert auto.run_once() == [] and "Sudah ada posisi" in auto.last_decisions["EURUSD"]
    auto.settings.auto_cooldown_minutes = 60
    source.close_position(source.positions()[0]["ticket"])
    clock.now += timedelta(minutes=30)
    assert auto.run_once() == [] and "Cooldown" in auto.last_decisions["EURUSD"]
    clock.now += timedelta(minutes=31)
    assert len(auto.run_once()) == 1


def test_max_open_positions(env):
    auto, source, plan, _ = env
    plan.update({"EURUSD": {}, "XAUUSD": {}, "GBPUSD": {}})
    auto.set_enabled(True)
    assert len(auto.run_once()) == 2
    assert "Maksimal 2" in auto.last_decisions["GBPUSD"]


def test_max_trades_per_day_and_reset(env):
    auto, source, plan, clock = env
    plan.update({"EURUSD": {}, "XAUUSD": {}, "GBPUSD": {}})
    auto.settings.auto_max_open_positions = 10
    auto.settings.auto_max_trades_per_day = 2
    auto.set_enabled(True)
    assert len(auto.run_once()) == 2
    for p in source.positions():
        source.close_position(p["ticket"])
    clock.now += timedelta(hours=2)
    assert auto.run_once() == []
    assert "per hari" in auto.last_decisions["GBPUSD"]
    clock.now = NOW + timedelta(days=1)
    assert len(auto.run_once()) >= 1


def test_daily_loss_kill_switch(env, monkeypatch):
    auto, source, plan, _ = env
    plan["EURUSD"] = {}
    auto.set_enabled(True)
    auto.run_once()  # catat equity awal hari = 10000
    acc = source.account_info()
    monkeypatch.setattr(source, "account_info", lambda: {**acc, "equity": 9650.0})
    assert auto.run_once() == []
    assert auto.enabled is False and "rugi harian" in auto.disabled_reason


def test_real_account_refused(env, monkeypatch):
    auto, source, plan, _ = env
    acc = source.account_info()
    monkeypatch.setattr(source, "account_info", lambda: {**acc, "trade_mode": 2})
    with pytest.raises(PermissionError, match="REAL"):
        auto.set_enabled(True)
    auto.settings.allow_auto_on_real_account = True
    auto.set_enabled(True)
    assert auto.enabled


def test_wide_spread_skipped(env, monkeypatch):
    auto, source, plan, _ = env
    plan["EURUSD"] = {}
    auto.settings.auto_max_spread_ratio = 0.0001
    auto.set_enabled(True)
    assert auto.run_once() == []
    assert "Spread" in auto.last_decisions["EURUSD"]


def test_api_toggle(tmp_path):
    settings = Settings(data_source="mock", order_log_path=str(tmp_path / "o.jsonl"))
    source = MockSource(settings, now=NOW)
    source.connect()
    app.state.source, app.state.settings = source, settings
    app.state.news = NewsFilter([StaticProvider([])])
    with TestClient(app) as c:
        assert c.get("/api/auto").json()["enabled"] is False
        r = c.post("/api/auto", json={"enabled": True})
        assert r.status_code == 200 and r.json()["enabled"] is True
        assert c.get("/api/auto").json()["rules"]["min_confidence"] == 80
        assert c.post("/api/auto", json={"enabled": False}).json()["enabled"] is False
    app.state.source = app.state.settings = app.state.news = None
