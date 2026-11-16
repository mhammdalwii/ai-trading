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


def _trading_mt5(fake_mt5):
    m = fake_mt5
    m.TRADE_ACTION_DEAL, m.ORDER_TYPE_BUY, m.ORDER_TYPE_SELL = 1, 0, 1
    m.ORDER_TIME_GTC, m.ORDER_FILLING_FOK, m.ORDER_FILLING_IOC, m.ORDER_FILLING_RETURN = 0, 0, 1, 2
    m.TRADE_RETCODE_DONE, m.TRADE_RETCODE_PLACED = 10009, 10008
    Info = namedtuple("Info", "point volume_min volume_max volume_step trade_stops_level digits filling_mode")
    m.symbol_info = lambda s: Info(0.00001, 0.01, 100, 0.01, 10, 5, 2)
    Term = namedtuple("Term", "trade_allowed")
    m.terminal_info = lambda: Term(True)
    R = namedtuple("R", "retcode order deal volume price comment")
    m.sent = []
    m.order_send = lambda req: (m.sent.append(req), R(10009, 55, 66, req["volume"], req["price"], "Request executed"))[1]
    return m


def test_send_market_order_request(fake_mt5):
    m = _trading_mt5(fake_mt5)
    src = make()
    src.connect()
    assert src.symbol_spec("EURUSD")["stops_level"] == pytest.approx(0.0001)
    res = src.send_market_order("EURUSD", "BUY", 0.1, 1.05, 1.06, "AI BUY 72%")
    req = m.sent[0]
    assert req["symbol"] == "EURUSD.m" and req["type"] == m.ORDER_TYPE_BUY and req["price"] == 1.05508
    assert req["type_filling"] == m.ORDER_FILLING_IOC and req["magic"] == 20261116
    assert res["order"] == 55


def test_order_rejected_by_broker(fake_mt5):
    m = _trading_mt5(fake_mt5)
    R = namedtuple("R", "retcode order deal volume price comment")
    m.order_send = lambda req: R(10019, 0, 0, 0, 0, "No money")
    src = make()
    src.connect()
    with pytest.raises(DataSourceError, match="No money"):
        src.send_market_order("EURUSD", "SELL", 0.1, 1.06, 1.05, "x")


def test_algo_trading_disabled(fake_mt5):
    m = _trading_mt5(fake_mt5)
    Term = namedtuple("Term", "trade_allowed")
    m.terminal_info = lambda: Term(False)
    src = make()
    src._connected = True
    with pytest.raises(DataSourceError, match="Algo Trading"):
        src.send_market_order("EURUSD", "BUY", 0.1, 1.05, 1.06, "x")
