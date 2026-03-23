# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Minimal runtime deps.
# bash is installed because run.sh currently uses bash-specific syntax.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Copy project files needed for install + runtime
COPY pyproject.toml ./pyproject.toml
COPY uv.lock ./uv.lock
COPY README.md ./README.md
COPY requirements.txt ./requirements.txt
COPY run.sh ./run.sh
COPY src ./src

# Install dependencies + local package
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir .

# Make entrypoint executable
RUN chmod +x /app/run.sh

# Run as non-root
RUN useradd -m -u 10001 appuser
USER appuser

EXPOSE 8000

# Official submission path:
# Dockerfile -> run.sh -> src/aegisforge/a2a_server.py
#
# AgentBeats can append:
#   --host --port --card-url
ENTRYPOINT ["bash", "/app/run.sh"]
