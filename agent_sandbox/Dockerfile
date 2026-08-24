FROM python:3.10-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers \
    MPLCONFIGDIR=/tmp/matplotlib_cache \
    NPM_CONFIG_CACHE=/tmp/npm_cache \
    XDG_DATA_HOME=/tmp/.local/share \
    XDG_CONFIG_HOME=/tmp/.config \
    UV_SYSTEM_PYTHON=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libcairo2-dev \
    pkg-config \
    python3-dev \
    build-essential \
    curl \
    gnupg \
    git \
    chromium \
    chromium-driver \
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
RUN uv pip install --no-cache -r requirements-sandbox.txt

RUN useradd -m -u 1500 -s /usr/sbin/nologin sandboxrunner

RUN playwright install chromium
RUN playwright install-deps chromium

RUN npm install --no-save animejs

# --- Устанавливаем сам MCP-сервер глобально, чтобы npx не лез в сеть на рантайме ---
RUN npm install -g @playwright/mcp@latest ctx7 skillui

# --- Предварительное создание временных папок и каталога пользователя ---
RUN mkdir -p /tmp/matplotlib_cache /tmp/npm_cache /tmp/.local /tmp/.config /home/sandboxrunner/.local && \
    chmod 1777 /tmp/matplotlib_cache /tmp/npm_cache /tmp/.local /tmp/.config && \
    chown -R sandboxrunner:sandboxrunner /app /opt/playwright-browsers /home/sandboxrunner

    
RUN npx -y skills add -y emilkowalski/skills

COPY app.py .

USER sandboxrunner

RUN python3 -c "import matplotlib.pyplot"
EXPOSE 8005

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8005"]