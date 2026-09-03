FROM python:3.10-slim

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

EXPOSE 7010

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7010", "--reload"]