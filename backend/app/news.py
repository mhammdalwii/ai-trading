"""Filter berita high impact (News Filter Guardrail).

Sumber utama: RSS kalender ekonomi Myfxbook. Myfxbook tidak punya API resmi, jadi formatnya
bisa berubah. Jika gagal, dipakai cadangan kalender ForexFactory (JSON mingguan).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

log = logging.getLogger("trading.news")

MYFXBOOK_RSS = "https://www.myfxbook.com/rss/forex-economic-calendar-events"
FOREXFACTORY_JSON = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
USER_AGENT = "Mozilla/5.0 (compatible; ai-trading-dashboard/0.2)"

# Mata uang yang dipengaruhi tiap pair. Emas & crypto dihargai dalam USD.
SYMBOL_CURRENCIES = {
    "XAUUSD": ("USD",),
    "BTCUSD": ("USD",),
    "ETHUSD": ("USD",),
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "USDJPY": ("USD", "JPY"),
    "AUDUSD": ("AUD", "USD"),
}

# Slug negara di URL Myfxbook -> mata uang
_COUNTRY_CURRENCY = {
    "united-states": "USD", "euro-area": "EUR", "eurozone": "EUR", "germany": "EUR", "france": "EUR",
    "italy": "EUR", "spain": "EUR", "united-kingdom": "GBP", "japan": "JPY", "australia": "AUD",
    "canada": "CAD", "new-zealand": "NZD", "switzerland": "CHF", "china": "CNY",
}


@dataclass(frozen=True)
class NewsEvent:
    time: datetime
    currency: str
    title: str
    impact: str  # "High", "Medium", "Low"

    def as_dict(self) -> dict:
        return {"time": self.time.isoformat(), "currency": self.currency, "title": self.title, "impact": self.impact}


class NewsProvider(ABC):
    name: str

    @abstractmethod
    def fetch(self) -> list[NewsEvent]: ...


def _http_get(url: str, timeout: float = 10) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL tetap
        return resp.read()


def _normalize_impact(text: str) -> str | None:
    m = re.search(r"\b(high|medium|moderate|low)\b", text, re.I)
    if not m:
        return None
    word = m.group(1).lower()
    return {"high": "High", "medium": "Medium", "moderate": "Medium", "low": "Low"}[word]


class MyfxbookProvider(NewsProvider):
    name = "myfxbook"

    def __init__(self, url: str = MYFXBOOK_RSS):
        self.url = url

    @staticmethod
    def parse(xml_bytes: bytes) -> list[NewsEvent]:
        root = ET.fromstring(xml_bytes)
        events: list[NewsEvent] = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = item.findtext("link") or ""
            pub = item.findtext("pubDate")
            desc = unescape(item.findtext("description") or "")
            if not pub:
                continue
            try:
                when = parsedate_to_datetime(pub).astimezone(timezone.utc)
            except (TypeError, ValueError):
                continue
            impact = _normalize_impact(re.sub(r"<[^>]+>", " ", desc)) or "Low"
            currency = None
            m = re.search(r"economic-calendar/([a-z-]+)/", link)
            if m:
                currency = _COUNTRY_CURRENCY.get(m.group(1))
            if currency is None:
                m = re.search(r"\b(USD|EUR|GBP|JPY|AUD|CAD|NZD|CHF|CNY)\b", f"{title} {desc}")
                currency = m.group(1) if m else None
            if currency:
                events.append(NewsEvent(when, currency, title, impact))
        return events

    def fetch(self) -> list[NewsEvent]:
        return self.parse(_http_get(self.url))


class ForexFactoryProvider(NewsProvider):
    name = "forexfactory"

    def __init__(self, url: str = FOREXFACTORY_JSON):
        self.url = url

    @staticmethod
    def parse(raw: bytes) -> list[NewsEvent]:
        out = []
        for row in json.loads(raw):
            try:
                when = datetime.fromisoformat(row["date"]).astimezone(timezone.utc)
            except (KeyError, ValueError):
                continue
            impact = _normalize_impact(row.get("impact", "")) or "Low"
            out.append(NewsEvent(when, row.get("country", ""), row.get("title", ""), impact))
        return out

    def fetch(self) -> list[NewsEvent]:
        return self.parse(_http_get(self.url))


class StaticProvider(NewsProvider):
    """Untuk tes / mode simulasi."""

    name = "static"

    def __init__(self, events: list[NewsEvent] | None = None):
        self.events = events or []

    def fetch(self) -> list[NewsEvent]:
        return list(self.events)


class NewsFilter:
    """Ambil kalender dari beberapa sumber (urut prioritas), cache 15 menit."""

    def __init__(self, providers: list[NewsProvider], window_minutes: int = 30, cache_seconds: int = 900):
        self.providers = providers
        self.window = timedelta(minutes=window_minutes)
        self.cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._events: list[NewsEvent] = []
        self._fetched_at = 0.0
        self.source: str | None = None
        self.error: str | None = None

    def _refresh(self) -> None:
        errors = []
        for p in self.providers:
            try:
                self._events = p.fetch()
                self.source, self.error = p.name, None
                self._fetched_at = time.monotonic()
                return
            except Exception as exc:  # noqa: BLE001 - sumber eksternal, apa pun bisa gagal
                log.warning("Gagal ambil kalender dari %s: %s", p.name, exc)
                errors.append(f"{p.name}: {exc}")
        self.error = "; ".join(errors)
        self._fetched_at = time.monotonic()  # jangan spam request saat semua sumber gagal

    def events(self) -> list[NewsEvent]:
        with self._lock:
            if not self._fetched_at or time.monotonic() - self._fetched_at > self.cache_seconds:
                self._refresh()
            return list(self._events)

    def check(self, symbol: str, now: datetime) -> dict:
        currencies = SYMBOL_CURRENCIES.get(symbol, ())
        upcoming = [
            e for e in self.events()
            if e.impact == "High" and e.currency in currencies and abs(e.time - now) <= self.window
        ]
        return {
            "blocked": bool(upcoming),
            "events": [e.as_dict() for e in sorted(upcoming, key=lambda e: e.time)],
            "source": self.source,
            "error": self.error,
        }
