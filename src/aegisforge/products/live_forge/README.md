# Live Forge — technical quickstart and scope

**Product:** Live Forge (AegisForge Colossus)  
**Hackathon reviewer guide:** [Codex x TwitchCon Hackathon 2026](../../../../Codex%20x%20TwitchCon%20Hackathon%202026.md)  
**Implementation status:** Local virtual interaction and synthetic end-to-end checks are implemented. **No authenticated Twitch demo has been verified.**

## Local setup

From a fresh PowerShell checkout of `AegisForge_agent` with Python 3.11 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e . "starlette>=0.40" "websockets>=14"
$env:LIVE_FORGE_OFFLINE = '1'
.\.venv\Scripts\python.exe -m aegisforge.products.live_forge.server
```

The local server prints a private operator token. Never publish it. Open http://127.0.0.1:8013/ (console), `/overlay` (public read-only overlay), `/studio` (art Creative Studio) and `/bridge` (six other virtual categories).

## Basic walkthrough

1. Configure an allowlisted streamer policy, selecting two supported mechanic options and a category.
2. Use the operator preview to see a labeled local visual effect without pretending to receive real votes.
3. Browse the Creative Studio and Universal Activity Bridge, including their separate activity states.
4. Use **PARAR TODO / STOP ALL** and inspect local evidence when enabled.
5. To validate the actual vote-to-approval path without Twitch, stop the server or open a second terminal, then run:

   ```powershell
   .\.venv\Scripts\python.exe -m aegisforge.products.live_forge.validation
   ```

   This check uses synthetic fixtures and temporary local state, not a live stream.

## Optional dedicated evidence directory

Set these before launching the server if you need persistent local Colossus evidence:

```powershell
$env:LIVE_FORGE_CHECKPOINT_DIR = Join-Path $env:LOCALAPPDATA 'AegisForge\LiveForgeJudgeDemo'
New-Item -ItemType Directory -Path $env:LIVE_FORGE_CHECKPOINT_DIR -Force | Out-Null
$env:LIVE_FORGE_OFFLINE = '1'
.\.venv\Scripts\python.exe -m aegisforge.products.live_forge.server
```

Do not reuse another product's Mission Control checkpoint store. The scene and active voting session are in-memory, even when evidence is archived.

## What exists

- Seven categories: gaming, art, coding, science, music, sports, talk.
- Fixed, safe mechanic catalog and approved compositions.
- Local operator-only policy changes, round lifecycle, effects, STOP ALL, and inspection UI.
- EventSub parser, vote deduplication and transport reconnection logic; unauthenticated operation remains labeled offline.
- Separate *closure* and *approval* of an A/B winner before a virtual scene/overlay changes.
- Colossus Laboratory / ProofGraph / Mission Control evidence bridge, with optional persistent checkpoints.
- Read-only Codex Engineering Bridge plus a **separate actual Codex CLI development session** which implemented additional integration work.

## Honest verification status

The recorded Codex session reported 15 isolated validation groups passing and a final 9/9 selected pytest tests passing, with earlier errors in other attempts. This does **not** establish a clean run of the entire repository test suite. A real authorized Twitch connection has **not been independently verified**. No Twitch viewer event must be represented as real without proof.

## Architecture and boundaries

Core files: `server.py`, `engine.py`, `eventsub_ingestion.py`, `twitch_eventsub.py`, `policy_engine.py`, `effect_dispatcher.py`, `creative_studio.py`, `activity_bridge.py`, `evidence_bridge.py`, `codex_bridge.py`, `validation.py`. Web UI: `src/aegisforge/web/live_forge/`.

The system operates only virtual local experiences. It does not allow chat to execute arbitrary software, operate actual Twitch accounts/OBS or modify a game, device, or physical installation. All credentials and the operator token must remain private. Official Twitch and hackathon eligibility and integration conditions apply.

For the contest's video, Codex session ID, status of genuine Twitch participation, and full submission checklist, consult the [dedicated reviewer guide](../../../../Codex%20x%20TwitchCon%20Hackathon%202026.md).
