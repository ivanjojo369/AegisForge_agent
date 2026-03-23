# Demo: τ²-style capability (AegisForge)

This demo shows that **AegisForge** routes requests into a **τ²-style adapter** (a thin layer meant to connect to your τ² tooling/domains).

## 1) Start the A2A server

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
uv sync --extra test
uv run python -m aegisforge.a2a_server --host 127.0.0.1 --port 9009 --card-url http://127.0.0.1:9009/
```

## 2) Verify the Agent Card

```powershell
curl http://127.0.0.1:9009/.well-known/agent-card.json
```

## 3) Send a τ²-style request (example)

Example message content:

```
tau2: please run a tiny multi-turn task in quipu_lab and summarize the outcome
```

## Expected output

- A response containing a section like:

- `[τ² adapter]`

## Next improvement (when you're ready)

- Wire a **small public demo task** that exercises your `quipu_lab` domain (tools + multi-turn)
- Keep the demo deterministic and lightweight (no private/held-out tasks)
