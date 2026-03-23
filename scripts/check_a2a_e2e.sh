#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

IMAGE_NAME="${IMAGE_NAME:-aegisforge:local}"
CONTAINER_NAME="${CONTAINER_NAME:-aegisforge-a2a-e2e}"
HOST_BIND="${HOST_BIND:-127.0.0.1}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
AGENT_PORT="${AGENT_PORT:-$PORT}"
STARTUP_RETRIES="${STARTUP_RETRIES:-30}"
STARTUP_SLEEP_SECONDS="${STARTUP_SLEEP_SECONDS:-2}"
SKIP_BUILD="${SKIP_BUILD:-0}"
KEEP_CONTAINER="${KEEP_CONTAINER:-0}"
CURL_BIN="${CURL_BIN:-curl}"

PUBLIC_URL="${AEGISFORGE_PUBLIC_URL:-http://${HOST_BIND}:${PORT}}"
HEALTH_URL="${HEALTH_URL:-${PUBLIC_URL}/health}"
CARD_URL="${CARD_URL:-${PUBLIC_URL}/.well-known/agent-card.json}"

print() {
  echo "[check_a2a_e2e] $*"
}

fail() {
  print "ERROR: $*"
  if docker ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
    print "Container logs:"
    docker logs "${CONTAINER_NAME}" || true
  fi
  exit 1
}

cleanup() {
  if [[ "${KEEP_CONTAINER}" != "1" ]]; then
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

wait_for_url() {
  local url="$1"
  local label="$2"

  for ((i=1; i<=STARTUP_RETRIES; i++)); do
    if "${CURL_BIN}" -fsS "${url}" >/dev/null 2>&1; then
      print "${label} is reachable: ${url}"
      return 0
    fi
    print "Waiting for ${label} (${i}/${STARTUP_RETRIES})..."
    sleep "${STARTUP_SLEEP_SECONDS}"
  done

  return 1
}

ensure_running() {
  local running
  running="$(docker inspect -f '{{.State.Running}}' "${CONTAINER_NAME}" 2>/dev/null || echo "false")"
  [[ "${running}" == "true" ]]
}

validate_agent_card() {
  local tmp_file
  tmp_file="$(mktemp)"
  "${CURL_BIN}" -fsS "${CARD_URL}" -o "${tmp_file}"

  python - "${tmp_file}" "${PUBLIC_URL}" "${HEALTH_URL}" <<'PY'
import json
import sys
from pathlib import Path

card_path = Path(sys.argv[1])
public_url = sys.argv[2].rstrip("/")
health_url = sys.argv[3]

data = json.loads(card_path.read_text(encoding="utf-8"))

if not isinstance(data, dict):
    raise SystemExit("Agent Card must be a JSON object.")

expected_any = {"id", "name", "version", "url", "capabilities", "description"}
if not any(key in data for key in expected_any):
    raise SystemExit(
        f"Agent Card is valid JSON but missing expected top-level keys. Found: {sorted(data.keys())}"
    )

advertised_url = data.get("url")
if isinstance(advertised_url, str) and advertised_url.rstrip("/") != public_url:
    print(
        f"Warning: Agent Card 'url' does not match PUBLIC_URL. "
        f"card={advertised_url!r} expected={public_url!r}",
        file=sys.stderr,
    )

advertised_health = data.get("health_url")
if isinstance(advertised_health, str) and advertised_health != health_url:
    print(
        f"Warning: Agent Card 'health_url' does not match HEALTH_URL. "
        f"card={advertised_health!r} expected={health_url!r}",
        file=sys.stderr,
    )

print("Agent Card JSON looks valid.")
PY

  rm -f "${tmp_file}"
}

print "Repo root: ${REPO_ROOT}"
print "Image: ${IMAGE_NAME}"
print "Container: ${CONTAINER_NAME}"
print "Public URL: ${PUBLIC_URL}"

if [[ "${SKIP_BUILD}" != "1" ]]; then
  print "Building Docker image..."
  docker build -t "${IMAGE_NAME}" "${REPO_ROOT}"
else
  print "Skipping Docker build because SKIP_BUILD=1"
fi

docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

print "Starting container..."
docker run -d \
  --name "${CONTAINER_NAME}" \
  -p "${PORT}:${PORT}" \
  -e HOST="${HOST}" \
  -e PORT="${PORT}" \
  -e AGENT_PORT="${AGENT_PORT}" \
  -e AEGISFORGE_PUBLIC_URL="${PUBLIC_URL}" \
  "${IMAGE_NAME}" >/dev/null

sleep 3

if ! ensure_running; then
  fail "Container did not stay up after startup."
fi

wait_for_url "${HEALTH_URL}" "/health" || fail "Health endpoint did not become reachable in time."
wait_for_url "${CARD_URL}" "Agent Card" || fail "Agent Card endpoint did not become reachable in time."

if ! ensure_running; then
  fail "Container crashed before checks completed."
fi

validate_agent_card || fail "Agent Card validation failed."

print "E2E OK: container stays up + agent card reachable."
