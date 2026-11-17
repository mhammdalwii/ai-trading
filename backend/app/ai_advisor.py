"""AI Personal Trading Advisor menggunakan Google Gemini API (Structured Outputs)."""
from __future__ import annotations

import json
import logging
from pydantic import BaseModel, Field

from .config import Settings

log = logging.getLogger("trading.ai")


class AIAdviceSchema(BaseModel):
    headline: str = Field(description="Ringkasan 1 kalimat tegas mengenai status sinyal dan kondisi pair saat ini.")
    executive_summary: str = Field(
        description="Penjelasan asisten pribadi (2-3 kalimat) yang membedah konfluensi tren H4/H1, level penting (Fibonacci/Order Block/S-R), dan momentum M15."
    )
    news_and_risk_note: str = Field(
        description="Analisis dampak jadwal berita Myfxbook/ForexFactory serta saran manajemen risiko terkait jarak SL/TP dan spread."
    )
    action_plan: list[str] = Field(
        description="3 poin langkah konkret yang harus dilakukan trader sekarang (misal: kapan harus entry, di mana pantau pantulan harga, atau kapan harus batal entry)."
    )


SYSTEM_INSTRUCTION = """Kamu adalah Senior Quantitative Trading Assistant pribadi untuk trader profesional.
Tugasmu adalah membaca data JSON hasil perhitungan mesin kuantitatif Multi-Timeframe (H4, H1, M15) dan filter berita Myfxbook, lalu memberikan penjelasan yang tajam, objektif, dan mudah dipahami dalam Bahasa Indonesia.

Aturan Ketat:
1. JANGAN mengarang angka Entry, Stop Loss (SL), atau Take Profit (TP) baru. Gunakan persis angka yang ada di data JSON.
2. Jelaskan mengapa Confidence Score tinggi atau rendah berdasarkan faktor-faktor teknikal yang diberikan (EMA 50/200, Fibonacci Golden Zone, Order Block SMC, Support/Resistance, MACD & RSI M15).
3. Jika sinyal adalah WAIT_AND_SEE karena berita High Impact, peringatkan trader dengan tegas untuk menahan jari dan menunggu volatilitas mereda.
4. Jika ada pertanyaan tambahan dari pengguna (user_question), jawab pertanyaan tersebut secara langsung di dalam executive_summary dan action_plan.
"""


def generate_ai_brief(
    analysis_data: dict,
    settings: Settings,
    user_question: str | None = None,
) -> dict:
    """Menghasilkan analisis naratif dari Gemini API, atau fallback deterministik jika API key kosong."""
    api_key = (settings.gemini_api_key or "").strip()
    if not api_key:
        return _fallback_brief(analysis_data, user_question, reason="GEMINI_API_KEY belum diisi di file .env")

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        payload = {
            "symbol": analysis_data.get("symbol"),
            "signal": analysis_data.get("signal"),
            "direction": analysis_data.get("direction"),
            "confidence_score": analysis_data.get("confidence"),
            "entry": analysis_data.get("entry"),
            "stop_loss": analysis_data.get("stop_loss"),
            "take_profit_1": analysis_data.get("take_profit_1"),
            "take_profit_2": analysis_data.get("take_profit_2"),
            "factors": analysis_data.get("factors", []),
            "warnings": analysis_data.get("warnings", []),
            "context_summary": {
                "price": analysis_data.get("context", {}).get("price"),
                "trend": analysis_data.get("context", {}).get("trend"),
                "rsi_m15": analysis_data.get("context", {}).get("rsi_m15"),
                "news": analysis_data.get("context", {}).get("news"),
            },
        }

        prompt = f"Data Analisis Market Terkini:\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n"
        if user_question:
            prompt += f"\nPertanyaan Spesifik Trader: \"{user_question}\"\nJawab pertanyaan ini berdasarkan data di atas."

        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=AIAdviceSchema,
                temperature=0.2,
            ),
        )

        parsed = json.loads(response.text)
        return {
            "provider": "gemini",
            "model": settings.gemini_model,
            **parsed,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("Gagal memanggil Gemini API, menggunakan fallback: %s", exc)
        return _fallback_brief(analysis_data, user_question, reason=f"Fallback aktif ({exc})")


def _fallback_brief(a: dict, user_question: str | None = None, reason: str = "") -> dict:
    """Penjelasan otomatis jika GEMINI_API_KEY belum disetel atau koneksi internet terputus."""
    sym = a.get("symbol", "-")
    sig = a.get("signal", "NO_TRADE")
    conf = a.get("confidence", 0)
    direction = a.get("direction", "neutral")

    pos_factors = [f["name"] for f in a.get("factors", []) if f.get("points", 0) > 0]
    weak_factors = [f["name"] for f in a.get("factors", []) if f.get("points", 0) == 0]

    headline = f"{sym}: Sinyal {sig} dengan tingkat keyakinan {conf:.1f}% (Bias {direction.upper()})."
    exec_sum = (
        f"Pendukung utama saat ini adalah {', '.join(pos_factors) if pos_factors else 'belum ada konfluensi kuat'}. "
        f"Komponen yang belum terkonfirmasi: {', '.join(weak_factors) if weak_factors else 'semua indikator mendukung'}."
    )
    if user_question:
        exec_sum += f" (Catatan pertanyaan Anda: '{user_question}' — aktifkan GEMINI_API_KEY di .env untuk jawaban interaktif penuh)."

    warnings = a.get("warnings") or []
    news_note = "; ".join(warnings) if warnings else "Jadwal berita high impact aman dalam ±30 menit ke depan."

    if sig in ("BUY", "SELL"):
        plan = [
            f"Pertimbangkan eksekusi {sig} di sekitar {a.get('entry')} dengan batas risiko ketat di SL {a.get('stop_loss')}.",
            f"Target profit bertahap di TP1 ({a.get('take_profit_1')}) atau TP2 ({a.get('take_profit_2')}).",
            "Pastikan spread broker tidak melebar sebelum menekan tombol Execute to MT5.",
        ]
    elif sig == "WAIT_AND_SEE":
        plan = [
            "Tahan eksekusi posisi baru karena ada rilis berita High Impact dalam rentang ±30 menit.",
            "Tunggu minimal 15-30 menit setelah berita rilis hingga spread dan volatilitas kembali normal.",
            "Lakukan analisis ulang setelah candle M15 berikutnya tutup.",
        ]
    else:
        plan = [
            "Belum ada setup dengan probabilitas tinggi; disarankan menunggu di luar market (No Trade).",
            "Pantau kembalinya harga ke area Golden Zone Fibonacci atau Order Block H1.",
            "Tunggu konfirmasi persilangan MACD dan momentum RSI searah tren utama di M15.",
        ]

    return {
        "provider": "fallback",
        "model": reason or "rule-based",
        "headline": headline,
        "executive_summary": exec_sum,
        "news_and_risk_note": news_note,
        "action_plan": plan,
    }