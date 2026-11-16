from datetime import datetime, timedelta, timezone

from app.news import ForexFactoryProvider, MyfxbookProvider, NewsEvent, NewsFilter, NewsProvider, StaticProvider

NOW = datetime(2026, 11, 16, 13, 0, tzinfo=timezone.utc)

MYFXBOOK_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Forex Economic Calendar</title>
<item>
  <title>Non Farm Payrolls</title>
  <link>https://www.myfxbook.com/forex-economic-calendar/united-states/non-farm-payrolls</link>
  <pubDate>Mon, 16 Nov 2026 13:20:00 GMT</pubDate>
  <description>&lt;table&gt;&lt;tr&gt;&lt;td&gt;Impact&lt;/td&gt;&lt;td&gt;High&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;</description>
</item>
<item>
  <title>German Factory Orders MoM</title>
  <link>https://www.myfxbook.com/forex-economic-calendar/germany/factory-orders-mom</link>
  <pubDate>Mon, 16 Nov 2026 13:10:00 GMT</pubDate>
  <description>&lt;td&gt;Medium&lt;/td&gt;</description>
</item>
</channel></rss>"""

FF_SAMPLE = b"""[
 {"title":"CPI y/y","country":"GBP","date":"2026-11-16T08:15:00-05:00","impact":"High","forecast":"","previous":""},
 {"title":"Bank Holiday","country":"JPY","date":"2026-11-16T00:00:00-05:00","impact":"Holiday","forecast":"","previous":""}
]"""


def test_parse_myfxbook():
    ev = MyfxbookProvider.parse(MYFXBOOK_SAMPLE)
    assert [(e.currency, e.impact) for e in ev] == [("USD", "High"), ("EUR", "Medium")]
    assert ev[0].time == datetime(2026, 11, 16, 13, 20, tzinfo=timezone.utc)


def test_parse_forexfactory():
    ev = ForexFactoryProvider.parse(FF_SAMPLE)
    assert ev[0].currency == "GBP" and ev[0].impact == "High"
    assert ev[0].time == datetime(2026, 11, 16, 13, 15, tzinfo=timezone.utc)
    assert ev[1].impact == "Low"


def test_filter_window_and_currency():
    events = [
        NewsEvent(NOW + timedelta(minutes=25), "USD", "NFP", "High"),
        NewsEvent(NOW + timedelta(minutes=45), "GBP", "CPI", "High"),
        NewsEvent(NOW + timedelta(minutes=5), "EUR", "PMI", "Medium"),
    ]
    f = NewsFilter([StaticProvider(events)])
    assert f.check("XAUUSD", NOW)["blocked"] is True       # USD dalam 25 menit
    assert f.check("EURUSD", NOW)["blocked"] is True        # ikut USD
    assert f.check("GBPUSD", NOW + timedelta(minutes=-40))["blocked"] is False
    assert f.check("GBPUSD", NOW + timedelta(minutes=20))["blocked"] is True   # CPI 25 menit lagi
    assert f.check("GBPUSD", NOW + timedelta(minutes=80))["blocked"] is False  # sudah lewat 35 menit


def test_fallback_to_second_provider():
    class Broken(NewsProvider):
        name = "myfxbook"

        def fetch(self):
            raise OSError("blocked")

    f = NewsFilter([Broken(), StaticProvider([NewsEvent(NOW, "USD", "FOMC", "High")])])
    res = f.check("BTCUSD", NOW)
    assert res["blocked"] and res["source"] == "static" and res["error"] is None


def test_all_providers_fail_reports_error():
    class Broken(NewsProvider):
        name = "x"

        def fetch(self):
            raise OSError("down")

    res = NewsFilter([Broken()]).check("EURUSD", NOW)
    assert res["blocked"] is False and "down" in res["error"] and res["source"] is None
