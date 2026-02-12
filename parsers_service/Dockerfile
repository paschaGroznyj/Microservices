FROM python:3.10-slim

WORKDIR /app

# 1. Системные зависимости (для Chromium и утилит)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    gnupg \
    unzip \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# 2. Устанавливаем Python-зависимости (включая playwright!)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Создаём пользователя
RUN useradd -m -u 1000 appuser

# 4. Playwright: устанавливаем браузер от имени appuser
USER appuser
RUN playwright install chromium
USER root

# 5. Копируем код и настраиваем права
COPY . .
RUN mkdir -p /app/knowledge_base/articles /app/logs /app/pw_profile_scholar \
    && chown -R appuser:appuser /app

# 6. Переключаемся на appuser
USER appuser

# 7. Настройки
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8003

CMD ["python", "app/app.py"]