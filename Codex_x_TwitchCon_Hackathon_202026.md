# Codex x TwitchCon Hackathon 2026

## Project: Live Forge

**Theme:** Interactive experiences for Twitch streamers and communities  
**Event:** October 23–November 2, 2026  
**Deadline listed by Devpost:** November 2, 2026, 2:00 PM Pacific Standard Time  
**Official event:** https://twitchcon-codex.devpost.com/  
**Official rules:** https://twitchcon-codex.devpost.com/rules

> **Submission working draft — not evidence of submission or eligibility.** An authorized, genuinely connected Twitch demonstration, a Codex session ID, and a qualifying entry are mandatory for the contest. No synthetic vote may be represented as a real Twitch event.

## Project overview

**Live Forge** is a multi-category framework for community-driven interactive stream mechanics. A streamer chooses permitted mechanics and when to apply them; an authorized Twitch audience can vote on those options, with effects constrained by the streamer's policies. The product reuses engineering and evidence facilities from **AegisForge Nexus / Colossus** while retaining its own application and runtime.

The intended user experience is **streamer-controlled modding of a live activity**, not permission for arbitrary chat commands to modify external applications.

## Seven activity categories

1. **Gaming:** Virtual goals and progress.
2. **Art:** Creative Studio with visual symbols, themes and objectives.
3. **Coding:** Isolated conceptual task and review board.
4. **Science:** Hypothetical comparisons and milestone tracking.
5. **Music:** Authorized visual ideas and progress; no unlicensed music playback.
6. **Sports:** Virtual analysis and goals.
7. **Talk:** Topics, decisions and conversational progression.

Art uses its own **Creative Studio**; the other six categories use **Universal Activity Bridge**, with separate activity state and a shared mechanic catalog.

## Implemented local features observed during development

- Mechanic catalog, composition of preapproved mechanics, and compatibility contracts by category.
- Streamer-only policy configuration, timed rounds, an emergency stop control, and A/B operator actions.
- Browser overlays with visual feedback; local Creative Studio with multiple symbols, objectives and palette changes.
- Activity Bridge dashboards with counters, conceptual details and recent action histories.
- Local evidence flows into Colossus **Laboratory**, **ProofGraph**, and **Mission Control**.
- Twitch **EventSub** receiver and voting logic developed but **not yet confirmed against an authenticated Twitch session**.
- Codex Engineering Bridge for supervised, read-only analysis. Separately, **Codex CLI was used to implement integration changes** in the local repository.

## What the Codex session achieved (local execution log)

The available Codex CLI log reports work on the flow:

**EventSub input → A/B votes → round closure → pending winning choice → streamer approval → virtual activity/overlay → Colossus evidence.**

It records changes to voting/ingestion, effect dispatch, evidence handling, server routing and web controls. It also reports an offline mode and a local validation module. A synthetic round was reported to apply the winning mechanic to Creative Studio and archive an evidence result.

Reported checks:

- **15 local validation groups** reported as passed, including category routing and simulated reconnection.
- A final **9/9 selected pytest checks** passed.
- Earlier pytest attempts had errors; the passing selection does **not** establish that every project test passes.
- Codex quota expired before a final complete summary was produced.

**Scope of the evidence:** local and synthetic. It does **not** establish an authentic Twitch connection or attest that every change was published.

## Local reproduction — operator-controlled demonstration

The following launcher was used during earlier Live Forge development on Windows:

```powershell
Set-Location -LiteralPath 'C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent'
$env:LIVE_FORGE_CHECKPOINT_DIR = Join-Path $env:LOCALAPPDATA 'AegisForge\LiveForgeMissions'
python -m aegisforge.products.live_forge.server
```

Then open:

- Streamer console: http://127.0.0.1:8013/
- Overlay: http://127.0.0.1:8013/overlay
- Creative Studio: http://127.0.0.1:8013/studio
- Universal Activity Bridge: http://127.0.0.1:8013/bridge

**Before release:** verify dependencies, CLI behavior and routes on a fresh checkout, and replace machine-specific paths with portable instructions. The operator token must remain private and never be pasted into an overlay or committed.

### Demonstration sequence (local capabilities)

1. Open the console and authenticate as the local operator.
2. Select a category and save an allowed A/B policy.
3. Demonstrate local A/B actions, consent and effects in the Creative Studio or Activity Bridge.
4. Inspect the evidence output in Colossus.
5. Separately demonstrate **real Twitch participation** only after an authorized EventSub connection has actually been established.

Local operator actions and synthetic fixtures must be labeled as such. They do not fulfill the competition's Twitch-connected demo requirement.

## Security and limitations

- The viewer cannot issue arbitrary shell commands or freely modify code through chat.
- External OBS, games, music software and hardware are **not** modified by the local virtual adapters.
- Approval of a mechanic is distinct from its initial proposal or the closing of a round.
- Twitch authentication, real event delivery, and behavior after actual reconnection still require independent authorized verification.
- Historical hashes prove consistency within the system's verification assumptions, not external authenticity.
- Shared Colossus code may predate the contest: the eligibility of **new Live Forge work during the official submission window** must be verified against repository history and the rules.

## Official submission checklist

- [ ] Confirm that the participant/team satisfies **the minimum age and other eligibility rules**. The rules require individuals to be at least 18 or the legal age of majority in their place of residence, whichever is older.
- [ ] Confirm Live Forge's contest-eligible creation timeline and separate it clearly from previously developed Colossus infrastructure.
- [ ] Ensure a repository contains source code, assets and reproducible installation instructions; do not disclose secrets.
- [ ] Confirm **an actual authorized Twitch integration** that behaves as described.
- [ ] Record a **video shorter than 3 minutes** showing the product **working on Twitch** and how it was built with Codex; upload to YouTube as publicly accessible or unlisted, not private.
- [ ] Record the **Codex session ID** from the main build thread (for example using `/status` when access is available).
- [ ] Include an English description of capabilities, the technical approach and Codex's specific contribution.
- [ ] Submit the repository URL, video URL, project description and session ID through Devpost before the deadline; confirm status **Submitted**, not just saved as a draft.

**Repository intended for publication:** https://github.com/ivanjojo369/AegisForge_agent  
**Live Forge submission branch/release:** TO BE CONFIRMED  
**Codex session ID:** TO BE FILLED  
**Authenticated Twitch demo:** NOT VERIFIED  
**Demo video:** TO BE ADDED  
**Submission status:** NOT VERIFIED

## Sources

- Official event: https://twitchcon-codex.devpost.com/
- Official rules: https://twitchcon-codex.devpost.com/rules
- Official submission FAQ: https://twitchcon-codex.devpost.com/resources
