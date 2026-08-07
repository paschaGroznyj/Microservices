FROM python:3.10-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_SYSTEM_PYTHON=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
    
# ������������� ��������� ����������� (���� ����� ��� �����-�� �������)
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# �������� � ������������� �����������
COPY requirements.txt .
RUN uv pip install --no-cache-dir -r requirements.txt

# �������� ���� ��� �������
COPY . .

# ������� ������������ ��� ������������
RUN useradd -m -u 1500 appuser && chown -R appuser:appuser /app
USER appuser

# ���������� ���������
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# ��������� ����
EXPOSE 8001

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]