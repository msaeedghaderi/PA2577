FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
# RUN mkdir -p /data
# VOLUME ["/data"]
EXPOSE 8081
ENV MT_SAMPLE_INTERVAL_SECONDS=300     MT_DB_URL=postgresql+psycopg://postgres:postgres@db:5432/postgres     MT_TRACK_TABLES=files,chunks,candidates,clones,statusUpdates     MT_STATUS_UPDATES_TABLE=statusUpdates     MT_SQLITE_PATH=/data/monitor.sqlite     MT_TIMEZONE=UTC
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
