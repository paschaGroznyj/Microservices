FROM python:3.10-slim AS runtime

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers \
    MPLCONFIGDIR=/tmp/matplotlib_cache \
    NPM_CONFIG_CACHE=/tmp/npm_cache \
    XDG_DATA_HOME=/tmp/.local/share \
    XDG_CONFIG_HOME=/tmp/.config \
    XDG_CACHE_HOME=/tmp/.cache \
    HOME=/tmp \
    UV_SYSTEM_PYTHON=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    curl \
    gnupg \
    git \
    chromium \
    chromium-driver \
    iproute2 \
    libreoffice \
    libreoffice-impress \
    libreoffice-draw \
    poppler-utils \
    fonts-liberation \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*



RUN install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg \
      -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) \
signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
      > /etc/apt/sources.list.d/docker.list && \
    apt-get update && apt-get install -y --no-install-recommends \
      docker-ce-cli docker-compose-plugin docker-buildx-plugin && \
    rm -rf /var/lib/apt/lists/*

# --- Node.js (нужен под npx / @playwright/mcp) ---
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*


COPY requirements-sandbox.txt .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r requirements-sandbox.txt

RUN useradd -m -u 1500 -s /usr/sbin/nologin sandboxrunner

# Полный chromium + headless shell: channel="chromium" и headless=False
# (headless=False под Xvfb: `Xvfb :99 &` + DISPLAY=:99; xvfb-run без xauth не работает).
RUN playwright install chromium && playwright install-deps chromium

RUN --mount=type=cache,target=/tmp/npm_cache \
    npm install --no-save animejs
RUN --mount=type=cache,target=/tmp/npm_cache \
    npm install -g @playwright/mcp@latest ctx7 skillui

# /opt/playwright-browsers не chown-им: браузерам хватает прав на чтение/запуск,
# а chown -R копирует их в новый слой целиком (+650 МБ).
RUN mkdir -p /tmp/matplotlib_cache /tmp/npm_cache /tmp/.local /tmp/.config /tmp/.cache /home/sandboxrunner/.local && \
    chmod 1777 /tmp/matplotlib_cache /tmp/npm_cache /tmp/.local /tmp/.config /tmp/.cache && \
    chown -R sandboxrunner:sandboxrunner /app /home/sandboxrunner

RUN npx -y skills add -y emilkowalski/skills
RUN npx skills

# КОД копируем В САМОМ КОНЦЕ — это уже так и есть
COPY . .

USER sandboxrunner
RUN python3 -c "import matplotlib.pyplot"
EXPOSE 8005
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8005"]


# ---- test: pytest и плагины только здесь, в prod-образ не попадают ----
# docker compose -f docker-compose.test.yml run --rm agent_sandbox_test
FROM runtime AS test
USER root
# /app/__init__.py делает /app пакетом: pytest кладёт в sys.path корень /, и `import app`
# находит каталог /app вместо app.py / app/. Убираем только в тестовом образе.
RUN rm -f /app/__init__.py
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt
USER sandboxrunner
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]

# ---- prod: последняя стадия = цель по умолчанию для docker build / compose ----
FROM runtime AS prod
