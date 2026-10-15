FROM python:3.10-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# ca-certificates: базовый TLS-стор. В корпоративной сети MAX API (platform-api2.max.ru)
# проходит через MITM-прокси со своим Root CA — положи его в max_service/certs/*.crt,
# он добавится в системный стор при сборке (путь также доступен через env MAX_CA_BUNDLE).
RUN apt-get update \
    && apt-get install -y ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV UV_SYSTEM_PYTHON=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

COPY requirements.txt .
RUN uv pip install --no-cache-dir -r requirements.txt

# Опциональные корпоративные сертификаты (папка может быть пустой/отсутствовать)
COPY . .
RUN if ls /app/certs/*.crt >/dev/null 2>&1 || ls /app/certs/*.pem >/dev/null 2>&1; then \
        cp /app/certs/*.crt /app/certs/*.pem /usr/local/share/ca-certificates/ 2>/dev/null || true; \
        update-ca-certificates || true; \
    fi

RUN useradd -m -u 1003 appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "app/bot.py"]
