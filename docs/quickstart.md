# Quickstart

This guide gets AegisForge running locally in under a minute.

## Prerequisites

- Docker installed
- A free local port `8000`
- A clone of this repository

## Build the image

```bash
docker build -t aegisforge:local .
```

## Run the container

```bash
docker run --rm -p 8000:8000 \
  -e HOST=0.0.0.0 \
  -e PORT=8000 \
  -e AGENT_PORT=8000 \
  -e AEGISFORGE_PUBLIC_URL=http://127.0.0.1:8000 \
  aegisforge:local
```

## Check health

```bash
curl http://127.0.0.1:8000/health
```

Expected shape:

```json
{
  "status": "ok",
  "service": "aegisforge",
  "version": "0.1.0",
  "public_url": "http://127.0.0.1:8000"
}
```

## Check Agent Card

```bash
curl http://127.0.0.1:8000/.well-known/agent-card.json
```

The endpoint should return valid JSON and advertise the public URL of the service.

## Local helper scripts

### PowerShell

```powershell
.\scripts\run_local_a2a.ps1
.\scripts\check_a2a_e2e.ps1
```

### Bash

```bash
bash scripts/run_local_a2a.sh
bash scripts/check_a2a_e2e.sh
```

## Common development loop

1. Build image
2. Run container
3. Probe `/health`
4. Probe `/.well-known/agent-card.json`
5. Run smoke checks

## Public endpoint

When deploying publicly, set:

```bash
AEGISFORGE_PUBLIC_URL=https://your-public-endpoint.example.com
```

That same public URL should be reflected in the Agent Card.
