"""Tes jembatan RPyC ke MT5 (mode Docker/VPS Linux).

Server RPyC classic dijalankan di proses yang sama dengan modul MetaTrader5 palsu,
sehingga jalur lengkapnya teruji: MT5Source -> RemoteMT5 -> RPyC -> "MetaTrader5".
"""
import threading
import time

import numpy as np
import pytest
import rpyc
from rpyc.utils.server import ThreadedServer

from app.config import Settings
from app.data_sources import DataSourceError, MT5Source
from app.mt5_bridge import RemoteMT5
from test_mt5_source import _trading_mt5, fake_mt5  # noqa: F401  (fixture)


@pytest.fixture
def bridge_server(fake_mt5):  # noqa: F811
    # Konfigurasi sama dengan server mt5linux (rpyc classic / SlaveService)
    server = ThreadedServer(rpyc.SlaveService, hostname="127.0.0.1", port=0)
    t = threading.Thread(target=server.start, daemon=True)
    t.start()
    for _ in range(50):
        if server.active:
            break
        time.sleep(0.02)
    yield server
    server.close()


def make(port, **kw):
    return MT5Source(Settings(data_source="mt5", symbol_suffix=".m", mt5_login=123, mt5_password="x",
                              mt5_server="Demo", mt5_bridge_host="127.0.0.1", mt5_bridge_port=port, **kw))


def test_bridge_connect_candles_tick(fake_mt5, bridge_server):  # noqa: F811
    src = make(bridge_server.port)
    assert src.name == "mt5-bridge"
    src.connect()
    assert fake_mt5.calls["init"] == {"login": 123, "password": "x", "server": "Demo"}
    assert src.is_connected()
    acc = src.account_info()
    assert acc["login"] == 123 and acc["trade_mode"] == 0
    c = src.get_candles("EURUSD", "M15", 1)[0]
    assert c.close == 1.055 and c.spread == 8
    t = src.get_tick("EURUSD")
    assert t.broker_symbol == "EURUSD.m" and t.spread_points == 8.0
    src.shutdown()


def test_bridge_values_are_local_copies(fake_mt5, bridge_server):  # noqa: F811
    m = RemoteMT5("127.0.0.1", bridge_server.port)
    rates = m.copy_rates_from_pos("EURUSD.m", m.TIMEFRAME_M15, 0, 1)
    assert isinstance(rates, np.ndarray) and rates.dtype.names[0] == "time"
    assert rates[0]["close"] == 1.055
    tick = m.symbol_info_tick("EURUSD.m")
    assert type(tick).__module__ != "rpyc.core.netref" and tick._asdict()["ask"] == 1.05508
    assert m.last_error() == (1, "ok")
    assert isinstance(m.TIMEFRAME_H4, int)
    m.close()


def test_bridge_order_send_passes_plain_dict(fake_mt5, bridge_server):  # noqa: F811
    m = _trading_mt5(fake_mt5)
    src = make(bridge_server.port)
    src.connect()
    res = src.send_market_order("EURUSD", "BUY", 0.1, 1.05, 1.06, "AI BUY 72%")
    req = m.sent[0]
    assert type(req) is dict and req["symbol"] == "EURUSD.m" and req["type_filling"] == m.ORDER_FILLING_IOC
    assert res["order"] == 55 and res["price"] == 1.05508


def test_bridge_unreachable_is_clear_error():
    src = make(1)  # tidak ada server di port 1
    with pytest.raises(DataSourceError, match="server MT5"):
        src.connect()
    assert not src.is_connected()


def test_reconnects_after_bridge_comes_up(fake_mt5, bridge_server, monkeypatch):  # noqa: F811
    src = make(bridge_server.port)
    real_connect = src._mt5._connect
    src._mt5._connect = lambda: (_ for _ in ()).throw(ConnectionRefusedError("belum siap"))
    with pytest.raises(DataSourceError):
        src.connect()
    assert not src.is_connected()  # belum lewat jeda sambung ulang
    src._mt5._connect = real_connect
    monkeypatch.setattr(MT5Source, "RECONNECT_SECONDS", 0.0)
    assert src.is_connected()


def test_order_send_is_never_retried(fake_mt5, bridge_server):  # noqa: F811
    _trading_mt5(fake_mt5)
    m = RemoteMT5("127.0.0.1", bridge_server.port)
    m.symbol_info("EURUSD.m")  # buka koneksi
    calls = []

    class Broken:
        closed = False
        namespace = {"_ai_call": lambda *a: (calls.append(a), (_ for _ in ()).throw(EOFError("putus")))[1]}

        def close(self):
            pass

    m._conn = Broken()
    with pytest.raises(DataSourceError, match="Status order tidak pasti"):
        m.order_send({"symbol": "EURUSD.m"})
    assert len(calls) == 1
    assert fake_mt5.sent == []
