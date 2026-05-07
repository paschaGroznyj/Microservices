FROM python:3.10-slim

WORKDIR /app

# ��������� �����������
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python �����������
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ��� ����������
COPY . .


RUN useradd -m -u 1500 appuser && chown -R appuser:appuser /app

USER appuser

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 7000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000", "--reload"]
