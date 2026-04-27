"""
A2A server entrypoint for AegisForge.

AgentBeats runs your Docker image by calling the ENTRYPOINT with:
  --host, --port, --card-url

This module must:
- Bind to the given host/port (container-safe defaults: 0.0.0.0:8000)
- Serve an Agent Card at /.well-known/agent-card.json
"""

from __future__ import annotations

import argparse
import os

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from .executor import Executor


RUNTIME_VERSION = "1.1.0"

# One selected opponent per AgentX-AgentBeats category.
# "mcu" covers the MCU/Minecraft benchmark; we do not treat mcu-minecraft as a separate track.
SELECTED_OPPONENT_TRACKS = (
    "mcu",
    "officeqa",
    "crmarena",
    "fieldworkarena",
    "maizebargain",
    "tau2",
    "osworld",
    "pibench",
    "cybergym",
    "netarena",
)

SELECTED_OPPONENT_REPOS = {
    "mcu": "https://github.com/KWSMooBang/MCU-AgentBeats",
    "officeqa": "https://github.com/arnavsinghvi11/officeqa_agentbeats",
    "crmarena": "https://github.com/rkstu/entropic-crmarenapro",
    "fieldworkarena": "https://github.com/ast-fri/FieldWorkArena-GreenAgent",
    "maizebargain": "https://github.com/gsmithline/tutorial-agent-beats-comp",
    "tau2": "https://github.com/RDI-Foundation/tau2-agentbeats",
    "osworld": "https://github.com/RDI-Foundation/osworld-green",
    "pibench": "Pi-Bench",
    "cybergym": "CyberGym",
    "netarena": "NetArena",
}

AGENT_CARD_TAGS = (
    "agentx-agentbeats",
    "phase2-purple",
    "unified-purple-agent",
    "selected-opponent-profiles",
    "a2a",
    "mcu",
    "minecraft-benchmark",
    "officeqa",
    "crmarena",
    "fieldworkarena",
    "maizebargain",
    "tau2",
    "osworld",
    "pibench",
    "cybergym",
    "netarena",
)


def _normalize_base_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return url
    return url if url.endswith("/") else (url + "/")


def _default_advertised_url(host: str, port: int) -> str:
    # If you want a stable public URL without passing --card-url:
    # export AEGISFORGE_PUBLIC_URL="https://your-domain.example/"
    public = os.getenv("AEGISFORGE_PUBLIC_URL") or os.getenv("PUBLIC_URL")
    if public:
        return _normalize_base_url(public)

    # Never advertise 0.0.0.0 as a public URL.
    safe_host = "localhost" if host in {"0.0.0.0", "::", ""} else host
    return f"http://{safe_host}:{port}/"


def _card_to_dict(card: AgentCard) -> dict:
    # Support pydantic v2 and v1 (depending on SDK version)
    if hasattr(card, "model_dump"):
        return card.model_dump()  # type: ignore[attr-defined]
    if hasattr(card, "dict"):
        return card.dict()  # type: ignore[attr-defined]
    return dict(card.__dict__)


def build_agent_card(*, url: str) -> AgentCard:
    skills = [
        AgentSkill(
            id="quipuloop.aegisforge.unified_purple",
            name="AegisForge Unified Purple Agent",
            description=(
                "A2A-compatible Purple Agent for the selected AgentX-AgentBeats opponents. "
                "MCU/Minecraft is represented by the canonical mcu track; mcu-minecraft is treated as an alias, not a separate track."
            ),
            tags=list(AGENT_CARD_TAGS),
            examples=[
                "Route an MCU/Minecraft benchmark task through the mcu profile.",
                "Answer an OfficeQA finance task with evidence-aware document handling.",
                "Handle a CRMArena business-process task without exposing protected formulas.",
                "Process FieldWorkArena, MAizeBargAIn, tau2, OSWorld, Pi-Bench, CyberGym, or NetArena tasks through their selected profiles.",
            ],
        )
    ]

    return AgentCard(
        name="AegisForge (QuipuLoop)",
        description=(
            "Unified A2A Purple Agent for AgentX-AgentBeats Phase 2. "
            "Supports the selected opponent matrix across Game, Finance, Business Process, Research, Multi-agent, tau2, Computer Use/Web, Agent Safety, Cybersecurity, and Coding."
        ),
        url=_normalize_base_url(url),
        version=RUNTIME_VERSION,
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=skills,
    )


def build_app(*, host: str, port: int, card_url: str | None) -> Starlette:
    advertised_url = _normalize_base_url(card_url) if card_url else _default_advertised_url(host, port)
    agent_card = build_agent_card(url=advertised_url)

    request_handler = DefaultRequestHandler(
        agent_executor=Executor(),
        task_store=InMemoryTaskStore(),
    )

    a2a_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    ).build()

    async def health(_request):
        return JSONResponse({"status": "ok"})

    async def agent_card_route(_request):
        return JSONResponse(_card_to_dict(agent_card))

    # We explicitly serve both endpoints to avoid SDK/version differences.
    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/.well-known/agent-card.json", agent_card_route, methods=["GET"]),
            Route("/.well-known/agent.json", agent_card_route, methods=["GET"]),
            Mount("/", app=a2a_app),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AegisForge A2A server.")
    parser.add_argument("--host", type=str, default=os.getenv("AEGISFORGE_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AEGISFORGE_PORT", "8000")))
    parser.add_argument(
        "--card-url",
        type=str,
        default=os.getenv("AEGISFORGE_CARD_URL") or None,
        help="Public URL to advertise in the agent card (recommended in hosted setups).",
    )
    args = parser.parse_args()

    app = build_app(host=args.host, port=args.port, card_url=args.card_url)

    # IMPORTANT: container-safe bind + default port
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=True)


if __name__ == "__main__":
    main()
    