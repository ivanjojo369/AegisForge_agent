from __future__ import annotations

"""SkillsBench task-family harness for AegisForge.

The harness is the strategic layer of the SkillsBench adapter.  It receives a
normalized SkillsBenchRequest, inspects a safe workspace snapshot, chooses a
task-family plan, and emits deterministic files through result_emitter.py.

Design constraints:
- no network access;
- no shell execution;
- no direct secret handling;
- no private/hidden answer lookup;
- no benchmark-specific solution tables;
- optional external reasoning callback can be injected by agent.py, but this
  module itself remains pure and offline.

Expected flow:

    contract.normalize_skillsbench_request(...)
      -> SkillsBenchHarness.handle_request(...)
      -> result_emitter.emit_result(...)
      -> adapter.py / agent.py / executor.py collect final_text + artifacts
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any
import csv
import io
import json
import math
import re
import statistics
import os

from .contract import (
    SkillsBenchRequest,
    normalize_skillsbench_request,
    request_debug_summary,
)
from .result_emitter import (
    SkillsBenchEmission,
    emission_as_agent_response,
    emit_failure_result,
    emit_result,
)
from .task_catalog import (
    TASK_CATALOG_VERSION,
    catalog_summary,
    classify_task,
    preferred_outputs_for_family,
)
from .workspace import SkillsBenchWorkspace
from .output_contract import (
    OUTPUT_CONTRACT_VERSION,
    build_output_contract,
    summarize_contract,
)
from .task_environment import (
    TASK_ENVIRONMENT_VERSION,
    discover_task_environment,
)
from .task_workspace_executor import (
    TASK_WORKSPACE_EXECUTOR_VERSION,
    SkillsBenchTaskWorkspaceExecutor,
)
from .solvers import default_solver_registry


HARNESS_VERSION = "skillsbench_harness_v0_3_2_stdout_workspace_executor_probe_2026_06_03"

ReasonerCallback = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class HarnessAction:
    """One planned action.  These are plans, not shell executions."""

    name: str
    family: str
    purpose: str
    inputs: tuple[str, ...] = field(default_factory=tuple)
    outputs: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.5
    risk: str = "low"
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["inputs"] = list(self.inputs)
        data["outputs"] = list(self.outputs)
        return data


@dataclass(frozen=True)
class HarnessPlan:
    """Strategy selected for a normalized SkillsBench request."""

    version: str
    task_id: str
    category: str
    difficulty: str
    family: str
    tags: tuple[str, ...]
    expected_outputs: tuple[str, ...]
    actions: tuple[HarnessAction, ...]
    workspace_summary: dict[str, Any]
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tags"] = list(self.tags)
        data["expected_outputs"] = list(self.expected_outputs)
        data["actions"] = [action.as_dict() for action in self.actions]
        data["assumptions"] = list(self.assumptions)
        data["warnings"] = list(self.warnings)
        return data


@dataclass(frozen=True)
class HarnessResult:
    """Pre-emission harness output."""

    request: SkillsBenchRequest
    plan: HarnessPlan
    answer: str
    files: tuple[dict[str, Any], ...]
    artifacts: tuple[dict[str, Any], ...]
    validation: dict[str, Any]
    diagnostics: dict[str, Any]
    status: str = "completed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.as_dict(),
            "plan": self.plan.as_dict(),
            "answer": self.answer,
            "files": list(self.files),
            "artifacts": list(self.artifacts),
            "validation": dict(self.validation),
            "diagnostics": dict(self.diagnostics),
            "status": self.status,
        }


def _safe_text(value: Any, *, limit: int = 12000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except Exception:
        return str(value)[:limit]


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def _slug(value: Any, *, fallback: str = "skillsbench") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_.+\-]+", "-", text).strip("-._")
    return text or fallback


def _first_text_excerpt(workspace_summary: Mapping[str, Any], *, max_chars: int = 6000) -> str:
    parts: list[str] = []
    files = workspace_summary.get("files")
    if isinstance(files, list):
        for item in files[:10]:
            if isinstance(item, Mapping):
                excerpt = item.get("text_excerpt")
                if isinstance(excerpt, str) and excerpt.strip():
                    parts.append(f"### {item.get('relative_path') or item.get('name')}\n{excerpt.strip()}")
                    if sum(len(p) for p in parts) >= max_chars:
                        break
    return "\n\n".join(parts)[:max_chars]


def _workspace_file_names(workspace_summary: Mapping[str, Any], *, limit: int = 80) -> list[str]:
    files = workspace_summary.get("files")
    out: list[str] = []
    if isinstance(files, list):
        for item in files[:limit]:
            if isinstance(item, Mapping):
                name = str(item.get("relative_path") or item.get("name") or "").strip()
                if name:
                    out.append(name)
    return out


def _suffix_counts(workspace_summary: Mapping[str, Any]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    files = workspace_summary.get("files")
    if isinstance(files, list):
        for item in files:
            if isinstance(item, Mapping):
                suffix = str(item.get("suffix") or "").lower() or "<none>"
                counter[suffix] += 1
    return dict(sorted(counter.items()))


def _parse_csv_excerpt(text: str) -> dict[str, Any]:
    if not text.strip():
        return {"ok": False, "reason": "empty"}
    try:
        reader = csv.reader(io.StringIO(text))
        rows = [row for _, row in zip(range(80), reader)]
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:200]}
    if not rows:
        return {"ok": False, "reason": "no rows"}
    widths = [len(row) for row in rows]
    return {
        "ok": True,
        "rows_sampled": len(rows),
        "columns_first_row": len(rows[0]),
        "column_widths": {
            "min": min(widths),
            "max": max(widths),
            "median": statistics.median(widths) if widths else 0,
        },
        "header": rows[0][:30],
    }


def _output_spec_name(value: Any) -> str:
    """Return an output artifact name from dicts or SkillsBenchArtifactRequest objects.

    contract.py represents expected outputs as SkillsBenchArtifactRequest
    dataclasses.  Older harness code treated those as dictionaries and called
    `.get(...)`, which caused:
        'SkillsBenchArtifactRequest' object has no attribute 'get'

    This helper keeps the harness tolerant of both shapes.
    """

    if value is None:
        return ""
    if isinstance(value, Mapping):
        raw = value.get("name") or value.get("file_name") or value.get("filename")
    else:
        raw = (
            getattr(value, "name", None)
            or getattr(value, "file_name", None)
            or getattr(value, "filename", None)
        )
    text = str(raw or "").strip()
    return text


def _output_spec_mime(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        raw = value.get("mime_type") or value.get("mime") or value.get("content_type")
    else:
        raw = (
            getattr(value, "mime_type", None)
            or getattr(value, "mime", None)
            or getattr(value, "content_type", None)
        )
    return str(raw or "").strip()


def _expected_output_names(expected_outputs: Sequence[Any], *, family: str) -> tuple[str, ...]:
    names: list[str] = []
    for item in expected_outputs or ():
        name = _output_spec_name(item)
        if name:
            names.append(name)
    return tuple(names) or tuple(preferred_outputs_for_family(family))


class SkillsBenchHarness:
    """Family-aware, offline SkillsBench harness.

    ``reasoner`` is optional.  agent.py may pass a callback that takes a JSON
    context and returns a task-specific answer.  If omitted, the harness emits a
    deterministic plan and structured artifacts based on workspace signals.
    """

    def __init__(
        self,
        *,
        reasoner: ReasonerCallback | None = None,
        include_text_context: bool = True,
        max_text_context_chars: int = 60000,
    ) -> None:
        self.reasoner = reasoner
        self.include_text_context = include_text_context
        self.max_text_context_chars = max(1000, int(max_text_context_chars))
        self.last_plan: HarnessPlan | None = None
        self.last_result: HarnessResult | None = None
        self.last_emission: SkillsBenchEmission | None = None
        self.last_task_environment: Any = None
        self.last_output_contract: Any = None
        self.last_workspace_execution: Any = None
        self.last_error: str = ""

    def handle(
        self,
        *,
        message: Any = None,
        metadata: Mapping[str, Any] | None = None,
        text: str = "",
        request: SkillsBenchRequest | None = None,
    ) -> dict[str, Any]:
        """High-level entry point returning current agent.py-compatible shape."""

        emission = self.handle_to_emission(message=message, metadata=metadata, text=text, request=request)
        return emission_as_agent_response(emission)

    def handle_to_emission(
        self,
        *,
        message: Any = None,
        metadata: Mapping[str, Any] | None = None,
        text: str = "",
        request: SkillsBenchRequest | None = None,
    ) -> SkillsBenchEmission:
        """Normalize, plan, run family strategy, and emit result files."""

        try:
            normalized = request or normalize_skillsbench_request(
                message=message,
                metadata=metadata,
                text=text,
                include_workspace=False,
            )
            workspace = SkillsBenchWorkspace.discover(
                normalized.metadata,
                normalized.prompt,
                task_id=normalized.task_id or "skillsbench_task",
            )
            result = self.run_request(normalized, workspace=workspace)
            emission = emit_result(
                normalized,
                answer=result.answer,
                files=result.files,
                artifacts=result.artifacts,
                commands=[action.as_dict() for action in result.plan.actions],
                validation=result.validation,
                diagnostics=result.diagnostics,
                status=result.status,
                workspace=workspace,
            )
            self.last_emission = emission
            return emission
        except Exception as exc:
            self.last_error = str(exc)
            fallback = request or normalize_skillsbench_request(message=message, metadata=metadata, text=text)
            workspace = SkillsBenchWorkspace.discover(fallback.metadata, fallback.prompt, task_id=fallback.task_id or "skillsbench_task")
            emission = emit_failure_result(
                fallback,
                error=str(exc),
                diagnostics={
                    "harness_version": HARNESS_VERSION,
                    "stage": "handle_to_emission",
                    "exception_type": exc.__class__.__name__,
                    "exception": str(exc)[:500],
                },
                workspace=workspace,
            )
            self.last_emission = emission
            return emission

    def run_request(self, request: SkillsBenchRequest, *, workspace: SkillsBenchWorkspace | None = None) -> HarnessResult:
        workspace = workspace or SkillsBenchWorkspace.discover(request.metadata, request.prompt, task_id=request.task_id or "skillsbench_task")
        workspace_summary = workspace.as_context(include_text=self.include_text_context)
        if self.include_text_context:
            self._trim_workspace_text(workspace_summary)

        plan = self.plan_request(request, workspace_summary=workspace_summary)
        self.last_plan = plan

        family = request.family or plan.family or "general"
        strategy = {
            "software_patch": self._solve_software_patch,
            "security_audit": self._solve_security_audit,
            "office_document": self._solve_office_document,
            "spreadsheet_finance": self._solve_spreadsheet_finance,
            "scientific_compute": self._solve_scientific_compute,
            "industrial_control": self._solve_industrial_control,
            "media_processing": self._solve_media_processing,
            "formal_reasoning": self._solve_formal_reasoning,
            "data_json": self._solve_data_json,
            "general": self._solve_general,
        }.get(family, self._solve_general)

        result = strategy(request, plan, workspace_summary)
        result = self._augment_with_task_workspace_execution(request, plan, workspace_summary, result)
        self.last_result = result
        return result


    def _workspace_executor_enabled(self) -> bool:
        """Whether to run the real-filesystem SkillsBench workspace executor."""

        raw = os.getenv("AEGISFORGE_SKILLSBENCH_WORKSPACE_EXECUTOR", "1").strip().lower()
        return raw not in {"0", "false", "no", "off"}

    def _workspace_executor_allow_writes(self) -> bool:
        """Writes are enabled by default only for SkillsBench runtime paths.

        The executor itself still refuses to write when it cannot see known task
        roots.  This flag lets us turn the writer into dry-run diagnostics in a
        local development run if needed.
        """

        raw = os.getenv("AEGISFORGE_SKILLSBENCH_WORKSPACE_WRITES", "1").strip().lower()
        return raw not in {"0", "false", "no", "off", "dry-run", "dryrun"}

    def _workspace_executor_write_probe(self) -> bool:
        raw = os.getenv("AEGISFORGE_SKILLSBENCH_WORKSPACE_WRITE_PROBE", "0").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def _augment_with_task_workspace_execution(
        self,
        request: SkillsBenchRequest,
        plan: HarnessPlan,
        workspace_summary: Mapping[str, Any],
        result: HarnessResult,
    ) -> HarnessResult:
        """Add real-filesystem SkillsBench execution diagnostics and writes.

        The old harness emitted only A2A/result_emitter artifacts.  Quick Submit
        showed that the SkillsBench worker keeps `artifact_refs: []` even when
        those artifacts are present.  This v0.3 layer extracts the actual output
        contract from the instruction, discovers task roots such as /root,
        /app/workspace, and /home/github/build, and writes real files when the
        task filesystem is visible.
        """

        if not self._workspace_executor_enabled():
            diagnostics = dict(result.diagnostics)
            diagnostics["task_workspace_executor"] = {
                "enabled": False,
                "version": TASK_WORKSPACE_EXECUTOR_VERSION,
                "reason": "AEGISFORGE_SKILLSBENCH_WORKSPACE_EXECUTOR disabled",
            }
            return HarnessResult(
                request=result.request,
                plan=result.plan,
                answer=result.answer,
                files=result.files,
                artifacts=result.artifacts,
                validation=result.validation,
                diagnostics=diagnostics,
                status=result.status,
            )

        try:
            contract = build_output_contract(
                request.metadata,
                request.prompt,
                task_id=request.task_id,
                category=request.category,
                difficulty=request.difficulty,
            )
            environment = discover_task_environment(
                request.metadata,
                request.prompt,
                task_id=request.task_id or "skillsbench_task",
                write_probe=self._workspace_executor_write_probe(),
                sample=True,
            )
            executor = SkillsBenchTaskWorkspaceExecutor(
                allow_writes=self._workspace_executor_allow_writes(),
                write_probe=self._workspace_executor_write_probe(),
                solver_registry=default_solver_registry(),
            )
            execution = executor.execute(
                request.metadata,
                request.prompt,
                contract=contract,
                environment=environment,
            )

            # Visible stdout probe for Quick Submit / GitHub logs.
            # Official SkillsBench results hide A2A artifact diagnostics, so this
            # line is the hard signal for whether the participant process can see
            # and write the real task filesystem.
            try:
                contract_context = contract.as_context() if hasattr(contract, "as_context") else {}
                environment_context = environment.as_context() if hasattr(environment, "as_context") else {}
                print(
                    "AEGISFORGE_SKILLSBENCH_WORKSPACE_EXECUTOR "
                    + json.dumps(
                        {
                            "marker": "skillsbench_harness_v0_3_2_stdout_workspace_executor_probe_2026_06_03",
                            "harness_version": HARNESS_VERSION,
                            "status": execution.status,
                            "ok": execution.ok,
                            "workspace_visible": execution.workspace_visible,
                            "wrote_any_file": execution.wrote_any_file,
                            "write_count": len(execution.writes),
                            "ok_writes": sum(1 for item in execution.writes if item.ok),
                            "artifact_record_count": len(execution.artifact_records()),
                            "output_contract_version": OUTPUT_CONTRACT_VERSION,
                            "task_environment_version": TASK_ENVIRONMENT_VERSION,
                            "task_workspace_executor_version": TASK_WORKSPACE_EXECUTOR_VERSION,
                            "task_id": request.task_id,
                            "category": request.category,
                            "difficulty": request.difficulty,
                            "family": plan.family,
                            "primary_outputs": list(
                                contract_context.get("primary_outputs")
                                or contract_context.get("outputs")
                                or contract_context.get("expected_outputs")
                                or []
                            )[:8],
                            "candidate_roots": list(
                                environment_context.get("candidate_roots")
                                or environment_context.get("roots")
                                or environment_context.get("known_roots")
                                or []
                            )[:12],
                        },
                        ensure_ascii=False,
                        default=str,
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except Exception as log_exc:
                print(
                    "AEGISFORGE_SKILLSBENCH_WORKSPACE_EXECUTOR_LOG_ERROR "
                    + json.dumps(
                        {
                            "marker": "skillsbench_harness_v0_3_2_stdout_workspace_executor_probe_2026_06_03",
                            "harness_version": HARNESS_VERSION,
                            "error_type": log_exc.__class__.__name__,
                            "error": str(log_exc)[:500],
                        },
                        ensure_ascii=False,
                        default=str,
                        sort_keys=True,
                    ),
                    flush=True,
                )

            self.last_output_contract = contract
            self.last_task_environment = environment
            self.last_workspace_execution = execution

            diagnostics = dict(result.diagnostics)
            diagnostics["output_contract_version"] = OUTPUT_CONTRACT_VERSION
            diagnostics["task_environment_version"] = TASK_ENVIRONMENT_VERSION
            diagnostics["task_workspace_executor_version"] = TASK_WORKSPACE_EXECUTOR_VERSION
            diagnostics["output_contract"] = contract.as_context()
            diagnostics["output_contract_summary"] = summarize_contract(contract)
            diagnostics["task_environment"] = environment.as_context()
            diagnostics["task_workspace_execution"] = execution.as_dict()

            validation = dict(result.validation)
            validation["task_workspace_executor"] = {
                "ok": execution.ok,
                "status": execution.status,
                "workspace_visible": execution.workspace_visible,
                "wrote_any_file": execution.wrote_any_file,
                "write_count": len(execution.writes),
                "ok_writes": sum(1 for item in execution.writes if item.ok),
            }

            # Add a compact, emitted diagnostic file to the existing result
            # package.  This is not the scoring channel; it is a trace that
            # proves whether the real task filesystem was visible and writable.
            extra_files = list(result.files)
            extra_files.append(
                {
                    "artifact_name": "task_workspace_execution",
                    "file_name": "task_workspace_execution.json",
                    "mime_type": "application/json",
                    "payload": execution.as_dict(),
                }
            )
            extra_files.append(
                {
                    "artifact_name": "output_contract",
                    "file_name": "skillsbench_output_contract.json",
                    "mime_type": "application/json",
                    "payload": contract.as_dict(),
                }
            )

            # If real filesystem writes occurred, add artifact descriptors for
            # visibility.  result_emitter may not read absolute task paths, so
            # these records are primarily diagnostics for executor.py.
            extra_artifacts = list(result.artifacts)
            for record in execution.artifact_records():
                extra_artifacts.append(record)

            answer = result.answer
            if execution.status == "task_filesystem_not_visible":
                answer += (
                    "\\n\\n[SkillsBench workspace executor] No known task filesystem root was visible "
                    "from the A2A process; this suggests the participant server is isolated from the "
                    "Harbor task sandbox."
                )
            elif execution.wrote_any_file:
                answer += (
                    f"\\n\\n[SkillsBench workspace executor] Wrote {sum(1 for item in execution.writes if item.ok)} "
                    "real task filesystem output(s)."
                )
            else:
                answer += (
                    f"\\n\\n[SkillsBench workspace executor] Ran with status `{execution.status}`; no real task output file was written."
                )

            return HarnessResult(
                request=result.request,
                plan=result.plan,
                answer=answer,
                files=tuple(dict(item) for item in extra_files),
                artifacts=tuple(dict(item) for item in extra_artifacts),
                validation=validation,
                diagnostics=diagnostics,
                status=result.status,
            )
        except Exception as exc:
            self.last_error = str(exc)
            try:
                print(
                    "AEGISFORGE_SKILLSBENCH_WORKSPACE_EXECUTOR_EXCEPTION "
                    + json.dumps(
                        {
                            "marker": "skillsbench_harness_v0_3_2_stdout_workspace_executor_probe_2026_06_03",
                            "harness_version": HARNESS_VERSION,
                            "task_workspace_executor_version": TASK_WORKSPACE_EXECUTOR_VERSION,
                            "error_type": exc.__class__.__name__,
                            "error": str(exc)[:800],
                            "task_id": request.task_id,
                            "category": request.category,
                            "difficulty": request.difficulty,
                            "family": plan.family,
                        },
                        ensure_ascii=False,
                        default=str,
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except Exception:
                pass
            diagnostics = dict(result.diagnostics)
            diagnostics["task_workspace_executor"] = {
                "enabled": True,
                "version": TASK_WORKSPACE_EXECUTOR_VERSION,
                "error_type": exc.__class__.__name__,
                "error": str(exc)[:800],
            }
            validation = dict(result.validation)
            validation["task_workspace_executor"] = {
                "ok": False,
                "status": "exception",
                "error": str(exc)[:400],
            }
            return HarnessResult(
                request=result.request,
                plan=result.plan,
                answer=result.answer + f"\\n\\n[SkillsBench workspace executor unavailable: {str(exc)[:240]}]",
                files=result.files,
                artifacts=result.artifacts,
                validation=validation,
                diagnostics=diagnostics,
                status=result.status,
            )

    def plan_request(self, request: SkillsBenchRequest, *, workspace_summary: Mapping[str, Any]) -> HarnessPlan:
        classification = classify_task(request.metadata, request.prompt)
        family = str(classification.get("family") or request.family or "general")
        expected_outputs = _expected_output_names(request.expected_outputs, family=family)

        names = _workspace_file_names(workspace_summary)
        suffixes = _suffix_counts(workspace_summary)
        warnings: list[str] = []
        assumptions: list[str] = [
            "The official SkillsBench green/worker owns hidden tests and scoring.",
            "This harness uses public task metadata and bounded workspace inspection only.",
            "Actions are strategy records; this module does not execute shell commands.",
        ]

        if not request.has_known_task:
            warnings.append("Unknown task_id; strategy inferred from metadata/text signals.")
        if not names:
            warnings.append("Workspace appears empty or inaccessible from the participant container.")

        actions = self._actions_for_family(request, family, names=names, expected_outputs=expected_outputs)

        return HarnessPlan(
            version=HARNESS_VERSION,
            task_id=request.task_id,
            category=request.category,
            difficulty=request.difficulty,
            family=family,
            tags=tuple(request.tags),
            expected_outputs=tuple(expected_outputs),
            actions=tuple(actions),
            workspace_summary={
                "root": workspace_summary.get("root"),
                "input_dir": workspace_summary.get("input_dir"),
                "output_dir": workspace_summary.get("output_dir"),
                "file_count": workspace_summary.get("file_count"),
                "suffix_counts": suffixes,
                "sample_files": names[:40],
                "warnings": workspace_summary.get("warnings", []),
            },
            assumptions=tuple(assumptions),
            warnings=tuple(warnings),
        )

    def _trim_workspace_text(self, workspace_summary: dict[str, Any]) -> None:
        remaining = self.max_text_context_chars
        files = workspace_summary.get("files")
        if not isinstance(files, list):
            return
        for item in files:
            if not isinstance(item, dict):
                continue
            excerpt = item.get("text_excerpt")
            if not isinstance(excerpt, str):
                continue
            if remaining <= 0:
                item.pop("text_excerpt", None)
                item["text_excerpt_omitted"] = True
                continue
            if len(excerpt) > remaining:
                item["text_excerpt"] = excerpt[:remaining]
                item["text_excerpt_truncated"] = True
                remaining = 0
            else:
                remaining -= len(excerpt)

    def _actions_for_family(
        self,
        request: SkillsBenchRequest,
        family: str,
        *,
        names: Sequence[str],
        expected_outputs: Sequence[str],
    ) -> list[HarnessAction]:
        common_inputs = tuple(names[:12])
        common_outputs = tuple(expected_outputs[:6])

        base = [
            HarnessAction(
                name="inspect_task_workspace",
                family=family,
                purpose="List and classify bounded input files before choosing a deliverable shape.",
                inputs=common_inputs,
                outputs=("workspace_manifest.json",),
                confidence=0.85,
            ),
            HarnessAction(
                name="emit_worker_compatible_outputs",
                family=family,
                purpose="Persist result files, output manifest, and artifact records through result_emitter.py.",
                inputs=common_inputs,
                outputs=common_outputs,
                confidence=0.8,
            ),
        ]

        family_specific = {
            "software_patch": [
                HarnessAction("derive_patch_plan", family, "Identify likely source/build files and produce a patch-oriented repair plan.", common_inputs, common_outputs, 0.72),
                HarnessAction("write_validation_commands", family, "Describe deterministic build/test commands for worker-side validation.", common_inputs, ("tests.md",), 0.7),
            ],
            "security_audit": [
                HarnessAction("map_attack_surface", family, "Summarize dependencies, PCAPs, rules, or vulnerable components without executing exploit code.", common_inputs, common_outputs, 0.68),
                HarnessAction("write_findings_manifest", family, "Emit structured findings/rules/remediation notes.", common_inputs, ("findings.json", "security_report.md"), 0.72),
            ],
            "office_document": [
                HarnessAction("extract_document_fields", family, "Infer form/redaction/citation/edit requirements from office inputs.", common_inputs, common_outputs, 0.7),
                HarnessAction("write_document_manifest", family, "Emit structured field/edit manifest plus readable validation notes.", common_inputs, ("fields_or_edits.json", "document_result.md"), 0.74),
            ],
            "spreadsheet_finance": [
                HarnessAction("profile_tables", family, "Sample CSV/XLSX-like inputs and infer table/formula outputs.", common_inputs, ("analysis.csv", "workbook_result.json"), 0.68),
                HarnessAction("write_workbook_notes", family, "Emit formulas, assumptions, and validation caveats.", common_inputs, ("workbook_notes.md",), 0.72),
            ],
            "scientific_compute": [
                HarnessAction("derive_numeric_schema", family, "Identify data columns, units, and expected numerical answer structure.", common_inputs, ("solution.json", "analysis.md"), 0.66),
                HarnessAction("write_validation_summary", family, "Emit reproducibility notes and sanity checks.", common_inputs, ("validation.csv",), 0.62),
            ],
            "industrial_control": [
                HarnessAction("identify_system_model", family, "Map inputs to geometry/control/energy/manufacturing model components.", common_inputs, ("solution.json", "parameters.csv"), 0.66),
                HarnessAction("write_simulation_plan", family, "Emit deterministic simulation/control-plan notes.", common_inputs, ("simulation_notes.md",), 0.62),
            ],
            "media_processing": [
                HarnessAction("inventory_media_assets", family, "Identify video/audio/image/3D assets and intended transformations.", common_inputs, ("media_manifest.json",), 0.68),
                HarnessAction("write_asset_plan", family, "Emit asset conversion/edit plan and output manifest.", common_inputs, ("asset_notes.md", "output_plan.json"), 0.67),
            ],
            "formal_reasoning": [
                HarnessAction("formalize_constraints", family, "Extract proof/optimization/planning constraints into a structured form.", common_inputs, ("solution.json",), 0.66),
                HarnessAction("write_proof_or_model_notes", family, "Emit Lean/proof/solver scaffold plus validation notes.", common_inputs, ("solution.lean", "proof_notes.md"), 0.62),
            ],
            "data_json": [
                HarnessAction("infer_schema", family, "Infer parser/mapping/search schema from task prompt and text inputs.", common_inputs, ("solution.json",), 0.74),
                HarnessAction("write_parser_notes", family, "Emit deterministic mapping/parser notes.", common_inputs, ("parser_or_mapping.md",), 0.7),
            ],
            "general": [
                HarnessAction("general_task_decomposition", family, "Decompose task into inputs, outputs, assumptions, and validation checks.", common_inputs, common_outputs, 0.55),
            ],
        }.get(family, [])

        return base + list(family_specific)

    def _make_reasoner_context(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "harness_version": HARNESS_VERSION,
            "task": {
                "task_id": request.task_id,
                "category": request.category,
                "difficulty": request.difficulty,
                "tags": list(request.tags),
                "family": request.family,
            },
            "prompt": request.prompt,
            "plan": plan.as_dict(),
            "workspace": workspace_summary,
            "catalog": {
                "version": TASK_CATALOG_VERSION,
                "summary": catalog_summary(),
            },
            "instructions": [
                "Produce a concrete task answer if enough information is present.",
                "Do not invent private test data or hidden answers.",
                "Prefer structured, reproducible output content.",
                "Do not request secrets or network-only operations.",
            ],
        }

    def _call_reasoner(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any]) -> str:
        if self.reasoner is None:
            return ""
        try:
            return _safe_text(self.reasoner(self._make_reasoner_context(request, plan, workspace_summary)), limit=120000)
        except Exception as exc:
            return f"[reasoner unavailable: {str(exc)[:240]}]"

    def _base_diagnostics(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "harness_version": HARNESS_VERSION,
            "contract": request_debug_summary(request),
            "plan": plan.as_dict(),
            "workspace_suffix_counts": _suffix_counts(workspace_summary),
            "workspace_file_names": _workspace_file_names(workspace_summary, limit=60),
            "catalog_summary": catalog_summary(),
        }

    def _result(
        self,
        request: SkillsBenchRequest,
        plan: HarnessPlan,
        workspace_summary: Mapping[str, Any],
        *,
        answer: str,
        files: Sequence[Mapping[str, Any]],
        artifacts: Sequence[Mapping[str, Any]] | None = None,
        validation: Mapping[str, Any] | None = None,
        status: str = "completed",
    ) -> HarnessResult:
        diagnostics = self._base_diagnostics(request, plan, workspace_summary)
        return HarnessResult(
            request=request,
            plan=plan,
            answer=answer,
            files=tuple(dict(item) for item in files),
            artifacts=tuple(dict(item) for item in (artifacts or [])),
            validation=dict(validation or {}),
            diagnostics=diagnostics,
            status=status,
        )

    def _family_header(self, request: SkillsBenchRequest, plan: HarnessPlan) -> str:
        return (
            f"SkillsBench task `{request.task_id or 'unknown'}` classified as `{plan.family}` "
            f"within category `{request.category or 'unknown'}`. "
            "AegisForge prepared a worker-compatible output package with bounded workspace inspection."
        )

    def _generic_structured_file(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any], *, family_notes: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "artifact_name": "solution",
            "file_name": "solution.json",
            "mime_type": "application/json",
            "payload": {
                "schema": "aegisforge.skillsbench.solution.v0_1",
                "task_id": request.task_id,
                "category": request.category,
                "family": plan.family,
                "difficulty": request.difficulty,
                "tags": list(request.tags),
                "workspace": {
                    "file_count": workspace_summary.get("file_count"),
                    "suffix_counts": _suffix_counts(workspace_summary),
                    "sample_files": _workspace_file_names(workspace_summary, limit=40),
                },
                "plan": plan.as_dict(),
                "family_notes": dict(family_notes),
            },
        }

    def _solve_general(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any]) -> HarnessResult:
        reasoned = self._call_reasoner(request, plan, workspace_summary)
        answer = reasoned or (
            self._family_header(request, plan)
            + "\n\nNo task-specific family solver was selected, so this output contains a structured decomposition, expected deliverables, and validation notes."
        )
        files = [
            self._generic_structured_file(request, plan, workspace_summary, family_notes={"mode": "general"}),
            {
                "artifact_name": "validation_notes",
                "file_name": "validation_notes.md",
                "mime_type": "text/markdown",
                "text": self._validation_notes(request, plan, workspace_summary),
            },
        ]
        return self._result(request, plan, workspace_summary, answer=answer, files=files, validation={"mode": "general", "ok": True})

    def _solve_data_json(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any]) -> HarnessResult:
        reasoned = self._call_reasoner(request, plan, workspace_summary)
        excerpt = _first_text_excerpt(workspace_summary, max_chars=12000)
        schema_guess = {
            "task_id": request.task_id,
            "input_files": _workspace_file_names(workspace_summary, limit=40),
            "detected_text_excerpt_present": bool(excerpt),
            "recommended_output": "solution.json",
            "parser_strategy": [
                "identify dialogue/data schema",
                "preserve source ordering",
                "emit strict JSON with validation notes",
            ],
        }
        answer = reasoned or self._family_header(request, plan)
        files = [
            {
                "artifact_name": "solution",
                "file_name": "solution.json",
                "mime_type": "application/json",
                "payload": schema_guess,
            },
            {
                "artifact_name": "parser_or_mapping",
                "file_name": "parser_or_mapping.md",
                "mime_type": "text/markdown",
                "text": self._notes_markdown(request, plan, workspace_summary, title="Parser / mapping notes"),
            },
        ]
        return self._result(request, plan, workspace_summary, answer=answer, files=files, validation={"json_output": True})

    def _solve_software_patch(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any]) -> HarnessResult:
        reasoned = self._call_reasoner(request, plan, workspace_summary)
        names = _workspace_file_names(workspace_summary, limit=80)
        likely_sources = [name for name in names if PathLikeSuffix.matches(name, {".py", ".java", ".js", ".ts", ".tsx", ".jsx", ".scala", ".go", ".rs", ".c", ".cpp", ".h", ".hpp", ".xml", ".gradle", ".pom", ".toml", ".yaml", ".yml"})]
        patch_text = (
            "diff --git a/README.skillsbench.md b/README.skillsbench.md\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/README.skillsbench.md\n"
            "@@\n"
            "+# AegisForge SkillsBench repair plan\n"
            "+\n"
            "+The participant identified a software-repair task. The concrete patch should be derived from the task workspace and validated with the commands listed in tests.md.\n"
        )
        repair_manifest = {
            "task_id": request.task_id,
            "family": plan.family,
            "likely_source_files": likely_sources[:40],
            "strategy": [
                "inspect build/test failure",
                "patch minimal failing source/config",
                "run deterministic validation commands in task environment",
            ],
            "note": "This manifest is strategy-oriented; no hidden benchmark answer is embedded.",
        }
        answer = reasoned or self._family_header(request, plan)
        files = [
            {"artifact_name": "solution_patch", "file_name": "solution.patch", "mime_type": "text/x-diff", "text": patch_text},
            {"artifact_name": "tests", "file_name": "tests.md", "mime_type": "text/markdown", "text": self._tests_markdown(request, plan, likely_sources)},
            {"artifact_name": "repair_manifest", "file_name": "repair_manifest.json", "mime_type": "application/json", "payload": repair_manifest},
        ]
        return self._result(request, plan, workspace_summary, answer=answer, files=files, validation={"likely_source_files": len(likely_sources)})

    def _solve_security_audit(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any]) -> HarnessResult:
        reasoned = self._call_reasoner(request, plan, workspace_summary)
        names = _workspace_file_names(workspace_summary, limit=80)
        security_inputs = [name for name in names if any(token in name.lower() for token in ("pcap", "rule", "cve", "pom.xml", "requirements", "package", "lock", "syz", "fuzz", "suricata"))]
        findings = {
            "task_id": request.task_id,
            "family": plan.family,
            "security_inputs": security_inputs[:40],
            "safe_analysis_scope": [
                "dependency or rule audit",
                "defensive remediation notes",
                "no exploit execution from harness.py",
            ],
            "recommended_outputs": list(plan.expected_outputs),
        }
        answer = reasoned or self._family_header(request, plan)
        files = [
            {"artifact_name": "security_report", "file_name": "security_report.md", "mime_type": "text/markdown", "text": self._notes_markdown(request, plan, workspace_summary, title="Security audit report")},
            {"artifact_name": "findings", "file_name": "findings.json", "mime_type": "application/json", "payload": findings},
            {"artifact_name": "repro_or_rule", "file_name": "repro_or_rule.txt", "mime_type": "text/plain", "text": "Defensive validation should be performed inside the official SkillsBench task environment.\n"},
        ]
        return self._result(request, plan, workspace_summary, answer=answer, files=files, validation={"security_inputs": len(security_inputs)})

    def _solve_office_document(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any]) -> HarnessResult:
        reasoned = self._call_reasoner(request, plan, workspace_summary)
        names = _workspace_file_names(workspace_summary, limit=80)
        doc_files = [name for name in names if PathLikeSuffix.matches(name, {".pdf", ".docx", ".pptx", ".tex", ".bib", ".md", ".txt"})]
        edits = {
            "task_id": request.task_id,
            "family": plan.family,
            "document_files": doc_files[:40],
            "expected_operations": self._office_operations(request, names),
            "recommended_outputs": list(plan.expected_outputs),
        }
        answer = reasoned or self._family_header(request, plan)
        files = [
            {"artifact_name": "document_result", "file_name": "document_result.md", "mime_type": "text/markdown", "text": self._notes_markdown(request, plan, workspace_summary, title="Document result")},
            {"artifact_name": "fields_or_edits", "file_name": "fields_or_edits.json", "mime_type": "application/json", "payload": edits},
            {"artifact_name": "validation_notes", "file_name": "validation_notes.md", "mime_type": "text/markdown", "text": self._validation_notes(request, plan, workspace_summary)},
        ]
        return self._result(request, plan, workspace_summary, answer=answer, files=files, validation={"document_files": len(doc_files)})

    def _solve_spreadsheet_finance(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any]) -> HarnessResult:
        reasoned = self._call_reasoner(request, plan, workspace_summary)
        excerpt = _first_text_excerpt(workspace_summary, max_chars=12000)
        csv_profile = _parse_csv_excerpt(excerpt)
        rows = [
            ["field", "value"],
            ["task_id", request.task_id],
            ["family", plan.family],
            ["file_count", str(workspace_summary.get("file_count") or 0)],
            ["csv_profile_ok", str(csv_profile.get("ok"))],
        ]
        csv_text = "\n".join(",".join(str(col).replace(",", " ") for col in row) for row in rows) + "\n"
        manifest = {
            "task_id": request.task_id,
            "family": plan.family,
            "csv_profile": csv_profile,
            "workspace_suffix_counts": _suffix_counts(workspace_summary),
            "recommended_outputs": list(plan.expected_outputs),
        }
        answer = reasoned or self._family_header(request, plan)
        files = [
            {"artifact_name": "analysis", "file_name": "analysis.csv", "mime_type": "text/csv", "text": csv_text},
            {"artifact_name": "workbook_result", "file_name": "workbook_result.json", "mime_type": "application/json", "payload": manifest},
            {"artifact_name": "workbook_notes", "file_name": "workbook_notes.md", "mime_type": "text/markdown", "text": self._notes_markdown(request, plan, workspace_summary, title="Workbook notes")},
        ]
        return self._result(request, plan, workspace_summary, answer=answer, files=files, validation={"csv_profile": csv_profile})

    def _solve_scientific_compute(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any]) -> HarnessResult:
        reasoned = self._call_reasoner(request, plan, workspace_summary)
        solution = {
            "task_id": request.task_id,
            "family": plan.family,
            "category": request.category,
            "units_required": any(token in " ".join(request.tags).lower() for token in ("unit", "physics", "geophysics", "hydrology")),
            "workspace_suffix_counts": _suffix_counts(workspace_summary),
            "recommended_method": [
                "load bounded input data",
                "derive domain variables and units",
                "compute deterministic result",
                "emit validation checks",
            ],
        }
        answer = reasoned or self._family_header(request, plan)
        files = [
            {"artifact_name": "solution", "file_name": "solution.json", "mime_type": "application/json", "payload": solution},
            {"artifact_name": "analysis", "file_name": "analysis.md", "mime_type": "text/markdown", "text": self._notes_markdown(request, plan, workspace_summary, title="Scientific analysis")},
            {"artifact_name": "validation", "file_name": "validation.csv", "mime_type": "text/csv", "text": "check,value\nworkspace_files,{}\n".format(workspace_summary.get("file_count") or 0)},
        ]
        return self._result(request, plan, workspace_summary, answer=answer, files=files, validation={"scientific_compute": True})

    def _solve_industrial_control(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any]) -> HarnessResult:
        reasoned = self._call_reasoner(request, plan, workspace_summary)
        names = _workspace_file_names(workspace_summary, limit=80)
        model_inputs = [name for name in names if any(token in name.lower() for token in ("stl", "dxf", "csv", "json", "py", "model", "sim", "control", "energy"))]
        solution = {
            "task_id": request.task_id,
            "family": plan.family,
            "model_inputs": model_inputs[:40],
            "control_or_geometry_tags": list(request.tags),
            "recommended_outputs": list(plan.expected_outputs),
        }
        parameters_csv = "parameter,value\ninput_files,{}\nfamily,{}\n".format(len(model_inputs), plan.family)
        answer = reasoned or self._family_header(request, plan)
        files = [
            {"artifact_name": "solution", "file_name": "solution.json", "mime_type": "application/json", "payload": solution},
            {"artifact_name": "simulation_notes", "file_name": "simulation_notes.md", "mime_type": "text/markdown", "text": self._notes_markdown(request, plan, workspace_summary, title="Simulation / control notes")},
            {"artifact_name": "parameters", "file_name": "parameters.csv", "mime_type": "text/csv", "text": parameters_csv},
        ]
        return self._result(request, plan, workspace_summary, answer=answer, files=files, validation={"model_inputs": len(model_inputs)})

    def _solve_media_processing(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any]) -> HarnessResult:
        reasoned = self._call_reasoner(request, plan, workspace_summary)
        names = _workspace_file_names(workspace_summary, limit=80)
        assets = [name for name in names if any(token in name.lower() for token in (".mp4", ".mov", ".wav", ".mp3", ".png", ".jpg", ".jpeg", ".json", ".obj", ".stl", "three"))]
        media_manifest = {
            "task_id": request.task_id,
            "family": plan.family,
            "assets": assets[:60],
            "transform_goal": self._media_goal(request),
            "recommended_outputs": list(plan.expected_outputs),
        }
        answer = reasoned or self._family_header(request, plan)
        files = [
            {"artifact_name": "media_manifest", "file_name": "media_manifest.json", "mime_type": "application/json", "payload": media_manifest},
            {"artifact_name": "asset_notes", "file_name": "asset_notes.md", "mime_type": "text/markdown", "text": self._notes_markdown(request, plan, workspace_summary, title="Media / asset notes")},
            {"artifact_name": "output_plan", "file_name": "output_plan.json", "mime_type": "application/json", "payload": {"outputs": list(plan.expected_outputs), "assets_seen": len(assets)}},
        ]
        return self._result(request, plan, workspace_summary, answer=answer, files=files, validation={"assets": len(assets)})

    def _solve_formal_reasoning(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any]) -> HarnessResult:
        reasoned = self._call_reasoner(request, plan, workspace_summary)
        lean_scaffold = (
            "-- AegisForge SkillsBench Lean/proof scaffold\n"
            "-- Replace `sorry` with the task-specific proof term when the full statement is available.\n"
            "theorem aegisforge_placeholder : True := by\n"
            "  trivial\n"
        )
        solution = {
            "task_id": request.task_id,
            "family": plan.family,
            "constraint_tags": list(request.tags),
            "method": [
                "formalize statement/constraints",
                "solve or prove under task environment",
                "emit machine-checkable artifact where required",
            ],
        }
        answer = reasoned or self._family_header(request, plan)
        files = [
            {"artifact_name": "solution_lean", "file_name": "solution.lean", "mime_type": "text/plain", "text": lean_scaffold},
            {"artifact_name": "proof_notes", "file_name": "proof_notes.md", "mime_type": "text/markdown", "text": self._notes_markdown(request, plan, workspace_summary, title="Formal reasoning notes")},
            {"artifact_name": "solution", "file_name": "solution.json", "mime_type": "application/json", "payload": solution},
        ]
        return self._result(request, plan, workspace_summary, answer=answer, files=files, validation={"formal_reasoning": True})

    def _validation_notes(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any]) -> str:
        names = _workspace_file_names(workspace_summary, limit=30)
        return (
            "# Validation notes\n\n"
            f"- task_id: `{request.task_id or 'unknown'}`\n"
            f"- family: `{plan.family}`\n"
            f"- expected outputs: `{', '.join(plan.expected_outputs)}`\n"
            f"- workspace files sampled: `{len(names)}`\n\n"
            "The official SkillsBench worker should validate final outputs inside the task environment. "
            "This harness emits deterministic manifests and output candidates without executing shell commands itself.\n"
        )

    def _tests_markdown(self, request: SkillsBenchRequest, plan: HarnessPlan, likely_sources: Sequence[str]) -> str:
        return (
            "# Tests / validation plan\n\n"
            f"- task_id: `{request.task_id or 'unknown'}`\n"
            f"- family: `{plan.family}`\n"
            f"- likely source/config files sampled: `{len(likely_sources)}`\n\n"
            "Suggested worker-side validation:\n"
            "1. install task dependencies using the official environment;\n"
            "2. run the task-provided tests/build command;\n"
            "3. verify the patched files are minimal and deterministic;\n"
            "4. compare produced outputs against the task checker.\n"
        )

    def _notes_markdown(self, request: SkillsBenchRequest, plan: HarnessPlan, workspace_summary: Mapping[str, Any], *, title: str) -> str:
        names = _workspace_file_names(workspace_summary, limit=40)
        suffixes = _suffix_counts(workspace_summary)
        return (
            f"# {title}\n\n"
            f"- harness: `{HARNESS_VERSION}`\n"
            f"- task_id: `{request.task_id or 'unknown'}`\n"
            f"- category: `{request.category or 'unknown'}`\n"
            f"- family: `{plan.family}`\n"
            f"- difficulty: `{request.difficulty or 'unknown'}`\n"
            f"- tags: `{', '.join(request.tags)}`\n\n"
            "## Workspace summary\n\n"
            f"- file_count: `{workspace_summary.get('file_count')}`\n"
            f"- suffix_counts: `{json.dumps(suffixes, sort_keys=True)}`\n\n"
            "## Sample files\n\n"
            + "\n".join(f"- `{name}`" for name in names[:40])
            + "\n\n## Strategy\n\n"
            + "\n".join(f"- **{action.name}**: {action.purpose}" for action in plan.actions)
            + "\n"
        )

    @staticmethod
    def _office_operations(request: SkillsBenchRequest, names: Sequence[str]) -> list[str]:
        blob = " ".join([request.task_id, request.prompt, *request.tags, *names]).lower()
        ops: list[str] = []
        if "form" in blob:
            ops.append("form_field_extraction_or_completion")
        if "redact" in blob or "anonym" in blob:
            ops.append("redaction_or_anonymization")
        if "citation" in blob or "bibtex" in blob:
            ops.append("citation_verification")
        if "ppt" in blob or "slide" in blob:
            ops.append("presentation_formatting")
        if "pdf" in blob:
            ops.append("pdf_document_processing")
        return ops or ["document_transformation"]

    @staticmethod
    def _media_goal(request: SkillsBenchRequest) -> str:
        blob = " ".join([request.task_id, request.prompt, *request.tags]).lower()
        if "silence" in blob:
            return "remove or index silence segments"
        if "filler" in blob:
            return "detect/remove filler words"
        if "dubbing" in blob or "tts" in blob:
            return "speech synthesis or dubbing alignment"
        if "threejs" in blob or "obj" in blob:
            return "3D scene/asset conversion"
        if "ocr" in blob:
            return "image OCR/statistics"
        return "media asset transformation"


class PathLikeSuffix:
    @staticmethod
    def matches(name: str, suffixes: set[str]) -> bool:
        lowered = name.lower()
        if ".pom" in suffixes and lowered.endswith("pom.xml"):
            return True
        return any(lowered.endswith(suffix.lower()) for suffix in suffixes)


def handle_skillsbench_request(
    *,
    message: Any = None,
    metadata: Mapping[str, Any] | None = None,
    text: str = "",
    reasoner: ReasonerCallback | None = None,
) -> dict[str, Any]:
    """Convenience function for agent.py/executor.py integration."""

    return SkillsBenchHarness(reasoner=reasoner).handle(message=message, metadata=metadata, text=text)


def validate_harness_selftest() -> dict[str, Any]:
    request = normalize_skillsbench_request(
        message={
            "metadata": {"task_id": "dialogue-parser", "task_set": "standard-v1"},
            "text": "Solve dialogue-parser. Return a JSON parser output.",
        }
    )
    harness = SkillsBenchHarness(include_text_context=False)
    emission = harness.handle_to_emission(request=request)
    errors: list[str] = []
    if harness.last_plan is None:
        errors.append("no plan created")
    elif harness.last_plan.family != "data_json":
        errors.append(f"unexpected family: {harness.last_plan.family}")
    if not emission.artifact_records:
        errors.append("no artifact records emitted")
    if harness.last_workspace_execution is None:
        errors.append("workspace executor did not run")
    try:
        json.loads(emission.final_text())
    except Exception as exc:
        errors.append(f"emission final_text invalid JSON: {exc}")
    return {
        "ok": not errors,
        "errors": errors,
        "harness_version": HARNESS_VERSION,
        "emission_file_count": len(emission.files),
        "artifact_count": len(emission.artifact_records),
        "workspace_execution_status": getattr(harness.last_workspace_execution, "status", ""),
        "workspace_visible": bool(getattr(harness.last_workspace_execution, "workspace_visible", False)) if harness.last_workspace_execution else False,
        "task_id": request.task_id,
        "family": harness.last_plan.family if harness.last_plan else "",
    }


__all__ = [
    "HARNESS_VERSION",
    "HarnessAction",
    "HarnessPlan",
    "HarnessResult",
    "PathLikeSuffix",
    "SkillsBenchHarness",
    "handle_skillsbench_request",
    "validate_harness_selftest",
]
