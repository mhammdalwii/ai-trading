# AI Trading Dashboard: backend FastAPI + dashboard web dalam satu image Linux.
# MT5 tidak ada di image ini. Di VPS Linux, MT5 berjalan di container terpisah (Wine)
# dan dihubungi lewat MT5_BRIDGE_HOST. Lihat docs/DEPLOY.md.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/backend

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/app ./app
COPY frontend /app/frontend

# Jalankan sebagai user biasa, bukan root. Folder data menyimpan log order (dipasang sebagai volume).
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/backend/data \
    && chown -R app:app /app/backend/data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/symbols', timeout=4)" || exit 1

# Di dalam container harus 0.0.0.0. Pembatasan akses diatur di docker-compose (port publish).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
