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
# Supported repo layouts:
#   data/aegisforge_unified_purple_agent.db
#   data/unified_purple_agent/aegisforge_unified_purple_agent.db
#   data/unified_purple_agent/manifest.json
COPY data ./data

# CRMArena/Salesforce data path used by src/aegisforge/agent.py.
# Do not set or modify OPENAI_API_KEY here; AgentBeats/Amber injects secrets at runtime.
#
# Keep the runtime DB path canonical at /app/data/aegisforge_unified_purple_agent.db.
# The validation block below will copy/sync from data/unified_purple_agent/ if needed.
ENV AEGISFORGE_CRM_DB_PATH=/app/data/aegisforge_unified_purple_agent.db \
    AEGISFORGE_UNIFIED_PURPLE_DATA_MANIFEST=/app/data/unified_purple_agent/manifest.json \
    AEGISFORGE_CRM_ENABLE_PUBLIC_METADATA=true \
    AEGISFORGE_CRM_HF_TIMEOUT_SECONDS=5 \
    AEGISFORGE_CRM_LLM_TIMEOUT_SECONDS=18 \
    AEGISFORGE_CRM_MAX_CONTEXT_CHARS=26000

# Verify Unified Purple local assets without blocking the build on a partial schema.
#
# Important:
# - Missing DB/manifest is still a build error.
# - A DB that does not yet contain full CRMArena/Salesforce tables is only a warning.
#   This keeps the image usable for the broader Unified Purple Agent strategy while
#   allowing runtime fallback to public/local metadata and LLM support when available.
RUN test -d /app/data \
 && python - <<'DOCKER_PY'
import json
import os
import shutil
import sqlite3
from pathlib import Path

canonical_db = Path(os.environ["AEGISFORGE_CRM_DB_PATH"])
manifest_path = Path(os.environ["AEGISFORGE_UNIFIED_PURPLE_DATA_MANIFEST"])

db_candidates = [
    canonical_db,
    Path("/app/data/unified_purple_agent/aegisforge_unified_purple_agent.db"),
    Path("/app/data/aegisforge_unified_purple_agent.db"),
]
db_candidates.extend(sorted(Path("/app/data").glob("**/*.db")))

manifest_candidates = [
    manifest_path,
    Path("/app/data/manifest.json"),
]
manifest_candidates.extend(sorted(Path("/app/data").glob("**/manifest.json")))

def first_existing(paths):
    seen = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and path.is_file() and path.stat().st_size > 0:
            return path
    return None

found_db = first_existing(db_candidates)
found_manifest = first_existing(manifest_candidates)

if found_db is None:
    raise SystemExit(
        "[Dockerfile] Missing Unified Purple SQLite DB. Expected one of: "
        "/app/data/aegisforge_unified_purple_agent.db or "
        "/app/data/unified_purple_agent/aegisforge_unified_purple_agent.db"
    )

if found_manifest is None:
    raise SystemExit(
        "[Dockerfile] Missing Unified Purple manifest. Expected: "
        "/app/data/unified_purple_agent/manifest.json"
    )

canonical_db.parent.mkdir(parents=True, exist_ok=True)
manifest_path.parent.mkdir(parents=True, exist_ok=True)

if found_db.resolve() != canonical_db.resolve():
    shutil.copy2(found_db, canonical_db)
    print(f"[Dockerfile] Copied DB into canonical runtime path: {found_db} -> {canonical_db}", flush=True)

if found_manifest.resolve() != manifest_path.resolve():
    shutil.copy2(found_manifest, manifest_path)
    print(f"[Dockerfile] Copied manifest into canonical path: {found_manifest} -> {manifest_path}", flush=True)

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
print(f"[Dockerfile] Unified Purple manifest: {manifest_path}", flush=True)
print(f"[Dockerfile] Unified Purple DB: {canonical_db}", flush=True)
print(f"[Dockerfile] Manifest default_profile: {manifest.get('default_profile', '')}", flush=True)

tables = set()
sqlite_ok = False
try:
    conn = sqlite3.connect(canonical_db)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        sqlite_ok = True
    finally:
        conn.close()
except Exception as exc:
    print(f"[Dockerfile] WARNING: SQLite inspection failed: {exc.__class__.__name__}: {exc}", flush=True)

required_any = {"Account", "Opportunity", "Product2", "Case"}
matched = required_any & tables

print(f"[Dockerfile] SQLite inspection ok: {int(sqlite_ok)}", flush=True)
print(f"[Dockerfile] SQLite tables detected: {sorted(tables)[:80]}", flush=True)
print(f"[Dockerfile] Salesforce/CRMArena table matches: {sorted(matched)}", flush=True)

if sqlite_ok and len(matched) >= 3:
    print("[Dockerfile] CRMArena/Salesforce DB schema looks usable.", flush=True)
else:
    print(
        "[Dockerfile] WARNING: Unified Purple DB exists but does not yet look "
        "like a full CRMArena/Salesforce SQLite DB. Build will continue; "
        "runtime can still use fallback/public/local metadata if needed.",
        flush=True,
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
