FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y     gcc     libpq-dev     curl     && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . .

CMD ["python", "app/main.py"]
