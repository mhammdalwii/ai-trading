# AI Trading Dashboard

Dashboard trading pribadi untuk 7 pair (XAUUSD, BTCUSD, ETHUSD, EURUSD, GBPUSD, USDJPY, AUDUSD)
yang terhubung ke satu akun MetaTrader 5.

## Rencana

1. **Fondasi & koneksi MT5** (selesai di versi ini): backend FastAPI yang menarik harga real-time
   dan candle H4/H1/M15 dari MT5.
2. **Mesin analisis & Confidence Score**: EMA 50/200, S/R, Order Block, Fibonacci 0.618,
   RSI, MACD, SL/TP berbasis ATR (RR 1:1.5 sampai 1:2), filter berita high impact ±30 menit.
3. **Web dashboard & One-Click Trade**: tombol "Execute to MT5".
4. **Auto-Trigger & pengaman**: eksekusi otomatis saat skor di atas 80%, batas lot dan posisi, log order.

## Kebutuhan

- Windows 10/11 (atau VPS Windows). Library resmi `MetaTrader5` untuk Python **hanya berjalan di Windows**.
- Terminal MetaTrader 5 dari broker Anda, sudah login ke akun demo, dan **dibiarkan terbuka**.
- Di MT5: Tools > Options > Expert Advisors > centang **Allow algorithmic trading**.
- Python 3.11 atau lebih baru.

Tanpa MT5 pun Anda bisa mencoba API dengan `DATA_SOURCE=mock` (data simulasi).

## Instalasi (Windows, PowerShell)

```powershell
git clone https://github.com/<akun-anda>/ai-trading-dashboard.git
cd ai-trading-dashboard\backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy ..\.env.example .env
notepad .env     # isi DATA_SOURCE=mt5, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
uvicorn app.main:app --reload
```

Lalu buka http://127.0.0.1:8000/docs untuk mencoba semua endpoint.

### Nama simbol broker

Banyak broker memberi akhiran pada simbol, misalnya `XAUUSD.m` atau `BTCUSD#`.
Cek di Market Watch MT5, lalu atur di `.env`:

```
SYMBOL_SUFFIX=.m
SYMBOL_MAP={"XAUUSD": "GOLD"}
```

`SYMBOL_MAP` menimpa `SYMBOL_SUFFIX` untuk simbol tertentu.

## Endpoint

| Endpoint | Fungsi |
|---|---|
| `GET /api/health` | Status koneksi dan info akun (saldo, equity, leverage) |
| `GET /api/symbols` | 7 pair beserta nama simbolnya di broker |
| `GET /api/ticks` | Harga bid/ask terkini ke-7 pair |
| `GET /api/tick/{symbol}` | Harga bid/ask satu pair |
| `GET /api/candles/{symbol}?timeframe=H1&count=300` | Candle satu timeframe (maks 1000) |
| `GET /api/mtf/{symbol}?count=300` | Candle H4, H1, M15 sekaligus untuk analisis |

## Tes

```bash
cd backend
pytest
```

Tes memakai data simulasi dan modul MT5 palsu, jadi bisa dijalankan di OS apa pun.

## Keamanan

- File `.env` berisi password akun dan **tidak boleh di-commit** (sudah ada di `.gitignore`).
- Selalu uji di akun demo sebelum memakai akun real.
