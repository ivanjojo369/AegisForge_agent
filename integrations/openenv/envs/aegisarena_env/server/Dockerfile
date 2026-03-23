FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8012

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY openenv.yaml /app/openenv.yaml
COPY models.py /app/models.py
COPY client.py /app/client.py
COPY config.py /app/config.py
COPY mission_registry.py /app/mission_registry.py
COPY reward.py /app/reward.py
COPY state_engine.py /app/state_engine.py
COPY seeds.py /app/seeds.py
COPY missions /app/missions
COPY data /app/data
COPY server /app/server

EXPOSE 8012

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8012/health || exit 1

CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8012"]
