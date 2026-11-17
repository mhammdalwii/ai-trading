"""Uji cepat dashboard yang sedang berjalan (lokal atau VPS). Hanya membaca data, TIDAK mengirim order.

Pakai (Windows / Mac / Linux, cukup Python 3.10+ tanpa library tambahan):
    python scripts/smoke_test.py
    python scripts/smoke_test.py --url http://127.0.0.1:8000 --token TOKEN_DASHBOARD
    python scripts/smoke_test.py --ai        # ikut uji Penjelasan AI (memanggil Claude jika API key diisi, ada biaya kecil)

Hasil: setiap pengecekan ditandai OK / PERINGATAN / GAGAL. Exit code 1 jika ada yang GAGAL.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
TRADE_MODE = {0: "DEMO", 1: "CONTEST", 2: "REAL"}

results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    mark = {"OK": "[OK]        ", "WARN": "[PERINGATAN]", "FAIL": "[GAGAL]     "}[status]
    print(f"{mark} {name}" + (f": {detail}" if detail else ""))


def call(base: str, path: str, token: str, method: str = "GET", body: dict | None = None, timeout: float = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Dashboard-Token", token)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, json.loads(res.read() or b"null"), time.monotonic() - t0
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"null")
        except ValueError:
            payload = None
        return exc.code, payload, time.monotonic() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--token", default="", help="DASHBOARD_TOKEN jika diisi di .env")
    ap.add_argument("--ai", action="store_true", help="uji juga POST /api/ai/XAUUSD")
    args = ap.parse_args()
    base, token = args.url, args.token
    print(f"Menguji {base}\n")

    # 1. Server hidup
    try:
        code, cfg, _ = call(base, "/api/config", token, timeout=10)
    except (urllib.error.URLError, OSError) as exc:
        record("FAIL", "Server bisa dihubungi", f"{exc}. Apakah container/uvicorn sudah jalan?")
        return summary()
    record("OK" if code == 200 else "FAIL", "Server bisa dihubungi", f"sumber data: {cfg.get('data_source') if cfg else '?'}")

    # 2. Koneksi ke MT5 / mock
    code, h, _ = call(base, "/api/health", token)
    if code == 200 and h.get("connected"):
        acc = h.get("account") or {}
        mode = TRADE_MODE.get(acc.get("trade_mode"), "?")
        record("OK", "Terhubung ke sumber data",
               f"{h.get('data_source')} | akun {acc.get('login')} @ {acc.get('server')} | {mode} | "
               f"balance {acc.get('balance')} {acc.get('currency')}")
        if mode == "REAL":
            record("WARN", "Jenis akun", "AKUN REAL terdeteksi. Uji dulu di akun DEMO.")
    else:
        record("FAIL", "Terhubung ke sumber data",
               (h or {}).get("message") or f"HTTP {code}. Cek MT5 terbuka/login, atau container mt5 masih menyiapkan diri")

    # 3. Simbol & harga
    code, ticks, _ = call(base, "/api/ticks", token)
    if code == 200 and isinstance(ticks, list):
        got = {t["symbol"] for t in ticks}
        missing = [s for s in SYMBOLS if s not in got]
        record("OK" if not missing else "FAIL", "Harga 7 pair",
               "lengkap" if not missing else f"tidak ada harga untuk {', '.join(missing)} (cek SYMBOL_SUFFIX/SYMBOL_MAP)")
    else:
        detail = (ticks or {}).get("detail") if isinstance(ticks, dict) else None
        record("FAIL", "Harga 7 pair", detail or f"HTTP {code}")

    # 4. Candle multi-timeframe
    code, mtf, _ = call(base, "/api/mtf/XAUUSD?count=300", token)
    if code == 200:
        sizes = {tf: len(mtf.get("timeframes", {}).get(tf, [])) for tf in ("H4", "H1", "M15")}
        ok = sizes and all(n >= 200 for n in sizes.values())
        record("OK" if ok else "WARN", "Candle H4/H1/M15 XAUUSD", ", ".join(f"{k}={v}" for k, v in sizes.items()) or "format tidak dikenal")
    else:
        record("FAIL", "Candle H4/H1/M15 XAUUSD", f"HTTP {code}")

    # 5. Analisis
    code, rows, dt = call(base, "/api/analysis", token, timeout=120)
    if code == 200 and isinstance(rows, list) and len(rows) == 7:
        best = rows[0]
        record("OK", "Analisis 7 pair", f"{dt:.1f} detik | skor tertinggi {best['symbol']} {best['signal']} {best['confidence']}%")
        errors = [r["symbol"] for r in rows if r.get("signal") == "ERROR"]
        if errors:
            record("WARN", "Analisis per pair", f"error di {', '.join(errors)}")
    else:
        record("FAIL", "Analisis 7 pair", f"HTTP {code}")

    # 6. Kalender berita
    code, news, _ = call(base, "/api/news?hours=24", token)
    if code == 200 and news.get("source"):
        record("OK", "Kalender berita", f"sumber {news['source']}, {len(news.get('events', []))} berita high impact 24 jam")
    else:
        record("WARN", "Kalender berita", (news or {}).get("error") or f"HTTP {code}. Filter berita tidak aktif")

    # 7. Auto-Trigger harus OFF saat start
    code, auto, _ = call(base, "/api/auto", token)
    if code == 200:
        record("OK" if not auto.get("enabled") else "WARN", "Auto-Trigger",
               "OFF (sesuai pengaman)" if not auto.get("enabled") else "sedang ON")
    else:
        record("FAIL", "Auto-Trigger", f"HTTP {code}")

    # 8. Log order bisa dibaca (volume data)
    code, _, _ = call(base, "/api/orders/log?limit=5", token)
    record("OK" if code == 200 else "FAIL", "Log order", "bisa dibaca" if code == 200 else f"HTTP {code}")

    # 9. Penjelasan AI
    code, ai, _ = call(base, "/api/ai/status", token)
    if code == 200:
        record("OK" if ai.get("enabled") else "WARN", "Claude API key",
               f"aktif, model {ai.get('model')}" if ai.get("enabled") else "ANTHROPIC_API_KEY kosong, dipakai penjelasan otomatis")
    if args.ai:
        code, brief, dt = call(base, "/api/ai/XAUUSD", token, method="POST",
                               body={"question": "Ringkas kondisi XAUUSD dalam satu kalimat."}, timeout=180)
        if code == 200:
            prov = brief.get("provider")
            status = "OK" if prov == "claude" or not (ai or {}).get("enabled") else "WARN"
            record(status, "Penjelasan AI XAUUSD", f"{prov} ({brief.get('model')}), {dt:.1f} detik: {brief.get('headline')}")
        else:
            record("FAIL", "Penjelasan AI XAUUSD", (brief or {}).get("detail") or f"HTTP {code}")

    return summary()


def summary() -> int:
    fails = sum(1 for s, _, _ in results if s == "FAIL")
    warns = sum(1 for s, _, _ in results if s == "WARN")
    print(f"\nRingkasan: {len(results) - fails - warns} OK, {warns} peringatan, {fails} gagal")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
