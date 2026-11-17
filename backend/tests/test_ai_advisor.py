"""Tes penjelasan AI (Claude) memakai klien Anthropic palsu: tidak ada panggilan jaringan / biaya."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.ai_advisor import ADVICE_JSON_SCHEMA, AIAdvisor, build_payload
from app.config import Settings
from app.data_sources import MockSource
from app.main import app

ANALYSIS = {
    "symbol": "XAUUSD", "signal": "BUY", "direction": "bullish", "confidence": 85.0,
    "entry": 2650.25, "stop_loss": 2640.07, "take_profit_1": 2665.52, "take_profit_2": 2670.61,
    "risk_reward": {"tp1": 1.5, "tp2": 2.0}, "auto_trade_eligible": True,
    "factors": [{"name": "Tren H4 (EMA 50/200)", "points": 20, "max_points": 20, "note": "H4 bullish"},
                {"name": "Order Block (SMC)", "points": 0, "max_points": 10, "note": "-"}],
    "warnings": [],
    "context": {"price": 2650.12, "rsi_m15": 55.0, "levels": [{"price": 1, "touches": 2}] * 50},
}

ADVICE = {
    "headline": "XAUUSD BUY kuat.",
    "executive_summary": "Tren H4 bullish dan momentum M15 mendukung.",
    "news_and_risk_note": "Tidak ada berita.",
    "action_plan": ["Entry di 2650.25", "SL 2640.07", "TP2 2670.61"],
}


class FakeClaude:
    def __init__(self, text=json.dumps(ADVICE), stop_reason="end_turn", error=None):
        self.calls = []
        self.text, self.stop_reason, self.error = text, stop_reason, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return SimpleNamespace(model=kw["model"], stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text=self.text)])


def make(fake=None, **kw):
    fake = fake or FakeClaude()
    settings = Settings(anthropic_api_key="sk-test", **kw)
    return AIAdvisor(settings, client_factory=lambda key: fake), fake


def test_without_api_key_uses_fallback():
    advisor = AIAdvisor(Settings(anthropic_api_key=""), client_factory=lambda k: pytest.fail("tidak boleh dipanggil"))
    out = advisor.brief(ANALYSIS, "kapan entry?")
    assert out["provider"] == "fallback" and "ANTHROPIC_API_KEY" in out["model"]
    assert len(out["action_plan"]) == 3 and "2650.25" in out["action_plan"][0]


def test_claude_request_shape():
    advisor, fake = make()
    out = advisor.brief(ANALYSIS, "Apakah aman entry sekarang?")
    assert out["provider"] == "claude" and out["model"] == "claude-opus-5-5"
    assert out["headline"] == ADVICE["headline"] and out["action_plan"] == ADVICE["action_plan"]
    kw = fake.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["effort"] == "medium"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert "temperature" not in kw and "thinking" not in kw
    text = kw["messages"][0]["content"]
    assert "Apakah aman entry sekarang?" in text and "2640.07" in text
    assert '"levels"' not in text  # data mentah yang panjang tidak dikirim


def test_schema_is_strict():
    assert ADVICE_JSON_SCHEMA["additionalProperties"] is False
    assert set(ADVICE_JSON_SCHEMA["required"]) == set(ADVICE)


def test_payload_keeps_engine_numbers():
    p = build_payload(ANALYSIS)
    assert p["entry"] == 2650.25 and p["take_profit_2"] == 2670.61 and "levels" not in p["context"]


@pytest.mark.parametrize("fake", [
    FakeClaude(stop_reason="refusal"),
    FakeClaude(stop_reason="max_tokens"),
    FakeClaude(text="bukan json"),
    FakeClaude(text=json.dumps({"headline": "x"})),
    FakeClaude(error=ConnectionError("offline")),
])
def test_failures_fall_back(fake):
    advisor, _ = make(fake)
    out = advisor.brief(ANALYSIS)
    assert out["provider"] == "fallback" and out["headline"].startswith("XAUUSD")


def test_cache_avoids_repeat_calls():
    advisor, fake = make()
    a = advisor.brief(ANALYSIS)
    b = advisor.brief(ANALYSIS)
    assert len(fake.calls) == 1 and a["cached"] is False and b["cached"] is True
    advisor.brief(ANALYSIS, "pertanyaan lain")
    assert len(fake.calls) == 2


def test_cache_can_be_disabled():
    advisor, fake = make(ai_cache_seconds=0)
    advisor.brief(ANALYSIS)
    advisor.brief(ANALYSIS)
    assert len(fake.calls) == 2


@pytest.fixture
def client():
    source = MockSource(Settings(data_source="mock"))
    source.connect()
    app.state.source = source
    with TestClient(app) as c:
        yield c
    app.state.source = None


def test_api_endpoint_with_fake_claude(client):
    advisor, fake = make()
    old, app.state.ai = app.state.ai, advisor
    try:
        r = client.post("/api/ai/EURUSD", json={"question": "Bagaimana trennya?"})
        assert r.status_code == 200
        body = r.json()
        assert body["provider"] == "claude" and body["symbol"] == "EURUSD" and "confidence" in body
        assert "Bagaimana trennya?" in fake.calls[0]["messages"][0]["content"]
        assert client.get("/api/ai/status").json() == {"enabled": True, "model": "claude-opus-5-5"}
    finally:
        app.state.ai = old


def test_api_endpoint_fallback_and_no_trade_side_effects(client):
    app.state.ai = AIAdvisor(Settings(anthropic_api_key=""))
    r = client.post("/api/ai/XAUUSD")
    assert r.status_code == 200 and r.json()["provider"] == "fallback"
    assert client.get("/api/positions").json() == []
    assert client.get("/api/ai/status").json()["enabled"] is False
    assert client.post("/api/ai/NOPE").status_code in (400, 404, 422)
