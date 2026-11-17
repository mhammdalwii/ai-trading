"""AI Trading Advisor memakai Claude (Anthropic API) dengan structured output.

AI hanya MENJELASKAN hasil mesin analisis (angka Entry/SL/TP, skor, alasan). AI tidak pernah
mengirim order dan tidak mengubah sinyal. Jika ANTHROPIC_API_KEY kosong atau API gagal,
dipakai penjelasan otomatis berbasis aturan (fallback), jadi dashboard tetap berjalan.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError

from .config import Settings

log = logging.getLogger("trading.ai")


class AIAdviceSchema(BaseModel):
    headline: str = Field(description="Ringkasan 1 kalimat tegas mengenai status sinyal dan kondisi pair saat ini.")
    executive_summary: str = Field(
        description="Penjelasan 2-3 kalimat yang membedah konfluensi tren H4/H1, level penting "
                    "(Fibonacci/Order Block/S-R), dan momentum M15."
    )
    news_and_risk_note: str = Field(
        description="Dampak jadwal berita high impact serta saran manajemen risiko terkait jarak SL/TP dan spread."
    )
    action_plan: list[str] = Field(
        description="3 langkah konkret untuk trader sekarang (kapan entry, level yang dipantau, kapan batal)."
    )


def _strict_schema(model: type[BaseModel]) -> dict:
    """Skema JSON dari Pydantic, ditambah additionalProperties=false untuk structured output."""
    schema = model.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = list(schema.get("properties", {}))
    return schema


ADVICE_JSON_SCHEMA = _strict_schema(AIAdviceSchema)

SYSTEM_PROMPT = """Anda adalah asisten analisis trading pribadi untuk satu trader. Anda menerima data JSON \
hasil mesin kuantitatif multi-timeframe (H4, H1, M15) dan filter berita, lalu menjelaskannya dalam Bahasa \
Indonesia yang jelas dan objektif.

Aturan:
1. Pakai persis angka Entry, Stop Loss, dan Take Profit dari data. Jangan membuat angka baru.
2. Jelaskan mengapa Confidence Score tinggi atau rendah berdasarkan faktor yang diberikan \
(EMA 50/200, Fibonacci golden zone, Order Block, Support/Resistance, MACD dan RSI M15).
3. Jika sinyal WAIT_AND_SEE karena berita high impact, sarankan dengan tegas untuk menunggu.
4. Jika sinyal NO_TRADE, jangan mendorong entry; jelaskan apa yang perlu terjadi dulu.
5. Jika ada pertanyaan trader, jawab langsung di executive_summary dan action_plan.
6. Ini bukan saran keuangan; keputusan dan eksekusi tetap di tangan trader."""

PAYLOAD_KEYS = ("symbol", "signal", "direction", "confidence", "entry", "stop_loss", "take_profit_1",
                "take_profit_2", "risk_reward", "auto_trade_eligible", "factors", "warnings")
CONTEXT_KEYS = ("price", "trend", "atr", "rsi_m15", "macd_m15", "fibonacci", "news")


def build_payload(analysis: dict) -> dict:
    payload = {k: analysis.get(k) for k in PAYLOAD_KEYS}
    ctx = analysis.get("context") or {}
    payload["context"] = {k: ctx.get(k) for k in CONTEXT_KEYS if k in ctx}
    return payload


class AIAdvisor:
    """Memanggil Claude dengan cache singkat agar klik berulang tidak menambah biaya."""

    def __init__(self, settings: Settings, client_factory: Callable[[str], Any] | None = None):
        self.settings = settings
        self._client_factory = client_factory or self._default_client
        self._client = None
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _default_client(api_key: str):
        import anthropic

        return anthropic.Anthropic(api_key=api_key, timeout=90.0, max_retries=2)

    @property
    def enabled(self) -> bool:
        return bool((self.settings.anthropic_api_key or "").strip())

    def _get_client(self):
        if self._client is None:
            self._client = self._client_factory(self.settings.anthropic_api_key.strip())
        return self._client

    def brief(self, analysis: dict, user_question: str | None = None) -> dict:
        question = (user_question or "").strip()[:500] or None
        if not self.enabled:
            return fallback_brief(analysis, question, reason="ANTHROPIC_API_KEY belum diisi di file .env")

        payload = build_payload(analysis)
        key = hashlib.sha256(json.dumps([payload, question], sort_keys=True, default=str).encode()).hexdigest()
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.settings.ai_cache_seconds:
                return {**hit[1], "cached": True}

        try:
            result = self._call_claude(payload, question)
        except Exception as exc:  # noqa: BLE001 - dashboard tidak boleh error karena AI
            log.warning("Gagal memanggil Claude, memakai fallback: %s", exc)
            return fallback_brief(analysis, question, reason=f"Claude tidak tersedia ({type(exc).__name__})")

        with self._lock:
            self._cache[key] = (now, result)
            for k in [k for k, (t, _) in self._cache.items() if now - t >= self.settings.ai_cache_seconds]:
                self._cache.pop(k, None)
        return {**result, "cached": False}

    def _call_claude(self, payload: dict, question: str | None) -> dict:
        text = "Data analisis market terkini:\n" + json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        if question:
            text += f"\n\nPertanyaan trader: {question}"

        response = self._get_client().beta.messages.create(
            model=self.settings.ai_model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
            output_config={
                "effort": self.settings.ai_effort,
                "format": {"type": "json_schema", "schema": ADVICE_JSON_SCHEMA},
            },
            # Jika model menolak (refusal), API otomatis mencoba model cadangan di panggilan yang sama
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Permintaan ditolak oleh model")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Jawaban terpotong (max_tokens)")
        raw = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
        if raw is None:
            raise RuntimeError("Tidak ada teks di jawaban")
        try:
            advice = AIAdviceSchema.model_validate_json(raw)
        except ValidationError as exc:
            raise RuntimeError(f"Format jawaban tidak valid: {exc.error_count()} error") from exc
        return {"provider": "claude", "model": response.model, **advice.model_dump()}


def fallback_brief(a: dict, user_question: str | None = None, reason: str = "") -> dict:
    """Penjelasan otomatis jika ANTHROPIC_API_KEY belum disetel atau API tidak bisa dihubungi."""
    sym = a.get("symbol", "-")
    sig = a.get("signal", "NO_TRADE")
    conf = float(a.get("confidence") or 0)
    direction = a.get("direction") or "neutral"

    pos_factors = [f["name"] for f in a.get("factors", []) if f.get("points", 0) > 0]
    weak_factors = [f["name"] for f in a.get("factors", []) if f.get("points", 0) == 0]

    headline = f"{sym}: sinyal {sig} dengan Confidence Score {conf:.1f}% (bias {direction.upper()})."
    exec_sum = (
        f"Pendukung utama: {', '.join(pos_factors) if pos_factors else 'belum ada konfluensi kuat'}. "
        f"Belum terkonfirmasi: {', '.join(weak_factors) if weak_factors else 'semua indikator mendukung'}."
    )
    if user_question:
        exec_sum += " Isi ANTHROPIC_API_KEY di .env agar pertanyaan Anda dijawab oleh Claude."

    warnings = a.get("warnings") or []
    news_note = "; ".join(warnings) if warnings else "Tidak ada berita high impact dalam ±30 menit."

    if sig in ("BUY", "SELL"):
        plan = [
            f"Pertimbangkan {sig} di sekitar {a.get('entry')} dengan SL {a.get('stop_loss')}.",
            f"Target bertahap di TP1 ({a.get('take_profit_1')}) atau TP2 ({a.get('take_profit_2')}).",
            "Pastikan spread tidak melebar sebelum menekan Execute to MT5.",
        ]
    elif sig == "WAIT_AND_SEE":
        plan = [
            "Tahan posisi baru karena ada berita high impact dalam ±30 menit.",
            "Tunggu 15 sampai 30 menit setelah rilis sampai spread dan volatilitas normal.",
            "Cek ulang setelah candle M15 berikutnya tutup.",
        ]
    else:
        plan = [
            "Belum ada setup dengan probabilitas tinggi; lebih baik menunggu.",
            "Pantau harga kembali ke golden zone Fibonacci atau Order Block H1.",
            "Tunggu MACD dan RSI M15 searah tren utama.",
        ]

    return {
        "provider": "fallback",
        "model": reason or "rule-based",
        "headline": headline,
        "executive_summary": exec_sum,
        "news_and_risk_note": news_note,
        "action_plan": plan,
        "cached": False,
    }
