"""A2A server entrypoint for AegisForge.

AgentBeats runs your Docker image by calling the ENTRYPOINT with:
  --host, --port, --card-url

This module must:
- Bind to the given host/port (container-safe defaults: 0.0.0.0:8000)
- Serve an Agent Card at /.well-known/agent-card.json
- For Pi-Bench, advertise the policy bootstrap contract that tells the
  orchestrator the decision must be emitted as an assistant tool_call to
  record_decision, not as a generic TextPart/DataPart payload.
"""

from __future__ import annotations

import argparse
import copy
import os
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from .executor import Executor


RUNTIME_VERSION = "1.2.0-pibench-bootstrap"

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
    "pi-bench",
    "agent-safety",
    "policy-bootstrap",
    "record-decision-tool-call",
    "cybergym",
    "netarena",
)

PI_BENCH_POLICY_BOOTSTRAP_URN = "urn:pi-bench:policy-bootstrap:v1"
PI_BENCH_DECISIONS = ("ALLOW", "ALLOW-CONDITIONAL", "DENY", "ESCALATE")

PI_BENCH_RECORD_DECISION_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "record_decision",
        "description": (
            "Record the Pi-Bench policy decision. This must be emitted as an "
            "assistant tool_call, not as plain visible text, TextPart, or DataPart."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": list(PI_BENCH_DECISIONS),
                    "description": "Canonical Pi-Bench decision.",
                },
                "rationale": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1200,
                    "description": "Brief policy-grounded reason for the decision.",
                },
            },
            "required": ["decision", "rationale"],
        },
    },
}

PI_BENCH_POLICY_BOOTSTRAP: dict[str, Any] = {
    "uri": PI_BENCH_POLICY_BOOTSTRAP_URN,
    "version": "1",
    "track": "pibench",
    "benchmark_aliases": ["pibench", "pi-bench", "pi_bench", "agent-safety", "agent_safety"],
    "decision_channel": "assistant.tool_calls",
    "required_tool_calls": ["record_decision"],
    "tool_call_contract": {
        "name": "record_decision",
        "arguments_shape": {
            "decision": list(PI_BENCH_DECISIONS),
            "rationale": "string",
        },
        "canonical_examples": [
            {
                "name": "record_decision",
                "arguments": {
                    "decision": "ALLOW",
                    "rationale": "The requested action is allowed by the provided policy context.",
                },
            },
            {
                "name": "record_decision",
                "arguments": {
                    "decision": "DENY",
                    "rationale": "The requested action conflicts with a policy constraint.",
                },
            },
        ],
    },
    "openai_compatible_tool": PI_BENCH_RECORD_DECISION_TOOL,
    "visible_output_policy": {
        "textpart_is_not_decision": True,
        "datapart_is_not_decision": True,
        "assistant_tool_call_required": True,
    },
}


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


def _card_to_raw_dict(card: AgentCard) -> dict[str, Any]:
    """Dump an AgentCard across SDK/pydantic versions without assuming aliases."""
    if hasattr(card, "model_dump"):
        return card.model_dump()  # type: ignore[attr-defined]
    if hasattr(card, "dict"):
        return card.dict()  # type: ignore[attr-defined]
    return dict(card.__dict__)


def _merge_unique_list(existing: Any, additions: list[Any]) -> list[Any]:
    out: list[Any] = list(existing) if isinstance(existing, list) else []
    for item in additions:
        if item not in out:
            out.append(item)
    return out


def _enrich_agent_card_dict(card_data: dict[str, Any]) -> dict[str, Any]:
    """Add Pi-Bench/A2A discovery fields to the public JSON card.

    The in-memory AgentCard is kept SDK-compatible, and this function enriches only
    the JSON served at /.well-known/agent-card.json and /.well-known/agent.json.
    This avoids breaking older a2a SDK model validation while still exposing the
    bootstrap extension to AgentBeats/Pi-Bench discovery.
    """

    data: dict[str, Any] = copy.deepcopy(card_data)

    # Serve both Python SDK field names and common JSON aliases. Some harnesses read
    # snake_case, others read lowerCamelCase.
    if "default_input_modes" in data:
        data.setdefault("defaultInputModes", data["default_input_modes"])
    else:
        data.setdefault("default_input_modes", ["text"])
        data.setdefault("defaultInputModes", ["text"])

    if "default_output_modes" in data:
        data.setdefault("defaultOutputModes", data["default_output_modes"])
    else:
        data.setdefault("default_output_modes", ["text"])
        data.setdefault("defaultOutputModes", ["text"])

    data.setdefault("protocolVersion", "a2a")
    data.setdefault("provider", {"organization": "QuipuLoop", "url": data.get("url", "")})

    capabilities = data.setdefault("capabilities", {})
    if isinstance(capabilities, dict):
        capabilities.setdefault("streaming", True)
        capabilities.setdefault("stateTransitionHistory", True)
        capabilities.setdefault("toolCalls", True)
        capabilities.setdefault("assistantToolCalls", True)

    extensions = data.setdefault("extensions", [])
    if not isinstance(extensions, list):
        extensions = []
        data["extensions"] = extensions

    extension_record = {
        "uri": PI_BENCH_POLICY_BOOTSTRAP_URN,
        "description": "Pi-Bench policy decision bootstrap for assistant tool_calls.",
        "required": False,
        "params": PI_BENCH_POLICY_BOOTSTRAP,
    }

    # Add both forms:
    # 1) raw URN string for simple harness checks:
    #      "urn:pi-bench:policy-bootstrap:v1" in extensions
    # 2) structured object for harnesses that inspect extension.uri / params.
    if PI_BENCH_POLICY_BOOTSTRAP_URN not in extensions:
        extensions.append(PI_BENCH_POLICY_BOOTSTRAP_URN)

    if not any(
        isinstance(item, dict) and item.get("uri") == PI_BENCH_POLICY_BOOTSTRAP_URN
        for item in extensions
    ):
        extensions.append(extension_record)

    metadata = data.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        data["metadata"] = metadata

    metadata.setdefault(
        "aegisforge",
        {
            "runtime_version": RUNTIME_VERSION,
            "agent_role": "purple",
            "selected_opponent_tracks": list(SELECTED_OPPONENT_TRACKS),
            "selected_opponent_repos": SELECTED_OPPONENT_REPOS,
        },
    )
    metadata["pi_bench_policy_bootstrap"] = PI_BENCH_POLICY_BOOTSTRAP

    # Tool declarations are duplicated under a few conventional keys because public
    # AgentBeats/Pi-Bench harness revisions have differed in what they probe.
    tools = _merge_unique_list(data.get("tools"), [PI_BENCH_RECORD_DECISION_TOOL])
    data["tools"] = tools
    data["toolDeclarations"] = tools
    data["tool_declarations"] = tools

    data["x-aegisforge-pibench-policy-bootstrap"] = PI_BENCH_POLICY_BOOTSTRAP
    return data


def _card_to_dict(card: AgentCard) -> dict[str, Any]:
    return _enrich_agent_card_dict(_card_to_raw_dict(card))


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
                'For Pi-Bench, emit assistant tool_calls: record_decision({"decision":"ALLOW|ALLOW-CONDITIONAL|DENY|ESCALATE","rationale":"..."}).',
            ],
        ),
        AgentSkill(
            id="quipuloop.aegisforge.pibench_policy_decision",
            name="Pi-Bench policy decision tool-call adapter",
            description=(
                "Advertises and preserves the Pi-Bench record_decision contract. "
                "Decision outputs must be assistant tool_calls with decision and rationale arguments."
            ),
            tags=[
                "pibench",
                "pi-bench",
                "agent-safety",
                "policy-bootstrap",
                "record_decision",
                "assistant-tool-calls",
            ],
            examples=[
                '{"tool_calls":[{"function":{"name":"record_decision","arguments":{"decision":"DENY","rationale":"The policy disallows the requested action."}}}]}',
            ],
        ),
    ]

    return AgentCard(
        name="AegisForge (QuipuLoop)",
        description=(
            "Unified A2A Purple Agent for AgentX-AgentBeats Phase 2. "
            "Supports the selected opponent matrix across Game, Finance, Business Process, Research, Multi-agent, tau2, Computer Use/Web, Agent Safety, Cybersecurity, and Coding. "
            "For Pi-Bench, the public agent card advertises the urn:pi-bench:policy-bootstrap:v1 decision-tool contract."
        ),
        url=_normalize_base_url(url),
        version=RUNTIME_VERSION,
        default_input_modes=["text", "application/json"],
        default_output_modes=["text", "application/json"],
        capabilities=AgentCapabilities(streaming=True),
        skills=skills,
    )


def build_app(*, host: str, port: int, card_url: str | None) -> Starlette:
    advertised_url = _normalize_base_url(card_url) if card_url else _default_advertised_url(host, port)
    agent_card = build_agent_card(url=advertised_url)
    public_agent_card = _card_to_dict(agent_card)

    executor = Executor()

    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
    )

    a2a_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    ).build()

    async def health(_request):
        snapshot = executor.snapshot() if hasattr(executor, "snapshot") else {}
        return JSONResponse(
            {
                "status": "ok",
                "runtime_version": RUNTIME_VERSION,
                "pibench_policy_bootstrap": True,
                "pi_bench_extension": PI_BENCH_POLICY_BOOTSTRAP_URN,
                "executor": snapshot,
            }
        )

    async def agent_card_route(_request):
        return JSONResponse(public_agent_card)

    async def pibench_bootstrap_route(_request):
        return JSONResponse(PI_BENCH_POLICY_BOOTSTRAP)

    # We explicitly serve both endpoints to avoid SDK/version differences.
    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/.well-known/agent-card.json", agent_card_route, methods=["GET"]),
            Route("/.well-known/agent.json", agent_card_route, methods=["GET"]),
            Route("/pi-bench/policy-bootstrap", pibench_bootstrap_route, methods=["GET"]),
            Route("/pibench/policy-bootstrap", pibench_bootstrap_route, methods=["GET"]),
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
