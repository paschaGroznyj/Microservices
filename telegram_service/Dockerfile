FROM python:3.10-slim AS runtime

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Переменные окружения
ENV UV_SYSTEM_PYTHON=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
 
# Копируем и устанавливаем зависимости
COPY requirements.txt .
RUN uv pip install --no-cache-dir -r requirements.txt

# Копируем весь код сервиса
COPY . .

# Создаем пользователя для безопасности
RUN useradd -m -u 1002 appuser && chown -R appuser:appuser /app
USER appuser


CMD ["python", "app/bot.py"]


# ---- test: pytest и плагины только здесь, в prod-образ не попадают ----
# docker compose -f docker-compose.test.yml run --rm telegram_service_test
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
