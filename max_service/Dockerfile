FROM python:3.10-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_SYSTEM_PYTHON=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

COPY requirements.txt .
RUN uv pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m -u 1003 appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "app/bot.py"]
