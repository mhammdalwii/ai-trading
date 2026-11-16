"""Tes MT5Source memakai modul MetaTrader5 palsu (library asli hanya ada di Windows)."""
import sys
import types
from collections import namedtuple

import numpy as np
import pytest

from app.config import Settings
from app.data_sources import DataSourceError, MT5Source


@pytest.fixture
def fake_mt5(monkeypatch):
    m = types.ModuleType("MetaTrader5")
    for i, tf in enumerate(["M1", "M5", "M15", "M30", "H1", "H4", "D1"]):
        setattr(m, f"TIMEFRAME_{tf}", i)
    m.calls = {}
    m.available = {"XAUUSD.m", "EURUSD.m"}
    m.initialize = lambda **kw: m.calls.setdefault("init", kw) is not None
    m.shutdown = lambda: None
    m.last_error = lambda: (1, "ok")
    m.terminal_info = lambda: object()
    m.symbol_select = lambda s, on: s in m.available
    rates = np.array(
        [(1763305200, 1.05, 1.06, 1.04, 1.055, 100, 8, 0)],
        dtype=[("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
               ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")],
    )
    m.copy_rates_from_pos = lambda s, tf, start, n: rates
    T = namedtuple("T", "time bid ask")
    I = namedtuple("I", "point")
    m.symbol_info_tick = lambda s: T(1763305200, 1.05500, 1.05508)
    m.symbol_info = lambda s: I(0.00001)
    A = namedtuple("A", "login server currency balance equity leverage trade_mode")
    m.account_info = lambda: A(123, "Demo", "USD", 1000.0, 1000.0, 100, 0)
    monkeypatch.setitem(sys.modules, "MetaTrader5", m)
    return m


def make(**kw):
    return MT5Source(Settings(data_source="mt5", symbol_suffix=".m", mt5_login=123, mt5_password="x", mt5_server="Demo", **kw))


def test_connect_passes_credentials(fake_mt5):
    src = make()
    src.connect()
    assert fake_mt5.calls["init"] == {"login": 123, "password": "x", "server": "Demo"}
    assert src.is_connected()
    assert src.account_info()["login"] == 123


def test_candles_and_tick(fake_mt5):
    src = make()
    src.connect()
    c = src.get_candles("EURUSD", "M15", 1)[0]
    assert c.close == 1.055 and c.spread == 8
    t = src.get_tick("EURUSD")
    assert t.broker_symbol == "EURUSD.m" and t.spread_points == 8.0


def test_missing_symbol_has_helpful_error(fake_mt5):
    src = make()
    src.connect()
    with pytest.raises(DataSourceError, match="SYMBOL_SUFFIX"):
        src.get_tick("BTCUSD")


def test_connect_failure(fake_mt5):
    fake_mt5.initialize = lambda **kw: False
    fake_mt5.last_error = lambda: (-6, "Authorization failed")
    with pytest.raises(DataSourceError, match="Authorization failed"):
        make().connect()
