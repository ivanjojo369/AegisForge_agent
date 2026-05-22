# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GIT_TERMINAL_PROMPT=0

WORKDIR /app

# Runtime deps.
# bash is required by run.sh.
# git is used only at build time to fetch a sparse OfficeQA source-corpus checkout.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    git \
  && rm -rf /var/lib/apt/lists/*

# Copy project files needed for install + runtime.
COPY pyproject.toml ./pyproject.toml
COPY uv.lock ./uv.lock
COPY README.md ./README.md
COPY requirements.txt ./requirements.txt
COPY run.sh ./run.sh
COPY src ./src

# Install dependencies + local package.
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir .

# OfficeQA public source corpus.
#
# IMPORTANT:
# - This fetches only the public source-document corpus paths needed for retrieval.
# - It intentionally does NOT copy officeqa.csv because that file contains answer labels.
# - agent.py must still ignore answer/gold/ground_truth/solution-like fields if future
#   files are added under this directory.
#
# Why sparse git instead of downloading the GitHub repo ZIP?
# - The full archive can make Docker builds look frozen because urllib has no progress output.
# - Sparse checkout gives progress, avoids unnecessary blobs, and fails faster on network issues.
ARG OFFICEQA_CORPUS_COMMIT=6aa8c32ba38ef9baf7e88c9c592d16a024090953
ARG OFFICEQA_REPO_URL=https://github.com/databricks/officeqa.git

ENV AEGISFORGE_OFFICEQA_DATA_DIR=/app/data/officeqa \
    AEGISFORGE_OFFICEQA_CORPUS_DIR=/app/data/officeqa/treasury_bulletins_parsed \
    AEGISFORGE_OFFICEQA_ENABLE_RAG=true \
    AEGISFORGE_OFFICEQA_CORPUS_URL=https://github.com/databricks/officeqa/tree/${OFFICEQA_CORPUS_COMMIT}/treasury_bulletins_parsed \
    AEGISFORGE_OFFICEQA_CORPUS_COMMIT=${OFFICEQA_CORPUS_COMMIT}

RUN set -eux; \
    git config --global http.lowSpeedLimit 1000; \
    git config --global http.lowSpeedTime 60; \
    mkdir -p "${AEGISFORGE_OFFICEQA_DATA_DIR}" /tmp/officeqa_download; \
    cd /tmp/officeqa_download; \
    git init officeqa; \
    cd officeqa; \
    git remote add origin "${OFFICEQA_REPO_URL}"; \
    git config core.sparseCheckout true; \
    git config core.sparseCheckoutCone true; \
    printf '%s\n' \
      '/treasury_bulletins_parsed/jsons/' \
      '/treasury_bulletins_parsed/transformed/' \
      > .git/info/sparse-checkout; \
    echo "[Dockerfile] Sparse-fetching OfficeQA source corpus commit ${OFFICEQA_CORPUS_COMMIT}"; \
    git fetch --depth 1 --filter=blob:none --progress origin "${OFFICEQA_CORPUS_COMMIT}"; \
    git checkout --progress FETCH_HEAD; \
    mkdir -p "${AEGISFORGE_OFFICEQA_DATA_DIR}"; \
    cp -a treasury_bulletins_parsed "${AEGISFORGE_OFFICEQA_DATA_DIR}/"; \
    rm -rf "${AEGISFORGE_OFFICEQA_DATA_DIR}/treasury_bulletins_parsed/transform_scripts" || true; \
    rm -rf /tmp/officeqa_download

# Safety filter and corpus metadata.
RUN set -eux; \
    find "${AEGISFORGE_OFFICEQA_DATA_DIR}" -type f \( \
      -iname '*answer*' -o \
      -iname '*gold*' -o \
      -iname '*ground_truth*' -o \
      -iname '*correct_answer*' -o \
      -iname '*expected_answer*' -o \
      -iname '*solution*' -o \
      -iname '*results*' -o \
      -iname '*provenance*' -o \
      -iname '*leaderboard*' -o \
      -iname '*submission*' -o \
      -iname '*secret*' -o \
      -iname '*token*' -o \
      -iname '*credential*' -o \
      -iname '*api_key*' \
    \) -delete; \
    python - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["AEGISFORGE_OFFICEQA_DATA_DIR"])
corpus = root / "treasury_bulletins_parsed"
files = [p for p in corpus.rglob("*") if p.is_file()]
metadata = {
    "source": "databricks/officeqa",
    "commit": os.environ.get("AEGISFORGE_OFFICEQA_CORPUS_COMMIT", ""),
    "included_tree": "treasury_bulletins_parsed/{jsons,transformed}",
    "excluded": [
        "officeqa.csv",
        "answer/gold/ground_truth/solution/results/provenance-like filenames",
        "transform_scripts",
    ],
    "file_count": len(files),
    "sample_files": [str(p.relative_to(root)) for p in files[:10]],
}
(root / "OFFICEQA_CORPUS_METADATA.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
print(f"[Dockerfile] OfficeQA source corpus file count after safety filter: {len(files)}", flush=True)
print(f"[Dockerfile] OfficeQA corpus root: {corpus}", flush=True)
if not files:
    raise SystemExit("OfficeQA source corpus is empty after sparse checkout/safety filter.")
PY

# OpenAI-compatible endpoint defaults.
#
# The leaderboard scenario passes OPENAI_API_KEY to the participant when available.
# We do not set or modify that secret here. This default only gives agent.py a
# standard OpenAI-compatible base URL to use when the key exists.
ENV OPENAI_BASE_URL=https://api.openai.com/v1 \
    AEGISFORGE_LLM_TIMEOUT_SECONDS=75 \
    AEGISFORGE_MAX_LLM_CALLS_PER_RESPONSE=1 \
    AEGISFORGE_OFFICEQA_RUNTIME_PROFILE=v0_6_1_sparse_corpus_llm_ready

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
#
# AgentBeats can append:
#   --host --port --card-url
ENTRYPOINT ["bash", "/app/run.sh"]
