"""One-shot AegisForge Nexus + Nebius Nemotron integration smoke test.

Repository path: scripts/nexus/smoke_nebius_nemotron.py

--offline: entirely synthetic, zero network traffic, zero billing.
--live: exactly ONE Nebius chat completion via NexusOrchestrator.prepare().
        No automatic retries, no registered tools, no actions, no remote shell.

Dependencies are the user's existing src/aegisforge/nexus/ modules and requests.
An API key is read from NEBIUS_API_KEY or securely prompted, never printed.
"""
from __future__ import annotations

import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import sys
from typing import Any

from aegisforge.nexus.config import NexusConfig, NexusConfigError
from aegisforge.nexus.executor import NexusExecutor
from aegisforge.nexus.nebius_client import NebiusAPIError, NebiusReply
from aegisforge.nexus.orchestrator import NexusOrchestrationError
from aegisforge.nexus.planner import NexusPlanningError, NexusPlanner


REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = REPO_ROOT / "examples" / "nexus_demo" / "sample_task.json"


class OfflineNebiusClient:
    """In-memory fake, never sends HTTP. NOT evidence of a live API call."""

    def chat(self, messages: Any, *, max_tokens: int = 512, temperature: float | None = 0.2) -> NebiusReply:
        request = json.loads(messages[-1]["content"])
        payload = {
            "goal": "Revisar un resultado sintético de manera auditable",
            "steps": [
                {"id": "s1", "kind": "reason", "title": "Definir comparación",
                 "description": "Proponer métricas para revisar datos sintéticos sin modificar archivos",
                 "tool": None, "depends_on": [],
                 "success_criteria": "Existe una lista verificable de métricas y supuestos"},
                {"id": "s2", "kind": "verify", "title": "Comprobar evidencia",
                 "description": "Indicar cómo realizar una comprobación independiente",
                 "tool": None, "depends_on": ["s1"],
                 "success_criteria": "Se define una prueba independiente y un resultado esperado"},
            ],
            "assumptions": ["Solo se utilizan datos sintéticos"],
            "risks": ["Una propuesta no sustituye una prueba independiente"],
        }
        assert request["max_steps"] >= 2
        return NebiusReply(content=json.dumps(payload), model="simulated-nebius-not-real",
                           finish_reason="stop", usage={"total_tokens": 0}, request_id="offline-only")


def load_task() -> tuple[str, list[str]]:
    if not TASK_FILE.is_file():
        raise ValueError(f"Falta el archivo de ejemplo: {TASK_FILE}")
    data = json.loads(TASK_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("La tarea de ejemplo no contiene un objeto JSON.")
    task, constraints = data.get("task"), data.get("constraints", [])
    if not isinstance(task, str) or not task.strip():
        raise ValueError("sample_task.json debe incluir un campo 'task' no vacío.")
    if (not isinstance(constraints, list) or not all(isinstance(x, str) for x in constraints)):
        raise ValueError("sample_task.json debe incluir 'constraints' como lista de textos.")
    return task, constraints


def get_key() -> str:
    key = os.environ.get("NEBIUS_API_KEY", "").strip()
    if key:
        return key
    if not sys.stdin.isatty():
        raise ValueError("Falta NEBIUS_API_KEY y no hay consola interactiva para introducirla.")
    key = getpass("Introduce tu NEBIUS_API_KEY (entrada oculta; NO la pegues en el chat): ").strip()
    if not key:
        raise ValueError("No se recibió una clave API; se canceló sin llamar a Nebius.")
    return key


def make_orchestrator(live: bool, key: str | None):
    if live:
        # Use validated official Token Factory configuration. Cap output budget.
        environment = dict(os.environ)
        environment["NEBIUS_API_KEY"] = key or ""
        environment["NEXUS_PLANNER_MAX_STEPS"] = "3"
        environment["NEXUS_PLANNER_MAX_TOKENS"] = "3000"
        environment["NEBIUS_TIMEOUT_SECONDS"] = "90"
        settings = NexusConfig.from_env(environment, require_api_key=True)
        client = settings.build_client()
        planner = settings.build_planner(client=client)
    else:
        client = OfflineNebiusClient()
        planner = NexusPlanner(client, max_steps=3, max_tokens=3000)
    from aegisforge.nexus.orchestrator import NexusOrchestrator
    return NexusOrchestrator(planner, NexusExecutor()), client


def run(live: bool, save_evidence: bool) -> dict[str, Any]:
    task, constraints = load_task()  # Validation before any billing or HTTP.
    if live:
        print("Se realizará como máximo UNA inferencia en Nebius Token Factory.")
        print("Puede consumir créditos de la cuenta autorizada. No hay reintentos automáticos.")
        answer = input("¿Confirmas esta única petición? Escribe SI: ").strip().upper()
        if answer != "SI":
            raise ValueError("Operación cancelada sin realizar peticiones.")
    key = get_key() if live else None
    orchestrator, client = make_orchestrator(live, key)
    try:
        print("Modo:", "LIVE — solicitud REAL a Nebius" if live else "OFFLINE — prueba simulada")
        print("Preparando plan (ninguna herramienta podrá ejecutarse)...")
        prepared = orchestrator.prepare(task, constraints=constraints)  # Exactly one POST in LIVE.
        preview = orchestrator.preview(prepared)  # No HTTP and no tool calls.
        outcome = orchestrator.run(prepared, execute_tools=False)  # No HTTP and no tools.
        usage = prepared.plan.usage
        safe_usage = {name: value for name, value in usage.items()
                      if name in {"prompt_tokens", "completion_tokens", "total_tokens"}
                      and type(value) is int and value >= 0}
        summary = {
            "live_inference": live,
            "provider": "Nebius Token Factory" if live else "simulated/offline",
            "model": prepared.plan.model,
            "request_id": prepared.plan.request_id,
            "usage": safe_usage,
            "plan_valid": True,
            "step_count": len(prepared.plan.steps),
            "step_kinds": [step.kind for step in prepared.plan.steps],
            "preview_status": preview.status,
            "execution_status": outcome.execution.status,
            "verification_status": outcome.status,
            "tools_invoked": outcome.execution.tools_invoked,
            "independent_checks_run": outcome.verification.independent_checks_run,
            "audit_events": len(outcome.evidence.events),
            "audit_sha256": outcome.evidence.final_sha256,
        }
        # No secrets, raw conversation, or user-supplied task content are printed.
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("Resultado: llamada y plan válidos." if live else "Resultado: contrato local válido (no prueba la conexión real).")
        print("Nota: verification_status=inconclusive es esperado sin comprobadores independientes.")
        if save_evidence:
            folder = REPO_ROOT / "evidence" / "nexus"
            folder.mkdir(parents=True, exist_ok=True)
            label = "real" if live else "offline"
            audit = folder / f"nebius_smoke_{label}_{outcome.evidence.run_id}.jsonl"
            metadata = folder / f"nebius_smoke_{label}_{outcome.evidence.run_id}.json"
            outcome.evidence.save_jsonl(audit)  # O_EXCL/new file; hashes checked on read.
            with metadata.open("x", encoding="utf-8") as file:
                json.dump(summary, file, ensure_ascii=False, indent=2)
            print("Evidencia resumida en:", metadata)
            print("Traza de auditoría en:", audit)
        return summary
    finally:
        if hasattr(client, "close"):
            client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--offline", action="store_true", help="Simulación local, sin API ni gastos")
    modes.add_argument("--live", action="store_true", help="Una inferencia REAL con NVIDIA Nemotron en Nebius")
    parser.add_argument("--save-evidence", action="store_true", help="Guardar resumen sanitizado y auditoría en evidence/nexus")
    args = parser.parse_args()
    try:
        run(live=args.live, save_evidence=args.save_evidence)
        return 0
    except (ValueError, OSError, json.JSONDecodeError, NebiusAPIError,
            NexusConfigError, NexusPlanningError, NexusOrchestrationError) as exc:
        # The client errors do not reproduce API responses or auth headers.
        print(f"ERROR [{type(exc).__name__}]: {exc}", file=sys.stderr)
        print("No se realizarán más llamadas automáticamente.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
