# AI Trading Dashboard

Dashboard trading pribadi untuk 7 pair (XAUUSD, BTCUSD, ETHUSD, EURUSD, GBPUSD, USDJPY, AUDUSD)
yang terhubung ke satu akun MetaTrader 5.

## Rencana

1. **Fondasi & koneksi MT5** (selesai): backend FastAPI yang menarik harga real-time
   dan candle H4/H1/M15 dari MT5.
2. **Mesin analisis & Confidence Score** (selesai di versi ini): EMA 50/200, S/R, Order Block, Fibonacci 0.618,
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
| `GET /api/analysis` | Ringkasan sinyal ke-7 pair, urut dari Confidence Score tertinggi |
| `GET /api/analysis/{symbol}` | Sinyal lengkap: Entry, SL, TP1, TP2, Confidence Score, dan alasan tiap skor |
| `GET /api/news?hours=24` | Jadwal berita high impact dan pair yang sedang diblokir |
| `GET /api/backtest/{symbol}?days=30` | Uji strategi di data historis (maks 60 hari) |

## Cara kerja analisis

| Faktor | Timeframe | Bobot |
|---|---|---|
| Tren EMA 50/200 (harga > EMA50 > EMA200 untuk bullish) | H4 | 20 |
| Tren EMA 50/200 | H1 | 15 |
| Harga di golden zone Fibonacci (0.5 sampai 0.65, sekitar 0.618) | H1 | 15 (7.5 jika di 0.382 sampai 0.786) |
| Dekat Support (untuk BUY) / Resistance (untuk SELL) | H4 + H1 | 10 |
| Di area Order Block searah tren | H4 + H1 | 10 |
| MACD cross / histogram menguat searah tren | M15 | 15 |
| RSI 50 sampai 70 (BUY) atau 30 sampai 50 (SELL) | M15 | 15 |

- **Arah**: diambil dari tren H4. Jika H4 netral, dipakai H1. Jika H4 dan H1 berlawanan, tidak ada sinyal.
- **Sinyal BUY/SELL** keluar jika MACD dan RSI M15 sama-sama mendukung dan skor minimal 60.
- **SL** = 1.5 x ATR(14) M15 dari harga entry (minimal 3x spread). **TP1** = 1.5R, **TP2** = 2R.
- **Berita**: jika ada berita high impact untuk mata uang pair itu dalam ±30 menit, sinyal menjadi
  **WAIT & SEE** dan skor dikurangi 40. Jika kalender gagal diambil, skor dikurangi 10 dan muncul peringatan.
- **auto_trade_eligible** bernilai true jika sinyal BUY/SELL dengan skor di atas 80 (dipakai di langkah 4).

### Sumber berita

Myfxbook tidak punya API resmi, jadi aplikasi membaca RSS kalender mereka. Jika gagal, aplikasi
otomatis memakai kalender ForexFactory sebagai cadangan. Atur urutannya di `.env` dengan `NEWS_SOURCES`.

## Tes

```bash
cd backend
pytest
```

Tes memakai data simulasi dan modul MT5 palsu, jadi bisa dijalankan di OS apa pun.

## Keamanan

- File `.env` berisi password akun dan **tidak boleh di-commit** (sudah ada di `.gitignore`).
- Selalu uji di akun demo sebelum memakai akun real.
