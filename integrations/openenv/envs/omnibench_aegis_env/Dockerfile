FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV OPENENV_EVAL_LAB_GIT=/usr/bin/git

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user

WORKDIR /workspace

COPY --chown=user:user server/requirements.txt /workspace/requirements.txt
RUN pip install --upgrade pip && pip install -r /workspace/requirements.txt

COPY --chown=user:user . /workspace/omnibench_aegis_env

ENV PYTHONPATH=/workspace

USER user

CMD ["python", "-m", "uvicorn", "omnibench_aegis_env.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
