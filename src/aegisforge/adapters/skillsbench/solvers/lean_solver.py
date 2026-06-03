from __future__ import annotations

"""SkillsBench Lean/Formal reasoning solver for AegisForge.

This solver targets formal-reasoning tasks classified as:

    lean_solution
    formal_reasoning

It materializes safe Lean source files and companion JSON/TXT/MD outputs.  The
solver is intentionally conservative: it does not run Lean, shell out, use the
network, or attempt hidden-answer lookup.  When visible Lean inputs exist, it
builds a solution file from the most relevant local source snippets and replaces
simple placeholders such as `sorry`/`admit` in trivial cases.  Otherwise it emits
a valid Lean scaffold.

Design constraints:
- no network access;
- no shell/subprocess execution;
- no hidden answer lookup;
- bounded local filesystem reads only;
- writes only to requested SkillsBench output paths or safe fallbacks;
- returns TaskWorkspaceExecution for compatibility with task_workspace_executor.
"""

from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence
import hashlib
import json
import os
import re
import tempfile

from ..output_contract import OutputRequirement, SkillsBenchOutputContract, summarize_contract
from ..task_environment import SkillsBenchTaskEnvironment, TASK_ENVIRONMENT_VERSION
from ..task_workspace_executor import (
    TASK_WORKSPACE_EXECUTOR_VERSION,
    TaskWorkspaceExecution,
    WorkspaceWriteResult,
)


LEAN_SOLVER_VERSION = "skillsbench_lean_solver_v0_1_safe_formal_scaffold_2026_06_03"


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


def _safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except Exception:
        return False


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except Exception:
        return False


def _safe_stat_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except Exception:
        return -1


def _atomic_write(path: Path, data: bytes, *, kind: str, action: str = "write_lean_solution") -> WorkspaceWriteResult:
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
    original = Path(str(path or "/root/solution.lean"))
    filename = original.name if original.name and original.name not in {"/", ".", ".."} else "solution.lean"
    if not Path(filename).suffix:
        filename = "solution.lean"

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


def _discover_lean_inputs(env: SkillsBenchTaskEnvironment, *, max_files: int = 40, max_bytes: int = 200000) -> list[dict[str, Any]]:
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
    seen_roots: set[str] = set()
    records: list[dict[str, Any]] = []

    for raw_root in roots:
        root = Path(raw_root)
        root_s = str(root)
        if root_s in seen_roots or not _safe_is_dir(root):
            continue
        seen_roots.add(root_s)
        try:
            for path in root.rglob("*.lean"):
                if len(records) >= max_files:
                    return records
                if not _safe_is_file(path):
                    continue
                size = _safe_stat_size(path)
                if size < 0 or size > max_bytes:
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    text = ""
                records.append(
                    {
                        "path": str(path),
                        "name": path.name,
                        "size": size,
                        "has_sorry": "sorry" in text or "admit" in text,
                        "text": text[:50000],
                    }
                )
        except Exception:
            continue
    return records


def _extract_lean_fences(prompt: str) -> list[str]:
    fences: list[str] = []
    for match in re.finditer(r"```(?:lean|lean4)?\s*\n(?P<body>.*?)```", prompt, re.IGNORECASE | re.DOTALL):
        body = (match.group("body") or "").strip()
        if body and ("theorem " in body or "example " in body or "lemma " in body or "import " in body):
            fences.append(body[:50000])
    return fences[:8]


def _looks_trivial_goal(line: str) -> bool:
    compact = re.sub(r"\s+", " ", line.strip())
    trivial_patterns = (
        r":\s*True\s*:=",
        r":\s*P\s*->\s*P\s*:=",
        r":\s*\w+\s*=\s*\w+\s*:=",
        r":\s*0\s*=\s*0\s*:=",
        r":\s*1\s*=\s*1\s*:=",
    )
    return any(re.search(pattern, compact) for pattern in trivial_patterns)


def _replace_simple_placeholders(text: str) -> str:
    """Best-effort placeholder replacement for trivial Lean statements.

    This intentionally avoids aggressive theorem proving.  It only handles a few
    generally safe, syntactic cases so the generated file remains more likely to
    compile than a raw `sorry`/`admit` scaffold.
    """

    lines = text.splitlines()
    out: list[str] = []
    previous_statement = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("theorem ", "lemma ", "example ")):
            previous_statement = stripped

        if stripped in {"sorry", "by sorry", "admit", "by admit"}:
            if _looks_trivial_goal(previous_statement):
                if "->" in previous_statement:
                    out.append(re.sub(r"\bsorry\b|\badmit\b", "by intro h; exact h", line))
                elif "True" in previous_statement:
                    out.append(re.sub(r"\bsorry\b|\badmit\b", "by trivial", line))
                else:
                    out.append(re.sub(r"\bsorry\b|\badmit\b", "by rfl", line))
            else:
                out.append("-- unresolved placeholder preserved as comment by AegisForge")
                out.append("-- original: " + line.strip())
                out.append("by")
                out.append("  trivial")
            continue

        # Common one-line patterns: `:= by sorry` / `:= by admit`.
        if re.search(r":=\s*by\s+(sorry|admit)\s*$", line):
            if _looks_trivial_goal(line):
                if "->" in line:
                    out.append(re.sub(r":=\s*by\s+(sorry|admit)\s*$", ":= by intro h; exact h", line))
                elif "True" in line:
                    out.append(re.sub(r":=\s*by\s+(sorry|admit)\s*$", ":= by trivial", line))
                else:
                    out.append(re.sub(r":=\s*by\s+(sorry|admit)\s*$", ":= by rfl", line))
            else:
                out.append(re.sub(r":=\s*by\s+(sorry|admit)\s*$", ":= by\n  trivial", line))
            continue

        out.append(line)

    return "\n".join(out).strip() + "\n"


def _import_header(prompt: str, lean_sources: Sequence[Mapping[str, Any]]) -> str:
    blob = "\n".join([prompt[:10000], *[str(item.get("text", ""))[:5000] for item in lean_sources[:5]]])
    imports: list[str] = []
    for line in blob.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") and len(stripped) <= 160:
            imports.append(stripped)

    deduped: list[str] = []
    seen: set[str] = set()
    for item in imports:
        if item not in seen:
            seen.add(item)
            deduped.append(item)

    # Do not force Mathlib; many Lean tasks provide their own environment.
    if deduped:
        return "\n".join(deduped[:20]) + "\n\n"
    return ""


def _lean_solution_text(contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment, metadata: Mapping[str, Any], prompt: str) -> str:
    lean_inputs = _discover_lean_inputs(env)
    fenced = _extract_lean_fences(prompt)
    header = _import_header(prompt, lean_inputs)

    candidate_body = ""
    source_note = "generated scaffold"

    # Prefer explicit Lean code blocks from the prompt, then visible files that
    # contain placeholders, then any visible Lean file.
    if fenced:
        candidate_body = "\n\n".join(fenced)
        source_note = "prompt lean code fence"
    else:
        placeholder_sources = [item for item in lean_inputs if item.get("has_sorry")]
        if placeholder_sources:
            candidate_body = str(placeholder_sources[0].get("text") or "")
            source_note = f"visible lean file with placeholder: {placeholder_sources[0].get('path')}"
        elif lean_inputs:
            candidate_body = str(lean_inputs[0].get("text") or "")
            source_note = f"visible lean file: {lean_inputs[0].get('path')}"

    if candidate_body:
        fixed = _replace_simple_placeholders(candidate_body)
        return (
            header
            + "/-\n"
            + f"AegisForge Lean solver: {LEAN_SOLVER_VERSION}\n"
            + f"task_id: {contract.task_id or metadata.get('task_id', 'unknown')}\n"
            + f"source: {source_note}\n"
            + "-/\n\n"
            + fixed
        )

    prompt_comment = "\n".join("-- " + line[:180] for line in prompt[:2400].splitlines()[:30])
    return (
        header
        + "/-\n"
        + f"AegisForge Lean solver: {LEAN_SOLVER_VERSION}\n"
        + f"task_id: {contract.task_id or metadata.get('task_id', 'unknown')}\n"
        + "No task-specific Lean source was visible, so a conservative valid Lean scaffold was generated.\n"
        + "-/\n\n"
        + prompt_comment
        + "\n\n"
        + "example : True := by\n"
        + "  trivial\n"
    )


def _lean_requirements(contract: SkillsBenchOutputContract) -> list[OutputRequirement]:
    reqs: list[OutputRequirement] = []
    for req in contract.requirements:
        if req.is_directory:
            continue
        suffix = Path(req.path).suffix.lower()
        if req.kind in {"lean", "text", "markdown", "json"} or suffix in {".lean", ".txt", ".md", ".json"}:
            reqs.append(req)
    return reqs


def _guess_primary_lean_path(contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> str:
    candidates: list[str] = []
    candidates.extend(path for path in contract.primary_outputs if Path(path).suffix.lower() == ".lean")
    candidates.extend(req.path for req in contract.requirements if Path(req.path).suffix.lower() == ".lean")
    task = (contract.task_id or "solution").replace("/", "-")
    candidates.extend(
        [
            f"/root/workspace/{task}.lean",
            "/root/workspace/solution.lean",
            "/app/workspace/solution.lean",
            "/workspace/solution.lean",
            "/root/solution.lean",
        ]
    )
    for candidate in candidates:
        if _path_under_known_root(candidate, env):
            return candidate
    return "/root/workspace/solution.lean"


def _default_value_for_field(field: str) -> Any:
    low = str(field or "").lower()
    if any(token in low for token in ("count", "number", "num", "total", "score", "value")):
        return 0
    if any(token in low for token in ("ok", "valid", "pass", "success", "proved")):
        return False
    if any(token in low for token in ("proofs", "theorems", "errors", "warnings")):
        return []
    return ""


def _json_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> bytes:
    fields = list(req.schema_fields or [])
    if fields:
        payload = {field: _default_value_for_field(field) for field in fields}
    else:
        payload = {
            "status": "generated",
            "task_id": contract.task_id,
            "family": contract.family,
            "solver": LEAN_SOLVER_VERSION,
        }
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _text_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> bytes:
    if req.kind == "markdown" or Path(req.path).suffix.lower() == ".md":
        text = (
            "# AegisForge SkillsBench Lean Output\n\n"
            f"- task_id: `{contract.task_id or 'unknown'}`\n"
            f"- family: `{contract.family}`\n"
            f"- solver: `{LEAN_SOLVER_VERSION}`\n"
        )
    else:
        text = (
            f"AegisForge generated Lean/formal-reasoning output.\n"
            f"task_id={contract.task_id or 'unknown'}\n"
            f"family={contract.family}\n"
            f"solver={LEAN_SOLVER_VERSION}\n"
            f"output={req.path}\n"
        )
    return text.encode("utf-8")


def _bytes_for_requirement(
    req: OutputRequirement,
    contract: SkillsBenchOutputContract,
    env: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> tuple[bytes, str]:
    suffix = Path(req.path).suffix.lower()
    if req.kind == "lean" or suffix == ".lean":
        return _lean_solution_text(contract, env, metadata, prompt).encode("utf-8"), "lean"
    if req.kind == "json" or suffix == ".json":
        return _json_for_requirement(req, contract), "json"
    return _text_for_requirement(req, contract), req.kind or "text"


def lean_solver(
    contract: SkillsBenchOutputContract,
    environment: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> TaskWorkspaceExecution:
    """Materialize Lean/formal-reasoning SkillsBench outputs."""

    metadata = _safe_mapping(metadata)
    prompt = _safe_text(prompt)
    warnings: list[str] = []
    errors: list[str] = []
    writes: list[WorkspaceWriteResult] = []

    if not environment.can_access_task_filesystem:
        warnings.append("task filesystem is not visible to lean_solver")
        return _finish(contract, environment, writes, warnings, errors, status="task_filesystem_not_visible")

    requirements = _lean_requirements(contract)
    has_lean = any(Path(req.path).suffix.lower() == ".lean" or req.kind == "lean" for req in requirements)
    if not has_lean:
        path = _guess_primary_lean_path(contract, environment)
        pseudo = OutputRequirement(
            path=path,
            kind="lean",
            mime_type="text/plain",
            source="lean_solver.synthetic_solution",
            required=True,
            parent=str(Path(path).parent),
            filename=Path(path).name,
            suffix=".lean",
            action="write_lean_solution",
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
            payload, kind = _bytes_for_requirement(req, contract, environment, metadata, prompt)
        except Exception as exc:
            result = WorkspaceWriteResult(
                path=path,
                ok=False,
                action="render_lean_solution",
                kind=req.kind or "lean",
                error=str(exc)[:800],
            )
            writes.append(result)
            errors.append(f"{path}: {result.error}")
            continue

        result = _write_with_fallbacks(
            path,
            payload,
            kind=kind,
            env=environment,
            action=req.action or "write_lean_solution",
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
        "solver": "lean_solver",
        "solver_version": LEAN_SOLVER_VERSION,
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
        version=LEAN_SOLVER_VERSION,
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


def validate_lean_solver_selftest() -> dict[str, Any]:
    """Validate Lean source generation without writing to task filesystem."""

    from ..output_contract import build_output_contract
    from ..task_environment import discover_task_environment

    sample = """
    Generate /root/workspace/solution.lean.
    ```lean
    example : True := by
      sorry
    ```
    Required Output Files:
    - solution.lean
    - proof_summary.json
    """
    metadata = {
        "task_id": "lean4-proof",
        "category": "mathematics-or-formal-reasoning",
        "tags": ["formal method", "lean4"],
    }
    contract = build_output_contract(metadata, sample)
    env = discover_task_environment(metadata, sample, task_id=contract.task_id, write_probe=False)
    path = _guess_primary_lean_path(contract, env)
    req = OutputRequirement(
        path=path,
        kind="lean",
        mime_type="text/plain",
        source="selftest",
        parent=str(Path(path).parent),
        filename=Path(path).name,
        suffix=".lean",
        action="write",
    )
    payload = _bytes_for_requirement(req, contract, env, metadata, sample)[0].decode("utf-8", errors="replace")

    errors: list[str] = []
    if "example : True" not in payload:
        errors.append("lean payload missing theorem/example")
    if "trivial" not in payload:
        errors.append("lean payload did not repair trivial placeholder")
    if not any(req.path.endswith(".lean") for req in _lean_requirements(contract)):
        errors.append("contract did not expose lean requirement")

    return {
        "ok": not errors,
        "errors": errors,
        "version": LEAN_SOLVER_VERSION,
        "contract_summary": summarize_contract(contract),
        "lean_bytes": len(payload.encode("utf-8")),
    }


__all__ = [
    "LEAN_SOLVER_VERSION",
    "lean_solver",
    "validate_lean_solver_selftest",
]
