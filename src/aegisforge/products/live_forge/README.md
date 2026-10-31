# Live Forge — Twitch EventSub live vote MVP

An **independent app** within AegisForge Colossus. This is a first technical foundation, not a full hackathon project or a Codex-built submission.

## Behavior

- Connects to Twitch **EventSub WebSockets** using an authorized **user access token** with `user:read:chat` scope.
- Subscribes to `channel.chat.message` for one channel via Twitch Helix (API v1).
- Accepts chat messages `!vote A` or `!vote B`; uses one current vote per chatter, deduplicates recent message IDs, and displays only aggregate counts.
- Standalone localhost UI, with a read-only `GET /api/state` endpoint. **No vote-injection endpoint.** No code sends messages to Twitch, operates OBS, triggers external effects, or writes personal IDs to disk.
- Without a Twitch credential, the page explicitly reports **not configured** and cannot claim to be live.

## Prerequisites

You must have a Twitch application and a valid authorized **User Access Token** with scope `user:read:chat`, from an official Twitch OAuth flow. Obtain consent for the applicable account(s). Do not share tokens in ChatGPT, commits, screenshots, or browser JS. The ID in `TWITCH_USER_ID` must be the same user as the token. The broadcaster channel ID belongs in `TWITCH_BROADCASTER_ID`.

Official docs:
- https://dev.twitch.tv/docs/eventsub/handling-websocket-events/
- https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/
- https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/

## PowerShell startup (local only)

```powershell
Set-Location -LiteralPath 'C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent'

# Install only if not already available in your Python environment:
python -m pip install "starlette>=0.40" "uvicorn>=0.30" "httpx>=0.27" "websockets>=14"

# To run offline, leave the four variables unset; votes will remain zero.
# For the real Twitch connection, set these *locally* in your PowerShell session:
# $env:TWITCH_CLIENT_ID = '<client id>'
# $env:TWITCH_USER_ACCESS_TOKEN = '<authorized user access token>'
# $env:TWITCH_BROADCASTER_ID = '<numeric channel id>'
# $env:TWITCH_USER_ID = '<numeric authorized user id>'

python -m aegisforge.products.live_forge.server
```

Open http://127.0.0.1:8013/ . Keep the web server local. This version **does not implement its own OAuth consent UI**: use an authorized token produced by the Twitch OAuth developer process. The token is validated against Twitch before connecting.

## Status and limitations

The code has been syntax-checked and locally exercised without Twitch credentials. **An authenticated live Twitch session has not been tested**; the external integration is only implemented pending a correctly authorized account and credentials. On reconnection there may be missed events, because this MVP reconnects with a new session. Votes are not persisted between server restarts. This is deliberately a limited pilot, not a production service.

No Codex session ID is included. This code was not generated using OpenAI Codex, so it **cannot be presented as evidence of Codex use**. If this product is ever used in an event, check current eligibility and developer-tool rules independently. Codex × TwitchCon 2026 expressly excludes under-18 entrants, including teams with ineligible members.

## Next engineering steps

Integrate the authenticated Twitch workflow with Colossus Trust (token and permitted subscriptions), Laboratory (replayed EventSub fixtures), ProofGraph (non-personal provenance), and Mission Control (read-only mission lifecycle). Only after a live session is verified should the UI label a stream connection successful.

## Universal Stream Mods — Streamer policy MVP

This incremental addition keeps the existing EventSub receiver and introduces a **strictly allowlisted, overlay-only** cross-category mechanic catalog, voting rounds, and a private streamer approval step. It is not a general game/OBS/desktop mod loader.

1. Start the server as above. PowerShell prints a **local operator token**; do not share it or add it to an overlay or video.
2. Open `http://127.0.0.1:8013/`, enter the token in the private operator field, select an activity and two mechanics, enable rounds and save the policy.
3. Optionally click **Vista previa de A (sin votos)** to view a clearly labeled local preview. It does **not** inject fake chat messages, is rate-limited, and consumes the configured cooldown.
4. Open the round. During an authorized Twitch connection, chat participants send `!vote A` or `!vote B`. Only valid, unique-per-user messages received while a round is open are counted.
5. The operator clicks **Cerrar y aplicar ganador**. A tie/no-vote round produces **no effect**. If an option won and its allowlist/cooldown policy permits it, Live Forge shows a time-limited overlay effect.
6. **PARAR TODO** immediately closes the round, removes the overlay effect, and disables the policy. All policies and effects exist only in process memory.

The streamer can use `http://127.0.0.1:8013/overlay` as a **local browser source** in compatible broadcast software on the same PC. The public overlay contains no credentials or controls. The operator panel must never be shown on stream when it contains credentials.

### Scope and assurance

- All packs are compiled-in, fixed strings. No audience-authored scripts, executable plug-ins, arbitrary commands, device controls, music playback, direct streaming actions, file changes, purchases, or unsafe dares.
- A community vote **does not automatically execute** anything. One approved overlay effect at most per finished round, 5–60 seconds, with a 10–300 second cooldown; no real-world effect adapters.
- Chat IDs remain in RAM for current-round deduplication and are not displayed. There are no voter-injection endpoints.
- New code is a preparatory MVP only. No authenticated Twitch connection or Codex session is asserted by this patch. If Codex is later connected, code generation and testing must stay in an isolated, approval-gated development workflow rather than in the chat/event execution path.

### API surface (local loopback only)

- `GET /api/mechanics`: static activity + pack catalog.
- `GET /api/state`: non-personal round, policy, Twitch connection and overlay status.
- `GET /overlay`: read-only browser-source overlay.
- `POST /api/control`: streamer operations requiring `Authorization: Bearer <local operator token>`, JSON content type, and same-origin for browser requests. Actions: `policy`, `start`, `finish`, `preview`, and `stop`. No API to inject votes or arbitrary effects.

**Notes:** Rounds are memory-only, not restored after restarts. EventSub subscriptions require genuine Twitch authorization. `preview` only checks overlay rendering and is explicitly labeled as such, not proof of a Twitch session. There is no integration with Nexus Laboratory, ProofGraph, Mission Control or Codex yet.

## Universal Mechanic Composer — Phase 2

This extension builds on Phase 1's existing engine, policy, overlay and authenticated
operator console. `mechanic_composer.py` contains a **fixed allowlist** of nineteen
unique two-mechanic combinations: RPG quest + palette, RPG quest + progress,
and palette + progress. Each composition renders the two built-in, activity-specific
messages into **one reversible overlay-only effect**. It does not alter the game,
creator software, stream scene, device, or Twitch account.

1. Enter the private operator token in `http://127.0.0.1:8013/`.
2. Select the target activity and choose two **different** base mechanics under
   **Compositor universal de mecánicas**.
3. Click **Componer sin activar**. This makes a read-only, authenticated preview;
   **no** effects, policy changes, votes or Twitch calls occur.
4. Click **Asignar mezcla a A** or **Asignar mezcla a B**. Choose the other vote
   option (which may also be a permitted combination). Enable rounds and click
   **Guardar política**; policy still requires two distinct option IDs.
5. Optionally use **Vista previa de A (sin votos)**. This explicitly activates
   a temporary **streamer preview**, obeying existing cooldown and duration.
6. When genuinely connected to Twitch, open a round. Votes received from Twitch
   through EventSub select A/B; the streamer still closes and approves the winning
   effect. A tie or no votes produces no effect. **PARAR TODO** remains immediate.

`GET /api/mechanics` now additionally returns `compositions` (public allowlist).
The existing `POST /api/control` supports authenticated `action: "compose"` with
`first`, `second`, `activity`, returning a preview marked `activates_effect: false`
and `approved_for_round: false`. This is **not** a vote injection or effect endpoint.
`GET /api/state` exposes only the public effect and round state. No keys or
operator token are sent to the overlay. The existing local server remains on
port 8013; there are no new ports, dependencies or test scripts.

Existing `quest_palette_mix` is retained as a standalone prebuilt preset but
cannot be nested into a new combination. No user-provided templates, free-form
chat prompts, code generation/execution, external application modifications,
real-world actions, or Codex/Twitch authenticated claims are introduced by this
phase. All state is process memory only.

## Expanded mechanic catalog (Phase 2 enhanced)

The catalog includes **12 fixed overlay-only presets** (4 existing + 8 additional):
quest, palette, progress, quest+palette preset, branching story, optional timed
challenge, hypothesis comparison, code-review prompt, rhythm visualization,
community roles, statistical spotlight and creative constraint. The composer
has **19 curated two-mechanic pairs** across the 7 streaming categories.
These are only prompts/visual elements, not real action adapters.

The drop-down for the second mechanic now filters to combinations supported
by the first, and the UI shows the catalog count. As before, only the operator
can approve a preview or policy; viewer messages never execute code or change
creator applications. Existing round, Twitch EventSub, cooldown and stop
behavior is unchanged.
