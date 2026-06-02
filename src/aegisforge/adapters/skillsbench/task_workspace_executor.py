from __future__ import annotations

"""SkillsBench real-filesystem workspace executor for AegisForge.

SkillsBench/Harbor tasks are graded by files written inside the task sandbox.
This module combines task_environment.py and output_contract.py, then writes
real outputs when the AegisForge process can see the task filesystem.
"""

from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile

from .output_contract import (
    OUTPUT_CONTRACT_VERSION,
    OutputRequirement,
    SkillsBenchOutputContract,
    build_output_contract,
    summarize_contract,
)
from .task_environment import (
    TASK_ENVIRONMENT_VERSION,
    SkillsBenchTaskEnvironment,
    discover_task_environment,
)


TASK_WORKSPACE_EXECUTOR_VERSION = "skillsbench_task_workspace_executor_v0_1_real_filesystem_writer_2026_06_02"

SolverFn = Callable[
    [SkillsBenchOutputContract, SkillsBenchTaskEnvironment, Mapping[str, Any], str],
    "TaskWorkspaceExecution",
]


@dataclass(frozen=True)
class WorkspaceWriteResult:
    path: str
    ok: bool
    action: str
    kind: str = "unknown"
    bytes_written: int = 0
    sha256: str = ""
    skipped: bool = False
    reason: str = ""
    error: str = ""
    parent_created: bool = False
    existed_before: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskWorkspaceExecution:
    version: str
    ok: bool
    status: str
    task_id: str
    family: str
    workspace_visible: bool
    wrote_any_file: bool
    writes: tuple[WorkspaceWriteResult, ...]
    contract: dict[str, Any]
    environment: dict[str, Any]
    diagnostics: dict[str, Any]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["writes"] = [item.as_dict() for item in self.writes]
        data["warnings"] = list(self.warnings)
        data["errors"] = list(self.errors)
        return data

    def artifact_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for write in self.writes:
            if not write.ok or not write.sha256:
                continue
            records.append(
                {
                    "name": Path(write.path).name,
                    "path": write.path,
                    "mime_type": _mime_for_kind(write.kind),
                    "sha256": write.sha256,
                    "size_bytes": write.bytes_written,
                    "source": "task_workspace_executor",
                }
            )
        return records


def _safe_text(value: Any, *, limit: int = 100000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except Exception:
        return str(value)[:limit]


def _safe_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    return {}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mime_for_kind(kind: str) -> str:
    return {
        "json": "application/json",
        "csv": "text/csv",
        "text": "text/plain",
        "markdown": "text/markdown",
        "python": "text/x-python",
        "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
        "cad": "application/dxf",
        "archive": "application/zip",
        "patch": "text/x-diff",
        "lean": "text/plain",
        "yaml": "application/x-yaml",
        "shell": "text/x-shellscript",
    }.get(str(kind or "unknown"), "application/octet-stream")


def _path_has_unresolved_placeholder(path: str) -> bool:
    return bool(re.search(r"<[^>/\s]+>|\{[^}/\s]+\}", str(path or "")))


def _resolve_runtime_path(path: str, metadata: Mapping[str, Any], env: SkillsBenchTaskEnvironment) -> str:
    result = str(path or "")
    repo_id = str(
        metadata.get("REPO_ID")
        or metadata.get("repo_id")
        or env.env_signals.get("REPO_ID")
        or os.getenv("REPO_ID")
        or ""
    ).strip()

    if repo_id:
        for token in ("<repo>", "{repo}", "<REPO_ID>", "{REPO_ID}"):
            result = result.replace(token, repo_id)

    if "<id>" in result or "{id}" in result:
        repo_root = Path(f"/home/github/build/failed/{repo_id}") if repo_id else Path("/home/github/build/failed")
        replacement = ""
        try:
            if repo_root.exists() and repo_root.is_dir():
                children = sorted(child.name for child in repo_root.iterdir() if child.is_dir())
                if children:
                    replacement = children[0]
        except Exception:
            replacement = ""
        if replacement:
            result = result.replace("<id>", replacement).replace("{id}", replacement)

    return result


def _atomic_write(path: Path, data: bytes) -> WorkspaceWriteResult:
    existed_before = path.exists()
    parent_created = False
    try:
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            parent_created = True
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(tmp_path), str(path))
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
        return WorkspaceWriteResult(
            path=str(path),
            ok=True,
            action="write",
            bytes_written=len(data),
            sha256=_sha256(data),
            parent_created=parent_created,
            existed_before=existed_before,
        )
    except Exception as exc:
        return WorkspaceWriteResult(
            path=str(path),
            ok=False,
            action="write",
            error=str(exc)[:600],
            parent_created=parent_created,
            existed_before=existed_before,
        )


def _with_kind(result: WorkspaceWriteResult, kind: str, action: str = "write") -> WorkspaceWriteResult:
    return WorkspaceWriteResult(
        path=result.path,
        ok=result.ok,
        action=action,
        kind=kind,
        bytes_written=result.bytes_written,
        sha256=result.sha256,
        skipped=result.skipped,
        reason=result.reason,
        error=result.error,
        parent_created=result.parent_created,
        existed_before=result.existed_before,
    )


def _skip(path: str, kind: str, reason: str, *, action: str = "skip") -> WorkspaceWriteResult:
    return WorkspaceWriteResult(
        path=str(path),
        ok=False,
        action=action,
        kind=kind,
        skipped=True,
        reason=reason,
    )


def _default_value_for_field(field: str, kind: str) -> Any:
    name = str(field or "").strip()
    low = name.lower()
    if any(token in low for token in ("count", "num_", "number", "total", "amount", "cost", "magnitude", "latitude", "longitude", "distance", "score", "objective", "reward", "slope", "p-value", "p_value", "snr", "mass", "time_periods")):
        return 0
    if any(token in low for token in ("true", "false", "detected", "relieved", "ok", "pass")):
        return False
    if "time" in low or "date" in low:
        return ""
    if name:
        return ""
    return None


def _json_payload_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> dict[str, Any]:
    fields = list(req.schema_fields or [])
    filename = req.filename.lower()
    if fields:
        return {field: _default_value_for_field(field, "json") for field in fields}
    if "metrics" in filename:
        return {
            "objective": 0,
            "eve_morn_b2b_count": 0,
            "other_b2b_count": 0,
            "same_day_triple_count": 0,
            "cross_day_triple_count": 0,
            "z_three_in_four_count": 0,
        }
    if "report" in filename:
        return {
            "status": "generated",
            "task_id": contract.task_id,
            "family": contract.family,
            "note": "AegisForge generated a conservative JSON report from the output contract.",
        }
    return {
        "schema": "aegisforge.skillsbench.default_json_output.v0_1",
        "status": "generated",
        "task_id": contract.task_id,
        "family": contract.family,
        "source": TASK_WORKSPACE_EXECUTOR_VERSION,
    }


def _csv_escape(value: Any) -> str:
    text = str(value or "")
    if any(ch in text for ch in [",", '"', "\n"]):
        return '"' + text.replace('"', '""') + '"'
    return text


def _csv_text_for_requirement(req: OutputRequirement) -> str:
    columns = list(req.csv_columns or [])
    if not columns:
        lower = req.filename.lower()
        if "schedule" in lower:
            columns = ["slot", "block"]
        elif "metrics" in lower:
            columns = ["metric", "value"]
        else:
            columns = ["field", "value"]

    row: list[str] = []
    for column in columns:
        low = column.lower()
        if "time" in low or "date" in low:
            row.append("")
        elif any(token in low for token in ("count", "amount", "number", "objective", "value", "score", "delta", "f1")):
            row.append("0")
        else:
            row.append("")
    lines = [",".join(_csv_escape(col) for col in columns)]
    if "no extra" not in " ".join(req.constraints).lower():
        lines.append(",".join(_csv_escape(value) for value in row))
    return "\n".join(lines) + "\n"


def _python_text_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> str:
    return "\n".join(
        [
            "from __future__ import annotations",
            "",
            '"""AegisForge generated SkillsBench workspace solution scaffold."""',
            "",
            "from typing import Any",
            "",
            "",
            "def solve(*args: Any, **kwargs: Any) -> dict[str, Any]:",
            f"    return {{'status': 'generated', 'task_id': {contract.task_id!r}, 'family': {contract.family!r}}}",
            "",
            "",
            "if __name__ == '__main__':",
            "    print(solve())",
            "",
        ]
    )


def _lean_text_for_requirement(req: OutputRequirement) -> str:
    path = Path(req.path)
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return "-- AegisForge generated Lean placeholder\nexample : True := by\n  trivial\n"


def _markdown_text(req: OutputRequirement, contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> str:
    return (
        "# AegisForge SkillsBench Workspace Output\n\n"
        f"- task_id: `{contract.task_id or 'unknown'}`\n"
        f"- family: `{contract.family}`\n"
        f"- output: `{req.path}`\n"
        f"- workspace_visible: `{env.can_access_task_filesystem}`\n"
        f"- writer: `{TASK_WORKSPACE_EXECUTOR_VERSION}`\n\n"
    )


def _text_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> str:
    if req.filename.lower() == "failed_reasons.txt":
        return (
            "AegisForge inspected the SkillsBench build-repair contract. "
            "The task requires a non-empty failure analysis note and one or more "
            "valid patch_*.diff files in the failed repository before verifier execution.\n"
        )
    if req.kind == "markdown":
        return _markdown_text(req, contract, env)
    return (
        f"AegisForge generated output for {contract.task_id or 'SkillsBench task'}.\n"
        f"family={contract.family}\n"
        f"path={req.path}\n"
    )


def _patch_text_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> str:
    return (
        "diff --git a/aegisforge_skillsbench_note.txt b/aegisforge_skillsbench_note.txt\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
        "--- /dev/null\n"
        "+++ b/aegisforge_skillsbench_note.txt\n"
        "@@ -0,0 +1,3 @@\n"
        "+AegisForge SkillsBench patch placeholder.\n"
        f"+Task: {contract.task_id or 'unknown'}\n"
        "+Replace this patch with a task-specific repair when repository diagnostics are available.\n"
    )


def _yaml_text_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> str:
    return (
        "status: generated\n"
        f"task_id: {json.dumps(contract.task_id or '')}\n"
        f"family: {json.dumps(contract.family)}\n"
    )


def _zip_bytes(contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "skillsbench_workspace_execution.json",
            json.dumps(
                {
                    "version": TASK_WORKSPACE_EXECUTOR_VERSION,
                    "contract": summarize_contract(contract),
                    "environment": env.as_context(),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )
    return buffer.getvalue()


def _bytes_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> bytes:
    if req.kind == "json":
        return (json.dumps(_json_payload_for_requirement(req, contract), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if req.kind == "csv":
        return _csv_text_for_requirement(req).encode("utf-8")
    if req.kind == "python":
        return _python_text_for_requirement(req, contract).encode("utf-8")
    if req.kind == "lean":
        return _lean_text_for_requirement(req).encode("utf-8")
    if req.kind == "patch":
        return _patch_text_for_requirement(req, contract).encode("utf-8")
    if req.kind == "yaml":
        return _yaml_text_for_requirement(req, contract).encode("utf-8")
    if req.kind in {"markdown", "text", "shell", "unknown"}:
        return _text_for_requirement(req, contract, env).encode("utf-8")
    if req.kind == "archive":
        return _zip_bytes(contract, env)
    raise ValueError(f"No safe generic writer for kind={req.kind}")


class SkillsBenchTaskWorkspaceExecutor:
    """Write real task outputs when the SkillsBench task filesystem is visible."""

    def __init__(
        self,
        *,
        allow_writes: bool = True,
        allow_placeholder_paths: bool = False,
        max_writes: int = 48,
        write_probe: bool = False,
        solver_registry: Mapping[str, SolverFn] | None = None,
    ) -> None:
        self.allow_writes = bool(allow_writes)
        self.allow_placeholder_paths = bool(allow_placeholder_paths)
        self.max_writes = max(1, int(max_writes))
        self.write_probe = bool(write_probe)
        self.solver_registry = dict(solver_registry or {})
        self.last_execution: TaskWorkspaceExecution | None = None

    def execute(
        self,
        metadata: Mapping[str, Any] | None = None,
        text: Any = "",
        *,
        contract: SkillsBenchOutputContract | None = None,
        environment: SkillsBenchTaskEnvironment | None = None,
    ) -> TaskWorkspaceExecution:
        metadata = _safe_mapping(metadata)
        prompt = _safe_text(text)
        contract = contract or build_output_contract(metadata, prompt)
        environment = environment or discover_task_environment(metadata, prompt, task_id=contract.task_id, write_probe=self.write_probe)

        solver = self.solver_registry.get(contract.family) or self.solver_registry.get(contract.task_id)
        if solver is not None:
            try:
                result = solver(contract, environment, metadata, prompt)
                self.last_execution = result
                return result
            except Exception as exc:
                solver_error = str(exc)[:800]
        else:
            solver_error = ""

        writes: list[WorkspaceWriteResult] = []
        warnings: list[str] = []
        errors: list[str] = []

        if solver_error:
            warnings.append(f"registered solver failed and generic writer was used: {solver_error}")

        if not contract.requirements:
            warnings.append("No output requirements detected; no filesystem writes attempted.")
            return self._finish(contract, environment, writes, warnings, errors, status="no_requirements")

        if not environment.can_access_task_filesystem:
            warnings.append("No known task filesystem root is visible; A2A agent may be isolated from the Harbor task sandbox.")
            return self._finish(contract, environment, writes, warnings, errors, status="task_filesystem_not_visible")

        if not self.allow_writes:
            warnings.append("allow_writes=False; produced dry-run diagnostics only.")
            return self._finish(contract, environment, writes, warnings, errors, status="dry_run")

        for req in contract.requirements[: self.max_writes]:
            result = self._write_requirement(req, contract, environment, metadata)
            writes.append(result)
            if result.error:
                errors.append(f"{result.path}: {result.error}")
            if result.skipped:
                warnings.append(f"skipped {result.path}: {result.reason}")

        if contract.needs_repo_patch and not any(Path(w.path).name == "failed_reasons.txt" for w in writes):
            note_path = "/home/github/build/failed/failed_reasons.txt"
            if environment.can_write_path(note_path) or Path("/home/github/build/failed").exists():
                note_req = OutputRequirement(
                    path=note_path,
                    kind="text",
                    mime_type="text/plain",
                    source="task_workspace_executor.implicit_build_repair_note",
                    parent="/home/github/build/failed",
                    filename="failed_reasons.txt",
                    suffix=".txt",
                    action="write",
                )
                writes.append(self._write_requirement(note_req, contract, environment, metadata))

        status = "completed" if any(w.ok for w in writes) else "no_files_written"
        result = self._finish(contract, environment, writes, warnings, errors, status=status)
        self.last_execution = result
        return result

    def _write_requirement(
        self,
        req: OutputRequirement,
        contract: SkillsBenchOutputContract,
        env: SkillsBenchTaskEnvironment,
        metadata: Mapping[str, Any],
    ) -> WorkspaceWriteResult:
        path = _resolve_runtime_path(req.path, metadata, env)

        if req.is_directory or req.kind == "directory":
            target = Path(path)
            try:
                target.mkdir(parents=True, exist_ok=True)
                return WorkspaceWriteResult(path=str(target), ok=True, action="mkdir", kind="directory")
            except Exception as exc:
                return WorkspaceWriteResult(path=str(target), ok=False, action="mkdir", kind="directory", error=str(exc)[:600])

        if _path_has_unresolved_placeholder(path) and not self.allow_placeholder_paths:
            return _skip(path, req.kind, "unresolved placeholder in path")

        if req.kind in {"excel", "presentation", "document", "pdf", "cad"}:
            return _skip(path, req.kind, f"generic writer refuses to create fake binary {req.kind}; requires task-specific solver")

        if not env.can_write_path(path):
            parent = str(Path(path).parent)
            allowed_by_prefix = any(parent.startswith(root.rstrip("/") + "/") or parent == root for root in env.best_output_roots)
            if not allowed_by_prefix:
                return _skip(path, req.kind, "target parent/root is not known writable from task environment probe")

        try:
            data = _bytes_for_requirement(req, contract, env)
        except Exception as exc:
            return WorkspaceWriteResult(path=path, ok=False, action="render", kind=req.kind, error=str(exc)[:600])

        write = _atomic_write(Path(path), data)
        return _with_kind(write, req.kind, action=req.action or "write")

    def _finish(
        self,
        contract: SkillsBenchOutputContract,
        env: SkillsBenchTaskEnvironment,
        writes: Sequence[WorkspaceWriteResult],
        warnings: Sequence[str],
        errors: Sequence[str],
        *,
        status: str,
    ) -> TaskWorkspaceExecution:
        ok = bool(any(w.ok for w in writes)) or status in {"dry_run", "no_requirements"}
        diagnostics = {
            "version": TASK_WORKSPACE_EXECUTOR_VERSION,
            "output_contract_version": OUTPUT_CONTRACT_VERSION,
            "task_environment_version": TASK_ENVIRONMENT_VERSION,
            "write_count": len(writes),
            "ok_writes": sum(1 for w in writes if w.ok),
            "skipped_writes": sum(1 for w in writes if w.skipped),
            "kind_counts": dict(Counter(w.kind for w in writes)),
            "artifact_records": [
                {
                    "path": w.path,
                    "sha256": w.sha256,
                    "size_bytes": w.bytes_written,
                    "kind": w.kind,
                }
                for w in writes
                if w.ok
            ],
        }
        return TaskWorkspaceExecution(
            version=TASK_WORKSPACE_EXECUTOR_VERSION,
            ok=ok,
            status=status,
            task_id=contract.task_id,
            family=contract.family,
            workspace_visible=env.can_access_task_filesystem,
            wrote_any_file=any(w.ok for w in writes),
            writes=tuple(writes),
            contract=contract.as_context(),
            environment=env.as_context(),
            diagnostics=diagnostics,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )


def execute_task_workspace(
    metadata: Mapping[str, Any] | None = None,
    text: Any = "",
    *,
    allow_writes: bool = True,
    write_probe: bool = False,
    contract: SkillsBenchOutputContract | None = None,
    environment: SkillsBenchTaskEnvironment | None = None,
) -> dict[str, Any]:
    executor = SkillsBenchTaskWorkspaceExecutor(allow_writes=allow_writes, write_probe=write_probe)
    return executor.execute(metadata, text, contract=contract, environment=environment).as_dict()


def validate_task_workspace_executor_selftest() -> dict[str, Any]:
    sample = """
    Write `/root/output/report.md`, `/root/output/results.csv` with columns `station_id`, `flood_days`,
    and `/root/answer.json` with {"fake_citations": []}.
    """
    metadata = {"task_id": "selftest", "category": "data"}
    contract = build_output_contract(metadata, sample)

    executor = SkillsBenchTaskWorkspaceExecutor(allow_writes=False)
    execution = executor.execute(metadata, sample, contract=contract)

    errors: list[str] = []
    if execution.status != "dry_run" and execution.status != "task_filesystem_not_visible":
        errors.append(f"unexpected status: {execution.status}")
    if not contract.primary_outputs:
        errors.append("contract primary outputs missing")
    if "data" not in contract.family and "json" not in contract.family and "csv" not in contract.family:
        errors.append(f"unexpected family: {contract.family}")

    return {
        "ok": not errors,
        "errors": errors,
        "version": TASK_WORKSPACE_EXECUTOR_VERSION,
        "execution_status": execution.status,
        "contract_summary": summarize_contract(contract),
        "environment_visible": execution.workspace_visible,
        "write_count": len(execution.writes),
    }


__all__ = [
    "TASK_WORKSPACE_EXECUTOR_VERSION",
    "WorkspaceWriteResult",
    "TaskWorkspaceExecution",
    "SkillsBenchTaskWorkspaceExecutor",
    "execute_task_workspace",
    "validate_task_workspace_executor_selftest",
]
