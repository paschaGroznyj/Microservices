FROM python:3.10-slim

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