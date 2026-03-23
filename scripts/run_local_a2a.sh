#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

IMAGE_NAME="${IMAGE_NAME:-aegisforge:local}"
CONTAINER_NAME="${CONTAINER_NAME:-aegisforge-local}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
AGENT_PORT="${AGENT_PORT:-$PORT}"
HOST_BIND="${HOST_BIND:-127.0.0.1}"
SKIP_BUILD="${SKIP_BUILD:-0}"

PUBLIC_URL="${AEGISFORGE_PUBLIC_URL:-http://${HOST_BIND}:${PORT}}"

print() {
  echo "[run_local_a2a] $*"
}

print "Repo root: ${REPO_ROOT}"
print "Image: ${IMAGE_NAME}"
print "Container: ${CONTAINER_NAME}"
print "Port: ${PORT}"
print "Public URL: ${PUBLIC_URL}"

if [[ "${SKIP_BUILD}" != "1" ]]; then
  print "Building Docker image..."
  docker build -t "${IMAGE_NAME}" "${REPO_ROOT}"
else
  print "Skipping Docker build because SKIP_BUILD=1"
fi

docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

print "Starting container in foreground..."
print "Health: ${PUBLIC_URL}/health"
print "Agent Card: ${PUBLIC_URL}/.well-known/agent-card.json"

exec docker run --rm -it \
  --name "${CONTAINER_NAME}" \
  -p "${PORT}:${PORT}" \
  -e HOST="${HOST}" \
  -e PORT="${PORT}" \
  -e AGENT_PORT="${AGENT_PORT}" \
  -e AEGISFORGE_PUBLIC_URL="${PUBLIC_URL}" \
  "${IMAGE_NAME}"
  