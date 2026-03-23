# Demo: OpenEnv-style capability (AegisForge)

This demo shows that **AegisForge** exposes an A2A server and can route a request into the **OpenEnv adapter**.

## 1) Start the A2A server

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
uv sync --extra test
uv run python -m aegisforge.a2a_server --host 127.0.0.1 --port 9009 --card-url http://127.0.0.1:9009/
```

## 2) Verify the Agent Card is available

```powershell
curl http://127.0.0.1:9009/.well-known/agent-card.json
```

You should receive JSON describing the agent.

## 3) Send an OpenEnv request (example)

Use any A2A client/harness you already have. Example message content:

```
openenv: connect to the environment server and run one short episode
```

## Expected output

- A response containing a section like:

- `[OpenEnv adapter]`
- `Configured env server: ...` (from `OPENENV_BASE_URL` if set)

## Next improvement (when you're ready)

- Pick **one** OpenEnv environment to support first (e.g., `websearch_env`)
- Implement a single HTTP call to the env server
- Return a short “episode trace” (steps + final outcome) in the response
