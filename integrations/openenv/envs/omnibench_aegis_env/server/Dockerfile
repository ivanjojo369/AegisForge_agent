FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY integrations/openenv/envs/omnibench_aegis_env/server/requirements.txt /tmp/requirements.txt

RUN python -m pip install --upgrade pip && \
    pip install -r /tmp/requirements.txt

# Entorno OpenEnv transferido/integrado
COPY integrations/openenv/envs/omnibench_aegis_env /app/integrations/openenv/envs/omnibench_aegis_env

# Datos de harness para seeds, misiones o contratos del entorno
COPY harness/AegisForge_scenarios/data/openenv /app/harness/AegisForge_scenarios/data/openenv

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "integrations.openenv.envs.omnibench_aegis_env.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
