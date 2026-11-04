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
    XDG_CACHE_HOME=/tmp/.cache \
    HOME=/tmp \
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

RUN playwright install chromium
RUN playwright install-deps chromium

RUN --mount=type=cache,target=/tmp/npm_cache \
    npm install --no-save animejs
RUN --mount=type=cache,target=/tmp/npm_cache \
    npm install -g @playwright/mcp@latest ctx7 skillui

RUN mkdir -p /tmp/matplotlib_cache /tmp/npm_cache /tmp/.local /tmp/.config /tmp/.cache /home/sandboxrunner/.local && \
    chmod 1777 /tmp/matplotlib_cache /tmp/npm_cache /tmp/.local /tmp/.config /tmp/.cache && \
    chown -R sandboxrunner:sandboxrunner /app /opt/playwright-browsers /home/sandboxrunner

RUN npx -y skills add -y emilkowalski/skills
RUN npx skills

# КОД копируем В САМОМ КОНЦЕ — это уже так и есть
COPY . .

USER sandboxrunner
RUN python3 -c "import matplotlib.pyplot"
EXPOSE 8005
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8005"]