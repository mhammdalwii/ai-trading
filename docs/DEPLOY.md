# Deploy ke VPS dengan Docker dan CI/CD

Panduan ini menjalankan dashboard 24 jam di VPS, dengan update otomatis setiap ada perubahan di branch `main`.

## Masalah utama: MT5 hanya ada di Windows

Library `MetaTrader5` untuk Python hanya berjalan di Windows, sedangkan Docker di VPS hampir selalu
menjalankan container Linux. Ada tiga pilihan:

| Pilihan | Cara kerja | Kelebihan | Kekurangan |
|---|---|---|---|
| **A. VPS Linux + MT5 di Wine (disarankan, dipakai di repo ini)** | Dua container: `mt5` (terminal MT5 + Python Windows di Wine, image `gmag11/metatrader5_vnc`) dan `app` (dashboard). `app` memanggil MT5 lewat RPyC di jaringan internal. | Sesuai keinginan: Docker + CI/CD penuh. VPS Linux lebih murah. Semua berjalan di satu VPS. | MT5 di Wine bukan cara resmi MetaQuotes; sesekali bisa bermasalah setelah update MT5. Butuh RAM lebih besar (image MT5 sekitar 4 GB). Login MT5 pertama kali lewat layar MT5 di browser. |
| B. VPS Windows tanpa Docker | Pasang Python + MT5 langsung di Windows, jalankan seperti README. | Paling stabil untuk MT5 (cara resmi). | Docker di Windows menjalankan container Linux, jadi tidak membantu MT5. CI/CD lebih sulit (perlu runner Windows atau skrip PowerShell). VPS Windows lebih mahal. |
| C. Hibrida | Dashboard di Docker (VPS Linux), MT5 + server RPyC di PC/VPS Windows terpisah. | MT5 resmi di Windows. | Dua mesin yang harus menyala, dan koneksi RPyC lewat internet harus diamankan (VPN). Paling rumit. |

**Rekomendasi: pilihan A.** Kode tidak berubah tujuannya: aplikasi yang sama, hanya cara bicara ke MT5
yang lewat jembatan (`MT5_BRIDGE_HOST`). Jika nanti MT5 di Wine terasa tidak stabil, pindah ke pilihan B
cukup dengan mengosongkan `MT5_BRIDGE_HOST` dan menjalankan aplikasi di Windows seperti di README.

Pengaman tetap sama di semua pilihan: Auto-Trigger selalu OFF saat container start, Auto-Trigger ditolak
di akun real kecuali `ALLOW_AUTO_ON_REAL_ACCOUNT=true`, dan batas lot/posisi tetap berlaku.

## Spesifikasi VPS yang disarankan (pilihan A)

- Ubuntu 22.04 atau 24.04, **x86_64/amd64** (MT5 di Wine tidak jalan di ARM).
- Minimal 2 vCPU, **4 GB RAM**, 30 GB disk.
- Akses SSH dengan kunci (bukan password).

## Gambaran

```
GitHub (push ke main)
  └─ GitHub Actions: tes 78 → build image → push ke ghcr.io → SSH ke VPS → docker compose up -d

VPS Linux
  ├─ container app  (dashboard, port 127.0.0.1:8000)
  │      │ RPyC port 8001, hanya jaringan internal Docker
  └─ container mt5  (MetaTrader 5 di Wine, layar MT5 di 127.0.0.1:3000)
```

Port 8000 dan 3000 hanya terbuka di localhost VPS. Anda membukanya dari laptop lewat SSH tunnel.
Port 8001 tidak dibuka sama sekali, karena server RPyC bisa menjalankan perintah apa pun.

## 1. Siapkan VPS (sekali saja)

```bash
ssh user@IP_VPS
curl -fsSLO https://raw.githubusercontent.com/mhammdalwii/ai-trading/main/deploy/setup-vps.sh
bash setup-vps.sh        # pasang Docker, buat folder ~/ai-trading, firewall hanya SSH
exit                     # login ulang agar grup docker aktif
```

## 2. Buat file `.env` di VPS (password akun hanya ada di sini)

```bash
ssh user@IP_VPS
cd ~/ai-trading
curl -fsSL https://raw.githubusercontent.com/mhammdalwii/ai-trading/main/.env.example -o .env
nano .env
chmod 600 .env
```

Isi minimal:

```
DATA_SOURCE=mt5
MT5_LOGIN=12345678
MT5_PASSWORD=password_akun_demo
MT5_SERVER=NamaBroker-Demo
MT5_BRIDGE_HOST=mt5
COMPOSE_PROFILES=mt5
VNC_PASSWORD=password_kuat_untuk_layar_mt5
```

`MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` **tidak pernah** disimpan di repo atau di GitHub.
CI/CD hanya mengirim `docker-compose.yml` dan skrip deploy; file `.env` di VPS tidak disentuh.

## 3. Isi GitHub Secrets

Di GitHub: **Settings > Secrets and variables > Actions > New repository secret**.

| Secret | Isi |
|---|---|
| `VPS_HOST` | IP atau domain VPS |
| `VPS_USER` | User SSH di VPS (yang sudah masuk grup docker) |
| `VPS_SSH_KEY` | Private key khusus deploy (lihat di bawah) |
| `VPS_KNOWN_HOSTS` | Hasil `ssh-keyscan IP_VPS` dari komputer Anda (disarankan) |
| `VPS_PORT` | Opsional, default 22 |
| `VPS_APP_DIR` | Opsional, default `ai-trading` (relatif ke home user) |

Membuat kunci deploy (di laptop):

```bash
ssh-keygen -t ed25519 -f deploy_key -N "" -C "github-actions-deploy"
ssh-copy-id -i deploy_key.pub user@IP_VPS
cat deploy_key          # salin ke secret VPS_SSH_KEY, lalu hapus file ini
ssh-keyscan IP_VPS      # salin ke secret VPS_KNOWN_HOSTS
```

Selama `VPS_HOST` belum diisi, workflow tetap menjalankan tes dan build, lalu melewati deploy.

## 4. Deploy pertama

Push ke `main` (atau buka tab **Actions > CI/CD > Run workflow**). Workflow akan:

1. Menjalankan semua tes.
2. Build image dan push ke `ghcr.io/mhammdalwii/ai-trading` (tag `latest` dan SHA commit).
3. SSH ke VPS, menarik image, lalu `docker compose up -d` dan menunggu container sehat.

Container `mt5` pertama kali butuh **sekitar 5 sampai 10 menit** untuk memasang Wine, MT5, dan Python.
Dashboard otomatis tersambung ulang ke MT5 setiap 30 detik sampai siap.

## 5. Login MT5 dan aktifkan Algo Trading (sekali saja)

Buka tunnel dari laptop:

```bash
ssh -L 8000:localhost:8000 -L 3000:localhost:3000 user@IP_VPS
```

1. Buka **http://localhost:3000**, login dengan `VNC_USER` / `VNC_PASSWORD`.
2. Di MT5: **File > Login to Trade Account**, pilih server broker dan login akun **demo**.
   (Aplikasi juga login otomatis memakai `MT5_LOGIN`, tapi server broker harus sudah dikenal terminal.)
3. Klik tombol **Algo Trading** di toolbar sampai hijau, dan di **Tools > Options > Expert Advisors**
   centang **Allow algorithmic trading**.
4. Cek simbol di Market Watch. Jika ada akhiran (misalnya `XAUUSD.m`), isi `SYMBOL_SUFFIX` di `.env`
   lalu `docker compose up -d`.

Pengaturan ini tersimpan di volume `mt5_config`, jadi tidak hilang saat deploy berikutnya.

Lalu buka **http://localhost:8000** untuk dashboard. Cek **http://localhost:8000/api/health**:
`data_source` harus `mt5-bridge` dan `connected` harus `true`.

## Perintah berguna di VPS

```bash
cd ~/ai-trading
docker compose ps                 # status container
docker compose logs -f app        # log dashboard
docker compose logs -f mt5        # log instalasi/terminal MT5
docker compose restart app        # restart dashboard (Auto-Trigger kembali OFF)
APP_IMAGE=ghcr.io/mhammdalwii/ai-trading:<sha> bash deploy/remote-deploy.sh   # rollback ke versi lama
```

## Mencoba di komputer sendiri (tanpa MT5)

```bash
cp .env.example .env      # DATA_SOURCE=mock
docker compose up -d --build
```

Buka http://localhost:8000.

## Membuka dashboard tanpa SSH tunnel (opsional, nanti)

Jangan ganti `APP_BIND=0.0.0.0` begitu saja: Docker melewati aturan `ufw`, sehingga port langsung terbuka
ke internet. `DASHBOARD_TOKEN` hanya melindungi aksi trading, bukan data akun. Jika ingin akses dari HP,
lebih aman pakai VPN (misalnya Tailscale) atau reverse proxy HTTPS dengan login.

## Catatan

- Image `gmag11/metatrader5_vnc` adalah proyek komunitas, bukan dari MetaQuotes. Versinya dikunci di
  `2.3` di `docker-compose.yml`; naikkan versi secara sengaja setelah dicoba.
- Paket GHCR dibuat privat secara default. Workflow login ke GHCR memakai token sementara saat deploy,
  jadi tidak perlu membuat paket publik.
- Selalu uji beberapa minggu di akun demo sebelum memakai akun real.
