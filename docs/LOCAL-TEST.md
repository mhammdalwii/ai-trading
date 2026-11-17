# Uji di komputer sendiri sebelum ke VPS

Urutan yang disarankan: mulai dari mode simulasi (tanpa broker), lalu Claude, lalu MT5 demo.
Lanjut ke VPS ([DEPLOY.md](DEPLOY.md)) hanya jika semua tahap di bawah sudah lulus.

Di setiap tahap, jalankan skrip uji cepat. Skrip ini hanya membaca data dan **tidak mengirim order**:

```powershell
python scripts\smoke_test.py          # Windows
python3 scripts/smoke_test.py         # Mac / Linux
```

Hasilnya berupa daftar `[OK]`, `[PERINGATAN]`, atau `[GAGAL]`. Tahap dianggap lulus jika tidak ada `[GAGAL]`.

---

## Tahap 0. Siapkan komputer

| Perlu | Keterangan |
|---|---|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | Windows: aktifkan WSL 2 saat instalasi. Setelah terpasang, buka Docker Desktop sampai statusnya "Engine running". |
| [Git](https://git-scm.com/downloads) | Untuk mengambil kode. |
| [Python 3.11+](https://www.python.org/downloads/) | Untuk skrip uji (dan untuk Tahap 4A). Windows: centang "Add Python to PATH". |
| RAM kosong | Tahap 1 sampai 3: 1 GB. Tahap 4B (MT5 di Docker): 4 GB. |

```powershell
git clone https://github.com/mhammdalwii/ai-trading.git
cd ai-trading
copy .env.example .env        # Mac/Linux: cp .env.example .env
```

---

## Tahap 1. Mode simulasi di Docker (tanpa broker)

`.env` bawaan sudah memakai `DATA_SOURCE=mock`, jadi tidak perlu diubah.

```powershell
docker compose up -d --build
docker compose ps             # app harus "Up ... (healthy)" setelah sekitar 20 detik
python scripts\smoke_test.py
```

Hasil yang diharapkan (contoh dari uji kami):

```
[OK]         Server bisa dihubungi: sumber data: mock
[OK]         Terhubung ke sumber data: mock | akun 0 @ MOCK-Demo | DEMO | balance 10000.0 USD
[OK]         Harga 7 pair: lengkap
[OK]         Candle H4/H1/M15 XAUUSD: H4=300, H1=300, M15=300
[OK]         Analisis 7 pair: 0.1 detik | skor tertinggi XAUUSD NO_TRADE 50.0%
[OK]         Kalender berita: sumber myfxbook, ... berita high impact 24 jam
[OK]         Auto-Trigger: OFF (sesuai pengaman)
[OK]         Log order: bisa dibaca
[PERINGATAN] Claude API key: ANTHROPIC_API_KEY kosong, dipakai penjelasan otomatis
```

Peringatan Claude memang wajar di tahap ini. Jika **Kalender berita** juga peringatan di PC Anda,
catat pesannya: artinya Myfxbook dan ForexFactory tidak bisa dibaca dari jaringan Anda.

### Cek manual di browser: http://localhost:8000

- [ ] Daftar 7 pair di kiri tampil dengan harga dan skor.
- [ ] Grafik berganti saat klik pair lain dan saat pilih M15 / H1 / H4.
- [ ] Panel sinyal menampilkan Entry, SL, TP1, TP2, dan tabel alasan skor.
- [ ] **Execute to MT5**: muncul konfirmasi, lalu order simulasi tercatat di "Posisi terbuka" dan "Log order".
      Tombol hanya aktif jika sinyalnya BUY/SELL. Jika semua pair NO TRADE, tunggu candle M15 berikutnya
      (data simulasi ikut berubah). Di mode simulasi, order tidak dikirim ke broker.
- [ ] Tutup posisi simulasi dari tabel posisi.
- [ ] Saklar **Auto-Trigger** bisa dinyalakan dan dimatikan.
- [ ] Panel **Penjelasan AI**: klik "Jelaskan", muncul penjelasan otomatis.
- [ ] Restart: `docker compose restart app`, lalu cek Auto-Trigger kembali OFF dan log order masih ada.

---

## Tahap 2. Penjelasan AI dengan Claude

1. Buat API key di https://console.anthropic.com (Settings > API Keys) dan isi saldo kredit.
2. Isi di `.env`: `ANTHROPIC_API_KEY=sk-ant-...`
3. Terapkan dan uji (opsi `--ai` memanggil Claude sekali, biayanya kecil):

```powershell
docker compose up -d
python scripts\smoke_test.py --ai
```

Yang diharapkan: `Claude API key: aktif, model claude-opus-5-5` dan
`Penjelasan AI XAUUSD: claude (...)`. Di dashboard, label panel AI berubah menjadi "Claude (...)".
Jika masih "Otomatis: Claude tidak tersedia (...)", lihat log: `docker compose logs app | findstr Claude`.

---

## Tahap 3. Siapkan akun MT5 demo

1. Buka akun **demo** di broker Anda. Catat login (angka), password, dan nama server (misal `NamaBroker-Demo`).
2. Pasang terminal MetaTrader 5 dari broker, login dengan akun demo.
3. Di Market Watch, klik kanan > **Show All**, lalu catat nama persis 7 simbol.
   Jika ada akhiran (misal `XAUUSD.m`), isi `SYMBOL_SUFFIX=.m`. Jika namanya beda total (misal `GOLD`),
   pakai `SYMBOL_MAP={"XAUUSD": "GOLD"}`.

Lalu pilih **salah satu** cara uji di bawah. Jika PC Anda Windows, mulai dari 4A karena paling sederhana.
Setelah itu, jika memungkinkan, coba 4B karena caranya sama persis dengan VPS nanti.

---

## Tahap 4A. MT5 demo di Windows, tanpa Docker (cara resmi MT5)

Matikan dulu container dari tahap sebelumnya agar port 8000 bebas: `docker compose down`.

Di `.env`:

```
DATA_SOURCE=mt5
MT5_LOGIN=12345678
MT5_PASSWORD=password_demo
MT5_SERVER=NamaBroker-Demo
MT5_BRIDGE_HOST=
SYMBOL_SUFFIX=
```

Lalu di PowerShell:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy ..\.env .env
uvicorn app.main:app
```

Biarkan jendela itu terbuka. Di jendela PowerShell kedua (dari folder `ai-trading`):

```powershell
python scripts\smoke_test.py --ai
```

Yang diharapkan: `Terhubung ke sumber data: mt5 | akun ... | DEMO`, dan harga 7 pair lengkap.

---

## Tahap 4B. MT5 demo di Docker, sama seperti VPS

Butuh PC x86_64 (Intel/AMD). **Mac Apple Silicon (M1/M2/M3) tidak didukung** oleh image MT5 ini.

Di `.env`:

```
DATA_SOURCE=mt5
MT5_LOGIN=12345678
MT5_PASSWORD=password_demo
MT5_SERVER=NamaBroker-Demo
MT5_BRIDGE_HOST=mt5
COMPOSE_PROFILES=mt5
VNC_PASSWORD=buat_password_sendiri
```

```powershell
docker compose up -d --build
docker compose logs -f mt5      # tunggu 5 sampai 10 menit pertama kali (unduh Wine, MT5, Python)
```

1. Buka http://localhost:3000, login dengan `VNC_USER` (default `trader`) dan `VNC_PASSWORD`.
2. Di MT5 dalam browser: **File > Login to Trade Account**, cari server broker, login akun demo.
3. Aktifkan tombol **Algo Trading** di toolbar (harus hijau).
4. Tunggu sekitar 30 detik (dashboard menyambung ulang otomatis), lalu:

```powershell
python scripts\smoke_test.py --ai
```

Yang diharapkan: `Terhubung ke sumber data: mt5-bridge | akun ... | DEMO`.

---

## Tahap 5. Uji order di akun demo (manual, di dashboard)

- [ ] Pilih pair yang sinyalnya BUY/SELL (jika tidak ada, tunggu atau coba di jam pasar ramai).
- [ ] **Execute to MT5** dengan lot 0.01. Cek posisi muncul di MT5 dengan SL dan TP yang sama dengan dashboard.
- [ ] Tutup posisi dari dashboard, cek di MT5 posisinya tertutup.
- [ ] Nyalakan Auto-Trigger beberapa jam di akun demo, lalu cek panel Auto-Trigger dan log order.
- [ ] Cek simbol dengan sinyal **WAIT & SEE** saat ada berita high impact (lihat panel berita).

---

## Siap ke VPS jika

- [ ] Tahap 1 lulus tanpa `[GAGAL]`.
- [ ] Tahap 2: Penjelasan AI memakai Claude.
- [ ] Tahap 4A atau 4B lulus, akun terbaca sebagai DEMO, harga 7 pair lengkap.
- [ ] Tahap 5: minimal satu order demo terbuka dan tertutup dengan benar.
- [ ] Nilai `.env` yang sudah benar (terutama `SYMBOL_SUFFIX` / `SYMBOL_MAP`) dicatat untuk dipakai di VPS.

---

## Jika ada masalah

| Gejala | Penyebab umum | Solusi |
|---|---|---|
| `Server bisa dihubungi` GAGAL | Container belum jalan atau port 8000 dipakai program lain | `docker compose ps`, `docker compose logs app`. Ganti `APP_PORT=8001` di `.env` lalu pakai `--url http://127.0.0.1:8001`. |
| `docker compose` error "VNC_PASSWORD" atau profil | `.env` belum lengkap | Isi `VNC_PASSWORD` jika `COMPOSE_PROFILES=mt5`. |
| `Terhubung ke sumber data` GAGAL (4A) | MT5 tertutup, salah login/server | Buka MT5 dan pastikan sudah login. Cek `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`. |
| `Terhubung ke sumber data` GAGAL (4B) | Container mt5 masih menyiapkan diri, atau belum login | `docker compose logs mt5`, tunggu "mt5linux server is running", login lewat http://localhost:3000. |
| Harga sebagian pair GAGAL | Nama simbol broker berbeda | Atur `SYMBOL_SUFFIX` / `SYMBOL_MAP`, lalu `docker compose up -d`. |
| Order ditolak "Algo Trading" | Tombol Algo Trading di MT5 mati | Klik tombol **Algo Trading** di toolbar MT5. |
| Kalender berita PERINGATAN | Myfxbook/ForexFactory tidak bisa diakses | Catat pesannya dan kirim ke kami. Sinyal tetap jalan, tapi skor dikurangi 10 dan muncul peringatan. |
| Penjelasan AI tetap "Otomatis" | API key salah, saldo kosong, atau tidak ada internet | `docker compose logs app`, cari baris "Gagal memanggil Claude". |

Untuk melaporkan hasil, salin seluruh output `smoke_test.py` dan (jika ada error) 30 baris terakhir dari
`docker compose logs app`. **Jangan** sertakan isi `.env`.
