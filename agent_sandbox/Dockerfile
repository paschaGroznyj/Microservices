FROM python:3.10-slim

WORKDIR /app

# Отключаем генерацию кэша .pyc, так как контейнер read-only
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Фиксируем путь для сборки и запуска
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers \
    MPLCONFIGDIR=/tmp/matplotlib_cache
# НЕ ЗАБЫВАТЬ ПРОПИСЫВАТЬ ENV ВНУТРИ app.py

RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libcairo2-dev \
    pkg-config \
    python3-dev \
    build-essential \
    curl \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-sandbox.txt .
RUN pip install --no-cache-dir -r requirements-sandbox.txt
RUN pip install --no-cache-dir fastapi "uvicorn[standard]"

# Создаем пользователя
RUN useradd -m -u 1500 -s /usr/sbin/nologin sandboxrunner

# Скачиваем Chromium и ЕГО зависимости (библиотеки) через playwright
RUN playwright install chromium
RUN playwright install-deps chromium

# Создаем нужные папки и передаем права пользователю
RUN mkdir -p /tmp/matplotlib_cache && chmod 1777 /tmp/matplotlib_cache \
    && chown -R sandboxrunner:sandboxrunner /app /opt/playwright-browsers

COPY app.py .

USER sandboxrunner

RUN python3 -c "import matplotlib.pyplot"
EXPOSE 8005

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8005"]
