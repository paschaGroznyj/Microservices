FROM python:3.10-slim AS runtime

WORKDIR /app

# Копируем uv из официального образа
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Разрешаем uv ставить пакеты в системный Python контейнера
ENV UV_SYSTEM_PYTHON=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Создаем пользователя заранее
RUN useradd -m -u 1500 appuser

# Установка зависимостей (кэшируется Docker'ом)
COPY requirements.txt .
RUN uv pip install --no-cache -r requirements.txt

# Копируем исходный код сразу с нужными правами
COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 7000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000", "--timeout-graceful-shutdown", "60"]


# ---- test: pytest и плагины только здесь, в prod-образ не попадают ----
# docker compose -f docker-compose.test.yml run --rm api_gateway_test
FROM runtime AS test
USER root
# /app/__init__.py делает /app пакетом: pytest кладёт в sys.path корень /, и `import app`
# находит каталог /app вместо app.py / app/. Убираем только в тестовом образе.
RUN rm -f /app/__init__.py
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt
USER appuser
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]

# ---- prod: последняя стадия = цель по умолчанию для docker build / compose ----
FROM runtime AS prod
