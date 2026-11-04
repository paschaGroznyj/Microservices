FROM python:3.10-slim AS runtime

WORKDIR /app

# 1. Системные зависимости (для Chromium и утилит)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# 2. Устанавливаем Python-зависимости (включая playwright!)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Создаём пользователя
RUN useradd -m -u 1002 appuser

## 4. Playwright: устанавливаем браузер от имени appuser
USER appuser
RUN playwright install chromium
USER root

# 5. Копируем код и настраиваем права
COPY . .

# 6. Переключаемся на appuser
USER appuser

# 7. Настройки
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8003

CMD ["python", "app/main.py"]


# ---- test: pytest и плагины только здесь, в prod-образ не попадают ----
# docker compose -f docker-compose.test.yml run --rm parsers_service_test
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
