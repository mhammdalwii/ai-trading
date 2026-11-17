#!/usr/bin/env bash
# Persiapan sekali jalan untuk VPS Ubuntu 22.04/24.04 (x86_64).
# Jalankan sebagai user biasa yang punya sudo:   bash setup-vps.sh
set -euo pipefail

if [ "$(uname -m)" != "x86_64" ]; then
  echo "MT5 di Wine hanya didukung di VPS x86_64 (amd64), bukan ARM." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Memasang Docker Engine + plugin compose (repo resmi Docker)..."
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"

mkdir -p ~/ai-trading/deploy

# Firewall: hanya SSH yang dibuka. Port dashboard (8000) dan layar MT5 (3000) di-bind ke 127.0.0.1
# oleh docker-compose, jadi dibuka lewat SSH tunnel.
if command -v ufw >/dev/null 2>&1; then
  sudo ufw allow OpenSSH
  sudo ufw --force enable
fi

cat <<'MSG'

Selesai. Langkah berikutnya:
  1. Logout lalu login lagi (agar grup docker aktif).
  2. Buat file ~/ai-trading/.env dari .env.example di repo, isi MT5_LOGIN/MT5_PASSWORD/MT5_SERVER,
     DATA_SOURCE=mt5, MT5_BRIDGE_HOST=mt5, COMPOSE_PROFILES=mt5, VNC_PASSWORD.
     Lalu: chmod 600 ~/ai-trading/.env
  3. Isi secret GitHub (VPS_HOST, VPS_USER, VPS_SSH_KEY, VPS_KNOWN_HOSTS) dan push ke main.
Panduan lengkap: docs/DEPLOY.md
MSG
