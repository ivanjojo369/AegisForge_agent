# Live Forge — Technical Evaluation Guide

**Project:** Live Forge — Universal Stream Mods  
**Project family:** AegisForge Nexus / Colossus  
**Associated event:** Codex x TwitchCon Hackathon 2026  
**Repository:** https://github.com/ivanjojo369/AegisForge_agent  
**Source:** `src/aegisforge/products/live_forge/`  
**Web frontend:** `src/aegisforge/web/live_forge/`  
**Status:** Working local prototype; authenticated Twitch EventSub connection observed on October 31, 2026. Real chat-message delivery to the application has **not** yet been demonstrated. Neither contest eligibility nor an official submission is established by this document.

## What Live Forge does

Live Forge is a local operator-controlled interactive experience framework for Twitch stream communities. The operator selects an activity, permits curated mechanics, opens and closes voting rounds, and explicitly approves effects. The application can display an overlay and change *virtual* activity state. It cannot execute viewers' shell commands, modify external games or OBS, perform purchases, or control physical systems.

Seven supported categories: **art**, **gaming**, **coding**, **science**, **music**, **sports**, and **talk**. Art uses the Creative Studio; the remaining six use Universal Activity Bridge. A fixed catalog and composition rules determine which effects each category supports.

Core architecture:

```text
Twitch EventSub (when authorized) -> validated chat messages -> A/B vote engine
     -> frozen round result -> operator approval -> virtual activity / overlay
     -> optional Colossus Laboratory, ProofGraph and Mission Control evidence
```

The standalone operator controls, previews and activity actions work without a Twitch broadcast. **Manual A/B actions and synthetic fixtures are not real chat votes.**

## Observed results and limits

- Local `python -m aegisforge.products.live_forge.validation` output showed `status: passed`, **15 check groups**, `synthetic_only: true`, `real_twitch_connection_tested: false` and `network_access: false`.
- The earlier Codex CLI development log reports nine selected Nexus pytest tests passing and prior unsuccessful test attempts. This is **not** a claim that the entire repository test suite passed.
- An OAuth device-code request was accepted, and the operator console later displayed **Twitch conectado / EventSub conectado**, with **zero notifications and zero votes**. This demonstrates an authenticated connection status as reported by the application; it does not establish that real `!vote A` or `!vote B` messages were received.
- Codex CLI was used to develop and test changes to voting, deduplication, reconnection, round approval, effect dispatch and evidence handling. The session ID must be recovered from the actual local Codex session record before it can be quoted as evidence.
- Live Forge's shared Colossus infrastructure predates portions of this work. Check commit history when separating new work from existing framework components.

## Prerequisites

- Windows PowerShell and Python **3.11 or newer**.
- The repository cloned locally. Its development installation requires Internet access to retrieve dependencies on a fresh machine; **no Twitch or Codex paid credits are required** for the offline demo.
- No Twitch account, streaming software, audience, OAuth tokens or broadcast are needed for the **offline** demonstration.

## Installation and offline launch

From a fresh clone, open PowerShell in the repository root and run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . "starlette>=0.40" "websockets>=14"
$env:LIVE_FORGE_OFFLINE = '1'
.\.venv\Scripts\python.exe -m aegisforge.products.live_forge.server
```

The server prints a **private local operator token**. Enter it only into the local operator console, not into recordings or public materials. Leave the server terminal open.

Local URLs:

| Surface | URL |
|---|---|
| Operator console | http://127.0.0.1:8013/ |
| Overlay | http://127.0.0.1:8013/overlay |
| Art Creative Studio | http://127.0.0.1:8013/studio |
| Other six activity bridges | http://127.0.0.1:8013/bridge |
| Read-only runtime status | http://127.0.0.1:8013/api/state |

If port 8013 is busy, stop the *previous Live Forge server process* before launching another; do not terminate unrelated processes. Offline mode must be set in the **same PowerShell process** used to launch the server.

## Reproduce the 15 synthetic validation groups

Run the following command from the repository root in a separate terminal with the environment installed:

```powershell
.\.venv\Scripts\python.exe -m aegisforge.products.live_forge.validation
```

Expected markers include `"status": "passed"`, `"check_groups": 15`, `"synthetic_only": true` and `"real_twitch_connection_tested": false`. The fixture blocks network connections and uses disposable local checkpoints. This is reproducibility evidence, **not a substitute for a real Twitch chat event**.

## Operator-controlled demonstration: no livestream required

1. Open the local console and enter the private operator token off-camera. The console will enable operator controls.
2. Choose **Art** and two allowed mechanics (for example, Community Quest / Progress Goal, if offered). Enable rounds and click **Guardar política**.
3. Click **Vista previa de A (sin votos)** and show the overlay. The preview is labeled local: it neither counts votes nor claims Twitch activity.
4. Once the policy and cooldown allow it, use **Aplicar A al estudio (sin votos)** and open `/studio`. Show how a permitted virtual scene changes and how the evidence summary updates.
5. Select **Science** or **Coding**, save a compatible policy, and use the appropriate **Activity Bridge** manual control. Open `/bridge` to show its independent virtual state. The operator may need to respect the cooldown.
6. Show **PARAR TODO** and the aggregated evidence panel. Never show credentials, the operator token, private chat details or personal account information.
7. From a second terminal, run the standalone validation command above. Explain that the synthetic end-to-end vote/approval test is separate from authentic Twitch EventSub operation.

A real-vote demonstration is **not** claimed until authorized chat events arrive, increment the actual vote counters and result in a separately approved effect.

## If genuine Twitch events are available

The Twitch integration uses `channel.chat.message` via EventSub WebSockets with an authorized user's `user:read:chat` scope. Only an eligible authorized operator may manage application registration and OAuth under Twitch's developer terms. The application does **not** require the owner to broadcast video just to load the local console.

A connected indicator with zero messages is evidence of connection, **not** proof of a completed real-vote workflow. A genuine end-to-end observation would need a legitimate channel chat message, accepted vote count, a closed round, human approval, a virtual effect and archived evidence. No fake HTTP vote endpoint is provided.

## Video storyboard (approximately 2 minutes 30 seconds)

The technical walkthrough can be recorded locally without a Twitch livestream. Such a recording demonstrates the standalone prototype; **do not claim that it satisfies any contest requirement for actual Twitch footage** unless real Twitch behavior is separately shown and verified.

| Approximate timestamp | Footage | Spoken or captioned claim |
|---|---|---|
| 0:00–0:20 | Main console and seven activity categories | Live Forge is a supervised virtual interactive experience platform. |
| 0:20–0:50 | Operator policy, two mechanics and preview | Streamers choose allowed mechanics; this preview is local, not a real vote. |
| 0:50–1:15 | Creative Studio and overlay | Authorized local actions update a virtual scene. |
| 1:15–1:40 | Activity Bridge, Science or Coding | Each activity has its own virtual contract and state. |
| 1:40–2:00 | Live Forge evidence summary and STOP ALL | Colossus records locally verifiable evidence; emergency stop remains available. |
| 2:00–2:20 | Terminal with 15/15 synthetic validations | Reproducible, isolated tests; not a live-chat benchmark. |
| 2:20–2:30 | GitHub source and Codex CLI activity record | The engineering work used Codex; note the session identifier only after recovery. |

Keep screen recordings free of copyrighted music, other people's identifiable chat messages, passwords, tokens, OAuth codes, and application secrets. Use captions if narration is not available.

## Codex session record

The main Codex build thread ran locally, modified the Live Forge source and ended at its usage limit after testing. **Session ID: NOT YET RECOVERED.** The ID should be extracted from the existing Codex session metadata or observed using Codex `/status` without submitting a new AI task. Do not fabricate an ID or paste full session logs that could contain secrets.

## Release checklist

- [x] Core product source and web UI publicly available on `main`.
- [x] Local isolated validation reported 15/15 groups passed.
- [x] OAuth acceptance and connected EventSub status observed in an operator screenshot.
- [ ] Fresh-install test on a clean environment independently repeated.
- [ ] Real Twitch chat message and vote observed; currently **not** demonstrated.
- [ ] Codex session ID read from local history.
- [ ] Short safe technical walkthrough recorded and reviewed.
- [ ] Final published documentation and release commit verified.
- [ ] Official competition eligibility independently confirmed; **not established by this document**.

## Source links

- Source repository: https://github.com/ivanjojo369/AegisForge_agent
- Twitch developer documentation: https://dev.twitch.tv/docs/eventsub/handling-websocket-events/
- Contest information and eligibility: https://twitchcon-codex.devpost.com/rules
