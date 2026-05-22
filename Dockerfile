# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Minimal runtime deps.
# bash is installed because run.sh currently uses bash-specific syntax.
# ca-certificates is required for HTTPS downloads during corpus bootstrap.
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

# Install dependencies + local package.
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir .

# OfficeQA public source corpus.
#
# IMPORTANT:
# - This downloads only the public source-document corpus tree from Databricks OfficeQA.
# - It intentionally does NOT download officeqa.csv, because that file contains answer labels.
# - agent.py must still ignore any answer/gold/ground_truth/solution-like fields if future
#   files are added under this directory.
ARG OFFICEQA_CORPUS_COMMIT=6aa8c32ba38ef9baf7e88c9c592d16a024090953
ARG OFFICEQA_CORPUS_ARCHIVE_URL=https://github.com/databricks/officeqa/archive/${OFFICEQA_CORPUS_COMMIT}.zip

ENV AEGISFORGE_OFFICEQA_DATA_DIR=/app/data/officeqa \
    AEGISFORGE_OFFICEQA_CORPUS_DIR=/app/data/officeqa/treasury_bulletins_parsed \
    AEGISFORGE_OFFICEQA_ENABLE_RAG=true \
    AEGISFORGE_OFFICEQA_CORPUS_URL=https://github.com/databricks/officeqa/tree/${OFFICEQA_CORPUS_COMMIT}/treasury_bulletins_parsed \
    AEGISFORGE_OFFICEQA_CORPUS_COMMIT=${OFFICEQA_CORPUS_COMMIT}

RUN mkdir -p "${AEGISFORGE_OFFICEQA_DATA_DIR}" /tmp/officeqa_download \
 && OFFICEQA_CORPUS_COMMIT="${OFFICEQA_CORPUS_COMMIT}" \
    OFFICEQA_CORPUS_ARCHIVE_URL="${OFFICEQA_CORPUS_ARCHIVE_URL}" \
    AEGISFORGE_OFFICEQA_DATA_DIR="${AEGISFORGE_OFFICEQA_DATA_DIR}" \
    python - <<'PY'
import json
import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

commit = os.environ["OFFICEQA_CORPUS_COMMIT"]
archive_url = os.environ["OFFICEQA_CORPUS_ARCHIVE_URL"]
data_dir = Path(os.environ["AEGISFORGE_OFFICEQA_DATA_DIR"]).resolve()
target = data_dir / "treasury_bulletins_parsed"
tmp_zip = Path("/tmp/officeqa_download/officeqa.zip")

forbidden_name_fragments = (
    "answer",
    "answers",
    "answer_key",
    "gold",
    "ground_truth",
    "correct_answer",
    "expected_answer",
    "solution",
    "solutions",
    "results",
    "provenance",
    "leaderboard",
    "submission",
    "secret",
    "token",
    "credential",
    "apikey",
    "api_key",
)

allowed_prefixes = (
    "treasury_bulletins_parsed/jsons/",
    "treasury_bulletins_parsed/transformed/",
)

print(f"[Dockerfile] Downloading OfficeQA public source corpus from {archive_url}", flush=True)
with urllib.request.urlopen(archive_url, timeout=180) as response:
    tmp_zip.write_bytes(response.read())

if target.exists():
    shutil.rmtree(target)
target.mkdir(parents=True, exist_ok=True)

extracted_files = 0
with zipfile.ZipFile(tmp_zip) as zf:
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if info.is_dir():
            continue

        marker = "treasury_bulletins_parsed/"
        idx = name.find(marker)
        if idx < 0:
            continue

        rel = name[idx:]
        rel_lower = rel.lower()
        basename_lower = Path(rel_lower).name

        if not rel_lower.startswith(allowed_prefixes):
            continue
        if any(fragment in basename_lower for fragment in forbidden_name_fragments):
            continue

        out_path = data_dir / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, out_path.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        extracted_files += 1

if extracted_files <= 0:
    print("[Dockerfile] ERROR: no OfficeQA source corpus files were extracted.", file=sys.stderr)
    sys.exit(2)

metadata = {
    "source": "databricks/officeqa",
    "commit": commit,
    "archive_url": archive_url,
    "included_tree": "treasury_bulletins_parsed/{jsons,transformed}",
    "excluded": [
        "officeqa.csv",
        "answer/gold/ground_truth/solution/results/provenance-like filenames",
        "transform_scripts",
    ],
    "file_count": extracted_files,
}
(data_dir / "OFFICEQA_CORPUS_METADATA.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
print(f"[Dockerfile] OfficeQA corpus installed at {target} with {extracted_files} files.", flush=True)
PY
RUN rm -rf /tmp/officeqa_download \
 && find "${AEGISFORGE_OFFICEQA_DATA_DIR}" -type f \( \
      -iname '*answer*' -o \
      -iname '*gold*' -o \
      -iname '*ground_truth*' -o \
      -iname '*correct_answer*' -o \
      -iname '*expected_answer*' -o \
      -iname '*solution*' -o \
      -iname '*results*' -o \
      -iname '*provenance*' -o \
      -iname '*leaderboard*' -o \
      -iname '*secret*' -o \
      -iname '*token*' -o \
      -iname '*credential*' -o \
      -iname '*api_key*' \
    \) -delete \
 && python - <<'PY'
from pathlib import Path
root = Path("/app/data/officeqa")
files = [p for p in root.rglob("*") if p.is_file()]
print(f"[Dockerfile] OfficeQA source corpus file count after safety filter: {len(files)}", flush=True)
if not files:
    raise SystemExit("OfficeQA source corpus is empty after safety filter.")
PY

# OpenAI-compatible endpoint defaults.
#
# The leaderboard scenario already passes OPENAI_API_KEY to the participant when
# available. We do not set or modify that secret here. This default only gives
# agent.py a standard OpenAI-compatible base URL to use when the key exists.
ENV OPENAI_BASE_URL=https://api.openai.com/v1 \
    AEGISFORGE_LLM_TIMEOUT_SECONDS=75 \
    AEGISFORGE_MAX_LLM_CALLS_PER_RESPONSE=1 \
    AEGISFORGE_OFFICEQA_RUNTIME_PROFILE=v0_6_0_corpus_llm_ready

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
