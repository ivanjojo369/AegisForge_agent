# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GIT_TERMINAL_PROMPT=0

WORKDIR /app

# Runtime deps only.
# - bash is required by run.sh
# - ca-certificates is useful for HTTPS calls such as public CRMArena metadata fallback
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Copy project files needed for install + runtime.
COPY pyproject.toml ./pyproject.toml
COPY uv.lock ./uv.lock
COPY README.md ./README.md
COPY requirements.txt ./requirements.txt
COPY run.sh ./run.sh
COPY src ./src

# Copy AegisForge Unified Purple local data.
# Expected repo layout:
#   data/unified_purple_agent/manifest.json
#   data/unified_purple_agent/aegisforge_unified_purple_agent.db
COPY data ./data

# CRMArena/Salesforce data path used by src/aegisforge/agent.py.
# Do not set or modify OPENAI_API_KEY here; AgentBeats/Amber injects secrets at runtime.
ENV AEGISFORGE_CRM_DB_PATH=/app/data/unified_purple_agent/aegisforge_unified_purple_agent.db \
    AEGISFORGE_UNIFIED_PURPLE_DATA_MANIFEST=/app/data/unified_purple_agent/manifest.json \
    AEGISFORGE_CRM_ENABLE_PUBLIC_METADATA=true \
    AEGISFORGE_CRM_HF_TIMEOUT_SECONDS=5 \
    AEGISFORGE_CRM_LLM_TIMEOUT_SECONDS=18 \
    AEGISFORGE_CRM_MAX_CONTEXT_CHARS=26000

# Fail the image build early if the Unified Purple SQLite asset is missing or invalid.
RUN test -s "$AEGISFORGE_CRM_DB_PATH" \
 && test -s "$AEGISFORGE_UNIFIED_PURPLE_DATA_MANIFEST" \
 && python - <<'DOCKER_PY'
import json
import os
import sqlite3
from pathlib import Path

manifest_path = Path(os.environ["AEGISFORGE_UNIFIED_PURPLE_DATA_MANIFEST"])
db_path = Path(os.environ["AEGISFORGE_CRM_DB_PATH"])

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
print(f"[Dockerfile] Unified Purple manifest: {manifest_path}", flush=True)
print(f"[Dockerfile] Unified Purple DB: {db_path}", flush=True)
print(f"[Dockerfile] Manifest default_profile: {manifest.get('default_profile', '')}", flush=True)

conn = sqlite3.connect(db_path)
try:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
finally:
    conn.close()

required_any = {"Account", "Opportunity", "Product2", "Case"}
matched = required_any & tables
print(f"[Dockerfile] SQLite tables detected: {sorted(tables)[:40]}", flush=True)
print(f"[Dockerfile] Salesforce/CRMArena table matches: {sorted(matched)}", flush=True)

if len(matched) < 3:
    raise SystemExit(
        "Unified Purple DB does not look like a CRMArena/Salesforce SQLite DB. "
        f"Expected at least 3 of {sorted(required_any)}, got {sorted(matched)}."
    )
DOCKER_PY

# Install dependencies + local package.
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir .

# OpenAI-compatible endpoint defaults.
# The leaderboard scenario passes API keys via Amber/runtime secrets when available.
# This Dockerfile deliberately does not set OPENAI_API_KEY.
ENV OPENAI_BASE_URL=https://api.openai.com/v1 \
    AEGISFORGE_LLM_TIMEOUT_SECONDS=75 \
    AEGISFORGE_MAX_LLM_CALLS_PER_RESPONSE=1 \
    AEGISFORGE_RUNTIME_PROFILE=crmarena_unified_purple_db_ready

# Make entrypoint executable.
RUN chmod +x /app/run.sh

# Run as non-root.
RUN useradd -m -u 10001 appuser \
 && chown -R appuser:appuser /app
USER appuser

# AgentBeats can pass --port at runtime. Expose both common local defaults.
EXPOSE 8000 8001

# Official submission path:
# Dockerfile -> run.sh -> src/aegisforge/a2a_server.py
# AgentBeats can append:
#   --host --port --card-url
ENTRYPOINT ["bash", "/app/run.sh"]
