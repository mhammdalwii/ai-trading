#!/usr/bin/env bash
# Dijalankan di VPS oleh GitHub Actions (job "deploy"). Bisa juga dijalankan manual:
#   cd ~/ai-trading && APP_IMAGE=ghcr.io/mhammdalwii/ai-trading:latest bash deploy/remote-deploy.sh
# Token GHCR (opsional) dibaca dari stdin. File .env TIDAK pernah dikirim dari CI: buat sekali di VPS.
set -euo pipefail

: "${APP_IMAGE:?APP_IMAGE belum diisi}"
export APP_IMAGE

if [ ! -f .env ]; then
  echo "File .env belum ada di $(pwd). Salin dari .env.example lalu isi (lihat docs/DEPLOY.md)." >&2
  exit 1
fi

token=""
if [ ! -t 0 ]; then
  read -r token || true
fi
if [ -n "$token" ]; then
  printf '%s\n' "$token" | docker login ghcr.io -u "${GHCR_USER:-github}" --password-stdin >/dev/null
fi

echo "Menarik image $APP_IMAGE"
pull_status=0
docker compose pull --quiet app || pull_status=$?
if [ -n "$token" ]; then
  docker logout ghcr.io >/dev/null || true
fi
[ "$pull_status" -eq 0 ] || exit "$pull_status"

# Container mt5 ikut jalan jika COMPOSE_PROFILES=mt5 di .env; data/login MT5 tetap di volume.
docker compose up -d --remove-orphans

echo "Menunggu container app sehat..."
for _ in $(seq 1 30); do
  status=$(docker inspect -f '{{.State.Health.Status}}' ai-trading-app 2>/dev/null || echo unknown)
  if [ "$status" = "healthy" ]; then
    echo "Deploy selesai: $APP_IMAGE"
    docker image prune -f >/dev/null || true
    exit 0
  fi
  sleep 3
done

echo "Container app belum sehat setelah 90 detik. Log terakhir:" >&2
docker compose logs --tail 50 app >&2
exit 1
