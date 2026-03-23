#!/usr/bin/env bash
set -euo pipefail

HOST="0.0.0.0"
PORT="8001"
CARD_URL=""

usage() {
  echo "Usage: ./run.sh [--host <host>] [--port <port>] [--card-url <url>]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="${2:-}"
      shift 2
      ;;
    --port)
      PORT="${2:-}"
      shift 2
      ;;
    --card-url)
      CARD_URL="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1"
      usage
      exit 1
      ;;
  esac
done

export AEGISFORGE_HOST="$HOST"
export AEGISFORGE_PORT="$PORT"
export AEGISFORGE_CARD_URL="$CARD_URL"

echo "[run.sh] Starting AegisForge A2A server..."
echo "[run.sh] HOST=$HOST PORT=$PORT CARD_URL=${CARD_URL:-<auto>}"

ARGS=(
  -m aegisforge.a2a_server
  --host "$HOST"
  --port "$PORT"
)

if [[ -n "$CARD_URL" ]]; then
  ARGS+=(--card-url "$CARD_URL")
fi

exec python "${ARGS[@]}"
