FROM python:3.10-slim

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

CMD ["python", "app/app.py"]