from datetime import datetime, timezone

from app.backtest import run_backtest
from app.config import Settings
from app.data_sources import MockSource


def test_backtest_runs_and_is_consistent():
    s = MockSource(Settings(), now=datetime(2026, 11, 16, 15, 7, tzinfo=timezone.utc))
    s.connect()
    sym = "EURUSD"
    r = run_backtest(sym, s.get_frame(sym, "H4", 1500), s.get_frame(sym, "H1", 3000), s.get_frame(sym, "M15", 4000), days=10)
    assert r["trades"] == len(r["history"]) or len(r["history"]) == 50
    for t in r["history"]:
        assert t["signal"] in ("BUY", "SELL")
        assert t["r"] >= -1.5  # rugi dibatasi SL (plus spread saat timeout)
        if t["outcome"] == "tp":
            assert t["r"] == 2.0
    times = [t["time"] for t in r["history"]]
    assert times == sorted(times) and len(set(times)) == len(times)
