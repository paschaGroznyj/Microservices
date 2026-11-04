FROM python:3.10-slim AS runtime

WORKDIR /app

# Системные зависимости
RUN apt-get update && apt-get install -y \
    iputils-ping \
    curl \
    net-tools \
    dnsutils \
    ca-certificates \
    git \
    tree \
    grep \
    findutils \
    coreutils \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# ca-certificates только для локальной отладки
 
# Копируем и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код сервиса
COPY . .

# Создаем пользователя для безопасности
RUN useradd -m -u 1002 appuser && chown -R appuser:appuser /app
USER appuser

# Переменные окружения
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 9001

CMD ["python", "app/main.py"]


# ---- test: pytest и плагины только здесь, в prod-образ не попадают ----
# docker compose -f docker-compose.test.yml run --rm agent_service_test
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
