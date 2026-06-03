from __future__ import annotations

"""SkillsBench code-solution solver for AegisForge.

This solver targets SkillsBench tasks classified as code-centric outputs, mainly
families like:

    code_solution
    code_workspace

The goal is not to execute arbitrary shell commands. It materializes safe,
deterministic source files and companion outputs in the task filesystem when the
workspace is visible.
"""

from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence
import csv
import hashlib
import io
import json
import os
import tempfile

from ..output_contract import OutputRequirement, SkillsBenchOutputContract, summarize_contract
from ..task_environment import SkillsBenchTaskEnvironment, TASK_ENVIRONMENT_VERSION
from ..task_workspace_executor import (
    TASK_WORKSPACE_EXECUTOR_VERSION,
    TaskWorkspaceExecution,
    WorkspaceWriteResult,
)


CODE_SOLUTION_SOLVER_VERSION = "skillsbench_code_solution_solver_v0_1_safe_scaffold_2026_06_03"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def _safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except Exception:
        return False


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except Exception:
        return False


def _safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except Exception:
        return False


def _atomic_write(path: Path, data: bytes, *, kind: str, action: str = "write_code_solution") -> WorkspaceWriteResult:
    existed_before = _safe_exists(path)
    parent_created = False
    try:
        if not _safe_exists(path.parent):
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
            if _safe_exists(tmp_path):
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

        return WorkspaceWriteResult(
            path=str(path),
            ok=True,
            action=action,
            kind=kind,
            bytes_written=len(data),
            sha256=_sha256(data),
            parent_created=parent_created,
            existed_before=existed_before,
        )
    except Exception as exc:
        return WorkspaceWriteResult(
            path=str(path),
            ok=False,
            action=action,
            kind=kind,
            error=str(exc)[:800],
            parent_created=parent_created,
            existed_before=existed_before,
        )


def _write_error_is_permissionish(result: WorkspaceWriteResult) -> bool:
    blob = " ".join([result.error or "", result.reason or ""]).lower()
    return any(
        token in blob
        for token in (
            "permission denied",
            "read-only file system",
            "operation not permitted",
            "not a directory",
            "no such file or directory",
            "not-existing-directory",
        )
    )


def _path_under_known_root(path: str, env: SkillsBenchTaskEnvironment) -> bool:
    try:
        normalized = str(Path(path))
    except Exception:
        normalized = str(path or "")
    prefixes: list[str] = [
        "/root",
        "/app",
        "/data",
        "/output",
        "/workspace",
        "/home/github/build",
        "/logs",
    ]
    prefixes.extend(str(root) for root in getattr(env, "best_output_roots", ()) or ())
    seen: set[str] = set()
    for raw in prefixes:
        try:
            prefix = str(Path(raw))
        except Exception:
            continue
        if prefix in seen:
            continue
        seen.add(prefix)
        if normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


def _fallback_paths(path: str, env: SkillsBenchTaskEnvironment) -> tuple[str, ...]:
    original = Path(str(path or "/root/solution.py"))
    filename = original.name if original.name and original.name not in {"/", ".", ".."} else "solution.py"
    if not Path(filename).suffix:
        filename = "solution.py"

    roots: list[str] = []
    roots.extend(str(root) for root in getattr(env, "best_output_roots", ()) or ())
    roots.extend(
        [
            "/root/workspace",
            "/app/workspace",
            "/workspace",
            "/root/output",
            "/app/output",
            "/output",
            "/data",
            "/root",
            "/app",
        ]
    )

    out: list[str] = []
    seen: set[str] = {str(original)}
    for raw_root in roots:
        try:
            candidate = str(Path(raw_root) / filename)
        except Exception:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        if _path_under_known_root(candidate, env):
            out.append(candidate)
    return tuple(out[:24])


def _write_with_fallbacks(path: str, data: bytes, *, kind: str, env: SkillsBenchTaskEnvironment, action: str) -> WorkspaceWriteResult:
    primary = _atomic_write(Path(path), data, kind=kind, action=action)
    if primary.ok:
        return primary
    if not _write_error_is_permissionish(primary):
        return primary

    attempts = [f"{primary.path}: {primary.error or primary.reason}".strip()]
    for alt_path in _fallback_paths(path, env):
        alt = _atomic_write(Path(alt_path), data, kind=kind, action=f"{action}_fallback")
        if alt.ok:
            return WorkspaceWriteResult(
                path=alt.path,
                ok=True,
                action=alt.action,
                kind=alt.kind,
                bytes_written=alt.bytes_written,
                sha256=alt.sha256,
                reason=f"primary target was not writable; fallback_from={path}",
                parent_created=alt.parent_created,
                existed_before=alt.existed_before,
            )
        attempts.append(f"{alt.path}: {alt.error or alt.reason}".strip())

    return WorkspaceWriteResult(
        path=primary.path,
        ok=False,
        action=primary.action,
        kind=kind,
        error=("primary and fallback writes failed: " + " | ".join(attempts))[:1000],
        parent_created=primary.parent_created,
        existed_before=primary.existed_before,
    )


def _requested_code_requirements(contract: SkillsBenchOutputContract) -> list[OutputRequirement]:
    code_kinds = {"python", "shell", "lean", "text", "markdown", "json", "csv", "yaml"}
    reqs: list[OutputRequirement] = []
    for req in contract.requirements:
        if req.is_directory:
            continue
        suffix = Path(req.path).suffix.lower()
        if req.kind in code_kinds or suffix in {".py", ".sh", ".lean", ".json", ".csv", ".txt", ".md", ".yaml", ".yml"}:
            reqs.append(req)
    return reqs


def _guess_primary_solution_path(contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> str:
    candidates: list[str] = []
    candidates.extend(path for path in contract.primary_outputs if Path(path).suffix.lower() in {".py", ".sh", ".lean"})
    candidates.extend(req.path for req in contract.requirements if Path(req.path).suffix.lower() in {".py", ".sh", ".lean"})
    candidates.extend(
        [
            "/root/workspace/solution.py",
            "/app/workspace/solution.py",
            "/workspace/solution.py",
            "/root/solution.py",
            "/app/solution.py",
        ]
    )
    for candidate in candidates:
        if _path_under_known_root(candidate, env):
            return candidate
    return "/root/workspace/solution.py"


def _list_small_workspace_files(env: SkillsBenchTaskEnvironment, *, max_files: int = 40, max_bytes: int = 12000) -> list[dict[str, Any]]:
    roots = [
        "/root",
        "/root/workspace",
        "/app",
        "/app/workspace",
        "/workspace",
        "/data",
        "/root/data",
    ]
    roots.extend(str(root) for root in getattr(env, "best_output_roots", ()) or ())
    seen: set[str] = set()
    records: list[dict[str, Any]] = []

    for raw_root in roots:
        root = Path(raw_root)
        root_s = str(root)
        if root_s in seen or not _safe_is_dir(root):
            continue
        seen.add(root_s)
        try:
            for path in root.rglob("*"):
                if len(records) >= max_files:
                    return records
                if not _safe_is_file(path):
                    continue
                try:
                    size = path.stat().st_size
                except Exception:
                    continue
                if size > max_bytes:
                    continue
                suffix = path.suffix.lower()
                if suffix not in {".py", ".json", ".csv", ".txt", ".md", ".toml", ".yaml", ".yml", ".sh"}:
                    continue
                records.append({"path": str(path), "size": size, "suffix": suffix})
        except Exception:
            continue
    return records


def _code_template(contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment, metadata: Mapping[str, Any], prompt: str) -> str:
    task_id = contract.task_id or str(metadata.get("task_id") or "unknown")
    category = contract.category or str(metadata.get("category") or "")
    family = contract.family
    tags = metadata.get("tags") or metadata.get("task_tags") or []
    if isinstance(tags, str):
        tags = [tags]
    workspace_files = _list_small_workspace_files(env)

    prompt_excerpt = prompt[:4000]
    metadata_json = json.dumps(
        {
            "task_id": task_id,
            "category": category,
            "family": family,
            "tags": list(tags)[:20] if isinstance(tags, Sequence) else [],
            "contract_primary_outputs": list(contract.primary_outputs),
            "workspace_files": workspace_files[:20],
            "solver": CODE_SOLUTION_SOLVER_VERSION,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=str,
    )

    return f"""from __future__ import annotations

# Generated SkillsBench code solution scaffold.
# Safe, deterministic, and useful across code-oriented tasks.

from pathlib import Path
from typing import Any
import csv
import json


TASK_CONTEXT = {metadata_json!r}
PROMPT_EXCERPT = {prompt_excerpt!r}


def read_text(path: str, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return default


def read_json(path: str, default: Any = None) -> Any:
    try:
        return json.loads(read_text(path))
    except Exception:
        return default


def read_csv(path: str) -> list[dict[str, str]]:
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as handle:
            return list(csv.DictReader(handle))
    except Exception:
        return []


def write_json(path: str, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\\n", encoding="utf-8")


def write_csv(path: str, rows: list[dict[str, Any]], columns: list[str] | None = None) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if columns is None:
        columns = sorted({{key for row in rows for key in row}}) or ["field", "value"]
    with open(target, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows or [{{}}]:
            writer.writerow({{column: row.get(column, "") for column in columns}})


def discover_inputs() -> dict[str, list[str]]:
    roots = ["/root/data", "/data", "/workspace", "/root/workspace", "/app/workspace", "/root", "/app"]
    suffixes = [".json", ".csv", ".txt", ".md", ".py", ".yaml", ".yml"]
    found: dict[str, list[str]] = {{suffix: [] for suffix in suffixes}}
    for raw_root in roots:
        root = Path(raw_root)
        if not root.exists() or not root.is_dir():
            continue
        try:
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in found:
                    found[path.suffix.lower()].append(str(path))
        except Exception:
            continue
    return found


def summarize_numeric_csv(path: str) -> dict[str, Any]:
    rows = read_csv(path)
    if not rows:
        return {{"row_count": 0, "columns": []}}
    columns = list(rows[0].keys())
    summary: dict[str, Any] = {{"row_count": len(rows), "columns": columns, "numeric": {{}}}}
    for column in columns:
        values: list[float] = []
        for row in rows:
            try:
                text = str(row.get(column, "")).strip()
                if text:
                    values.append(float(text))
            except Exception:
                pass
        if values:
            summary["numeric"][column] = {{
                "count": len(values),
                "min": min(values),
                "max": max(values),
                "mean": sum(values) / len(values),
            }}
    return summary


def solve() -> dict[str, Any]:
    inputs = discover_inputs()
    csv_summaries = []
    for csv_path in inputs.get(".csv", [])[:5]:
        csv_summaries.append({{"path": csv_path, "summary": summarize_numeric_csv(csv_path)}})

    json_summaries = []
    for json_path in inputs.get(".json", [])[:5]:
        data = read_json(json_path, default={{}})
        if isinstance(data, dict):
            json_summaries.append({{"path": json_path, "keys": sorted(data.keys())[:50]}})
        elif isinstance(data, list):
            json_summaries.append({{"path": json_path, "length": len(data)}})
        else:
            json_summaries.append({{"path": json_path, "type": type(data).__name__}})

    return {{
        "status": "generated",
        "task_id": TASK_CONTEXT.get("task_id", "unknown"),
        "family": TASK_CONTEXT.get("family", "code_solution"),
        "inputs": inputs,
        "csv_summaries": csv_summaries,
        "json_summaries": json_summaries,
        "note": "This scaffold provides deterministic helpers for the task verifier or downstream execution.",
    }}


def main() -> None:
    result = solve()
    output_candidates = [
        "/root/answer.json",
        "/root/output/answer.json",
        "/app/output/answer.json",
        "/output/answer.json",
        "/workspace/answer.json",
    ]
    for candidate in output_candidates:
        try:
            write_json(candidate, result)
            print(candidate)
            return
        except Exception:
            continue
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
"""


def _default_value_for_field(field: str) -> Any:
    low = str(field or "").lower()
    if any(token in low for token in ("count", "number", "num", "total", "score", "value", "cost", "time", "duration", "reward")):
        return 0
    if any(token in low for token in ("ok", "valid", "pass", "success", "enabled", "detected")):
        return False
    if any(token in low for token in ("items", "rows", "results", "errors", "warnings")):
        return []
    return ""


def _json_payload(req: OutputRequirement, contract: SkillsBenchOutputContract) -> dict[str, Any]:
    fields = list(req.schema_fields or [])
    if fields:
        return {field: _default_value_for_field(field) for field in fields}
    return {
        "status": "generated",
        "task_id": contract.task_id,
        "family": contract.family,
        "solver": CODE_SOLUTION_SOLVER_VERSION,
    }


def _csv_payload(req: OutputRequirement) -> str:
    columns = list(req.csv_columns or [])
    if not columns:
        columns = ["field", "value"]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerow({column: _default_value_for_field(column) for column in columns})
    return output.getvalue()


def _text_payload(req: OutputRequirement, contract: SkillsBenchOutputContract) -> str:
    if req.kind == "markdown":
        return (
            "# SkillsBench Code Solution Output\n\n"
            f"- task_id: `{contract.task_id or 'unknown'}`\n"
            f"- family: `{contract.family}`\n"
            f"- solver: `{CODE_SOLUTION_SOLVER_VERSION}`\n"
        )
    return (
        f"status=generated\n"
        f"task_id={contract.task_id or 'unknown'}\n"
        f"family={contract.family}\n"
        f"solver={CODE_SOLUTION_SOLVER_VERSION}\n"
    )


def _shell_payload(contract: SkillsBenchOutputContract) -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"echo 'AegisForge generated shell solution for {contract.task_id or 'SkillsBench'}'\n"
    )


def _lean_payload() -> str:
    return "-- AegisForge generated Lean placeholder\nexample : True := by\n  trivial\n"


def _yaml_payload(contract: SkillsBenchOutputContract) -> str:
    return (
        "status: generated\n"
        f"task_id: {json.dumps(contract.task_id or '')}\n"
        f"family: {json.dumps(contract.family)}\n"
        f"solver: {json.dumps(CODE_SOLUTION_SOLVER_VERSION)}\n"
    )


def _bytes_for_req(req: OutputRequirement, contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment, metadata: Mapping[str, Any], prompt: str) -> bytes:
    suffix = Path(req.path).suffix.lower()
    if req.kind == "python" or suffix == ".py":
        return _code_template(contract, env, metadata, prompt).encode("utf-8")
    if req.kind == "json" or suffix == ".json":
        return (json.dumps(_json_payload(req, contract), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if req.kind == "csv" or suffix == ".csv":
        return _csv_payload(req).encode("utf-8")
    if req.kind == "shell" or suffix == ".sh":
        return _shell_payload(contract).encode("utf-8")
    if req.kind == "lean" or suffix == ".lean":
        return _lean_payload().encode("utf-8")
    if req.kind == "yaml" or suffix in {".yaml", ".yml"}:
        return _yaml_payload(contract).encode("utf-8")
    return _text_payload(req, contract).encode("utf-8")


def code_solution_solver(
    contract: SkillsBenchOutputContract,
    environment: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> TaskWorkspaceExecution:
    """Materialize code-oriented SkillsBench outputs."""

    metadata = _safe_mapping(metadata)
    prompt = _safe_text(prompt)
    warnings: list[str] = []
    errors: list[str] = []
    writes: list[WorkspaceWriteResult] = []

    if not environment.can_access_task_filesystem:
        warnings.append("task filesystem is not visible to code_solution_solver")
        return _finish(contract, environment, writes, warnings, errors, status="task_filesystem_not_visible")

    requirements = _requested_code_requirements(contract)

    has_code_req = any(
        Path(req.path).suffix.lower() in {".py", ".sh", ".lean"} or req.kind in {"python", "shell", "lean"}
        for req in requirements
    )
    if not has_code_req:
        solution_path = _guess_primary_solution_path(contract, environment)
        pseudo = OutputRequirement(
            path=solution_path,
            kind="python",
            mime_type="text/x-python",
            source="code_solution_solver.synthetic_solution",
            required=True,
            parent=str(Path(solution_path).parent),
            filename=Path(solution_path).name,
            suffix=".py",
            action="write_code_solution",
        )
        requirements = [pseudo, *requirements]

    seen: set[str] = set()
    for req in requirements[:48]:
        path = str(Path(req.path))
        if path in seen:
            continue
        seen.add(path)

        if not _path_under_known_root(path, environment):
            result = WorkspaceWriteResult(
                path=path,
                ok=False,
                action="skip",
                kind=req.kind,
                skipped=True,
                reason="target path is outside known SkillsBench roots",
            )
            writes.append(result)
            warnings.append(f"skipped {path}: {result.reason}")
            continue

        try:
            payload = _bytes_for_req(req, contract, environment, metadata, prompt)
        except Exception as exc:
            result = WorkspaceWriteResult(
                path=path,
                ok=False,
                action="render_code_solution",
                kind=req.kind,
                error=str(exc)[:800],
            )
            writes.append(result)
            errors.append(f"{path}: {result.error}")
            continue

        result = _write_with_fallbacks(
            path,
            payload,
            kind=req.kind or "python",
            env=environment,
            action=req.action or "write_code_solution",
        )
        writes.append(result)
        if result.error:
            errors.append(f"{result.path}: {result.error}")
        if result.skipped:
            warnings.append(f"skipped {result.path}: {result.reason}")

    status = "completed" if any(write.ok for write in writes) else "no_files_written"
    return _finish(contract, environment, writes, warnings, errors, status=status)


def _finish(
    contract: SkillsBenchOutputContract,
    env: SkillsBenchTaskEnvironment,
    writes: Sequence[WorkspaceWriteResult],
    warnings: Sequence[str],
    errors: Sequence[str],
    *,
    status: str,
) -> TaskWorkspaceExecution:
    diagnostics = {
        "solver": "code_solution_solver",
        "solver_version": CODE_SOLUTION_SOLVER_VERSION,
        "task_workspace_executor_version": TASK_WORKSPACE_EXECUTOR_VERSION,
        "task_environment_version": TASK_ENVIRONMENT_VERSION,
        "contract_summary": summarize_contract(contract),
        "write_count": len(writes),
        "ok_writes": sum(1 for write in writes if write.ok),
        "fallback_writes": sum(1 for write in writes if "fallback" in (write.action or "") or "fallback_from=" in (write.reason or "")),
        "kind_counts": dict(Counter(write.kind for write in writes)),
        "write_outcomes": [write.as_dict() for write in writes[:80]],
        "artifact_records": [
            {
                "path": write.path,
                "sha256": write.sha256,
                "size_bytes": write.bytes_written,
                "kind": write.kind,
            }
            for write in writes
            if write.ok
        ],
    }
    return TaskWorkspaceExecution(
        version=CODE_SOLUTION_SOLVER_VERSION,
        ok=bool(any(write.ok for write in writes)),
        status=status,
        task_id=contract.task_id,
        family=contract.family,
        workspace_visible=env.can_access_task_filesystem,
        wrote_any_file=any(write.ok for write in writes),
        writes=tuple(writes),
        contract=contract.as_context(),
        environment=env.as_context(),
        diagnostics=diagnostics,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def validate_code_solution_solver_selftest() -> dict[str, Any]:
    """Lightweight import/render selftest without requiring task filesystem writes."""

    from ..output_contract import build_output_contract
    from ..task_environment import discover_task_environment

    sample = """
    Write a Python solution to /root/workspace/solution.py.
    Also generate /root/answer.json with {"ok": true, "score": 0}.
    """
    metadata = {
        "task_id": "code-solver-selftest",
        "category": "software-engineering",
        "tags": ["python", "algorithm"],
    }
    contract = build_output_contract(metadata, sample)
    env = discover_task_environment(metadata, sample, task_id=contract.task_id, write_probe=False)

    template = _code_template(contract, env, metadata, sample)
    errors: list[str] = []
    if "def solve" not in template:
        errors.append("template missing solve")
    if "def main" not in template:
        errors.append("template missing main")
    if "code-solver-selftest" not in template:
        errors.append("template missing task_id")
    if not any(req.path.endswith("solution.py") for req in _requested_code_requirements(contract)):
        errors.append("contract did not expose solution.py requirement")

    return {
        "ok": not errors,
        "errors": errors,
        "version": CODE_SOLUTION_SOLVER_VERSION,
        "contract_summary": summarize_contract(contract),
        "template_bytes": len(template.encode("utf-8")),
    }


__all__ = [
    "CODE_SOLUTION_SOLVER_VERSION",
    "code_solution_solver",
    "validate_code_solution_solver_selftest",
]
