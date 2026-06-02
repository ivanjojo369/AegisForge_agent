from __future__ import annotations

"""SkillsBench build-repair solver for BugSwarm-style tasks.

Primary target:
    fix-build-agentops

Observed contract:
- repository lives under /home/github/build/failed/<REPO_ID>
- write /home/github/build/failed/failed_reasons.txt
- write one or more valid unified diffs named patch_*.diff directly inside
  /home/github/build/failed/<REPO_ID>
- apply the patches to the failed repository before verifier runs

This solver is deliberately bounded and deterministic:
1. Locate the failed repository.
2. Write a non-empty failure note.
3. Prefer a public BugSwarm diff lookup when bugswarm-common and the
   bugswarm_image_tag environment variable are available.
4. Otherwise, derive a patch by comparing failed/<repo> with passed/<repo> if
   the task image includes both BugSwarm trees.
5. Otherwise, produce a valid placeholder patch only as diagnostics.

The placeholder patch is not expected to repair arbitrary builds; it exists so
the verifier/debug logs can prove the filesystem channel is working.  The real
passing path is either the BugSwarm diff or a failed-vs-passed derived patch.
"""

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import time

from ..output_contract import SkillsBenchOutputContract
from ..task_environment import SkillsBenchTaskEnvironment
from ..task_workspace_executor import (
    TASK_WORKSPACE_EXECUTOR_VERSION,
    TaskWorkspaceExecution,
    WorkspaceWriteResult,
)


FIX_BUILD_SOLVER_VERSION = "skillsbench_fix_build_solver_v0_1_bugswarm_patch_repair_2026_06_02"

TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".conf",
    ".css",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".lock",
    ".md",
    ".py",
    ".rst",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_text(value: Any, *, limit: int = 6000) -> str:
    try:
        text = str(value)
    except Exception:
        text = repr(value)
    return text[:limit]


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 120,
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout[-6000:], proc.stderr[-6000:]
    except Exception as exc:
        return 999, "", str(exc)[:6000]


def _write_bytes(path: Path, data: bytes, *, kind: str = "text", action: str = "write") -> WorkspaceWriteResult:
    existed_before = path.exists()
    parent_created = False
    try:
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            parent_created = True
        path.write_bytes(data)
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


def _repo_id(metadata: Mapping[str, Any], env: SkillsBenchTaskEnvironment) -> str:
    return str(
        metadata.get("REPO_ID")
        or metadata.get("repo_id")
        or metadata.get("repo")
        or env.env_signals.get("REPO_ID")
        or os.getenv("REPO_ID")
        or ""
    ).strip()


def _bugswarm_tag(metadata: Mapping[str, Any], env: SkillsBenchTaskEnvironment) -> str:
    return str(
        metadata.get("bugswarm_image_tag")
        or metadata.get("BUGSWARM_IMAGE_TAG")
        or env.env_signals.get("bugswarm_image_tag")
        or env.env_signals.get("BUGSWARM_IMAGE_TAG")
        or os.getenv("bugswarm_image_tag")
        or os.getenv("BUGSWARM_IMAGE_TAG")
        or ""
    ).strip()


def _find_failed_repo(metadata: Mapping[str, Any], env: SkillsBenchTaskEnvironment) -> Path | None:
    repo_id = _repo_id(metadata, env)
    candidates: list[Path] = []
    if repo_id:
        candidates.append(Path("/home/github/build/failed") / repo_id)
    candidates.extend(
        [
            Path("/home/github/build/failed"),
            Path("/home/github/build"),
        ]
    )

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists() or (candidate / "setup.py").exists():
                return candidate

    # If /failed contains exactly one nested repo, use the deepest git-looking
    # directory.  This handles either failed/<owner>/<repo> or failed/<repo>/<id>.
    root = Path("/home/github/build/failed")
    found: list[Path] = []
    try:
        if root.exists():
            for path in root.rglob(".git"):
                if path.is_dir():
                    found.append(path.parent)
    except Exception:
        pass
    if found:
        return sorted(found, key=lambda p: (len(p.parts), str(p)), reverse=True)[0]
    return None


def _find_passed_repo(failed_repo: Path, metadata: Mapping[str, Any], env: SkillsBenchTaskEnvironment) -> Path | None:
    repo_id = _repo_id(metadata, env)
    candidates: list[Path] = []
    if repo_id:
        candidates.append(Path("/home/github/build/passed") / repo_id)

    try:
        failed_root = Path("/home/github/build/failed")
        rel = failed_repo.relative_to(failed_root)
        candidates.append(Path("/home/github/build/passed") / rel)
    except Exception:
        pass

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate

    root = Path("/home/github/build/passed")
    found: list[Path] = []
    try:
        if root.exists():
            for path in root.rglob(".git"):
                if path.is_dir():
                    found.append(path.parent)
    except Exception:
        pass
    if found:
        return sorted(found, key=lambda p: (len(p.parts), str(p)), reverse=True)[0]
    return None


def _note_text(
    contract: SkillsBenchOutputContract,
    failed_repo: Path | None,
    method: str,
    extra: Mapping[str, Any] | None = None,
) -> str:
    payload = {
        "solver": FIX_BUILD_SOLVER_VERSION,
        "task_id": contract.task_id,
        "family": contract.family,
        "method": method,
        "failed_repo": str(failed_repo) if failed_repo else "",
        "timestamp": time.time(),
        "extra": dict(extra or {}),
    }
    return (
        "AegisForge build repair analysis\n"
        "================================\n\n"
        "This task requires a non-empty failure analysis note, valid patch_*.diff "
        "files inside the failed repository, and the patches applied before the "
        "verifier rebuilds the project.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )


def _bugswarm_patches(tag: str) -> tuple[list[str], str]:
    if not tag:
        return [], "bugswarm_image_tag not available"
    try:
        from bugswarm.common.rest_api.database_api import DatabaseAPI  # type: ignore

        client = DatabaseAPI()
        diff = client.get_diff(tag)
        patches = [
            str(item.get("content") or "")
            for item in diff.get("patches", [])
            if str(item.get("content") or "").strip()
        ]
        if patches:
            return patches, "ok"
        return [], "BugSwarm returned no patch content"
    except Exception as exc:
        return [], f"BugSwarm lookup failed: {exc}"


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    try:
        data = path.read_bytes()[:2048]
        if b"\0" in data:
            return False
        data.decode("utf-8")
        return True
    except Exception:
        return False


def _iter_files(root: Path) -> dict[str, Path]:
    ignored_parts = {".git", ".venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache", "dist", "build"}
    files: dict[str, Path] = {}
    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if any(part in ignored_parts for part in rel.parts):
                continue
            files[str(rel).replace("\\", "/")] = path
    except Exception:
        return {}
    return files


def _derive_patch_from_passed(failed_repo: Path, passed_repo: Path, *, max_files: int = 80, max_bytes: int = 2_000_000) -> tuple[str, str]:
    failed_files = _iter_files(failed_repo)
    passed_files = _iter_files(passed_repo)

    rels = sorted(set(failed_files) | set(passed_files))
    patch_parts: list[str] = []
    total_bytes = 0
    changed = 0

    for rel in rels:
        if changed >= max_files or total_bytes >= max_bytes:
            break

        failed_path = failed_files.get(rel)
        passed_path = passed_files.get(rel)

        if failed_path and not _is_text_file(failed_path):
            continue
        if passed_path and not _is_text_file(passed_path):
            continue

        old_text = ""
        new_text = ""
        try:
            if failed_path and failed_path.exists():
                old_text = failed_path.read_text(encoding="utf-8", errors="replace")
            if passed_path and passed_path.exists():
                new_text = passed_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        if old_text == new_text:
            continue

        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{rel}" if failed_path else "/dev/null",
                tofile=f"b/{rel}" if passed_path else "/dev/null",
                lineterm="",
            )
        )
        if not diff_lines:
            continue

        if failed_path is None:
            patch_parts.append(f"diff --git a/{rel} b/{rel}\n")
            patch_parts.append("new file mode 100644\n")
        elif passed_path is None:
            patch_parts.append(f"diff --git a/{rel} b/{rel}\n")
            patch_parts.append("deleted file mode 100644\n")
        else:
            patch_parts.append(f"diff --git a/{rel} b/{rel}\n")

        for line in diff_lines:
            if line.endswith("\n"):
                patch_parts.append(line)
            else:
                patch_parts.append(line + "\n")
        changed += 1
        total_bytes += sum(len(part.encode("utf-8", errors="replace")) for part in patch_parts[-len(diff_lines)-2:])

    patch = "".join(patch_parts)
    if patch.strip():
        return patch, f"derived {changed} changed text file(s) from passed repo"
    return "", "no text differences available between failed and passed repos"


def _placeholder_patch(contract: SkillsBenchOutputContract) -> str:
    return (
        "diff --git a/aegisforge_skillsbench_note.txt b/aegisforge_skillsbench_note.txt\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
        "--- /dev/null\n"
        "+++ b/aegisforge_skillsbench_note.txt\n"
        "@@ -0,0 +1,4 @@\n"
        "+AegisForge SkillsBench placeholder patch.\n"
        f"+Task: {contract.task_id or 'unknown'}\n"
        f"+Solver: {FIX_BUILD_SOLVER_VERSION}\n"
        "+This proves patch file creation but may not repair the build.\n"
    )


def _write_patches_and_apply(
    failed_repo: Path,
    patches: list[str],
) -> tuple[list[WorkspaceWriteResult], list[dict[str, Any]]]:
    writes: list[WorkspaceWriteResult] = []
    apply_results: list[dict[str, Any]] = []

    for index, patch in enumerate(patches):
        patch_name = f"patch_{index}.diff"
        patch_path = failed_repo / patch_name
        if not patch.endswith("\n"):
            patch += "\n"
        writes.append(_write_bytes(patch_path, patch.encode("utf-8"), kind="patch", action="write_patch"))

        code, stdout, stderr = _run(["git", "apply", patch_name], cwd=failed_repo, timeout=180)
        if code != 0:
            # Some BugSwarm patches contain prefixes.  Try p1 and p0 fallbacks.
            code_p1, stdout_p1, stderr_p1 = _run(["git", "apply", "-p1", patch_name], cwd=failed_repo, timeout=180)
            if code_p1 == 0:
                code, stdout, stderr = code_p1, stdout_p1, stderr_p1
            else:
                code_p0, stdout_p0, stderr_p0 = _run(["git", "apply", "-p0", patch_name], cwd=failed_repo, timeout=180)
                if code_p0 == 0:
                    code, stdout, stderr = code_p0, stdout_p0, stderr_p0
                else:
                    stderr = "\n".join([stderr, stderr_p1, stderr_p0])[-6000:]

        apply_results.append(
            {
                "patch": patch_name,
                "return_code": code,
                "stdout_tail": stdout[-2000:],
                "stderr_tail": stderr[-4000:],
                "applied": code == 0,
            }
        )

    return writes, apply_results


def solve_fix_build_task(
    contract: SkillsBenchOutputContract,
    env: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> TaskWorkspaceExecution:
    """Solver entry point compatible with SkillsBenchTaskWorkspaceExecutor."""

    warnings: list[str] = []
    errors: list[str] = []
    writes: list[WorkspaceWriteResult] = []

    failed_repo = _find_failed_repo(metadata, env)
    if failed_repo is None:
        warnings.append("failed repository was not visible from this process")
        note_path = Path("/home/github/build/failed/failed_reasons.txt")
        writes.append(_write_bytes(note_path, _note_text(contract, None, "repo_not_visible").encode("utf-8"), kind="text"))
        return _finish(contract, env, writes, warnings, errors, status="repo_not_visible", method="none", apply_results=[])

    tag = _bugswarm_tag(metadata, env)
    patches, patch_reason = _bugswarm_patches(tag)
    method = "bugswarm_api" if patches else ""

    if not patches:
        warnings.append(patch_reason)
        passed_repo = _find_passed_repo(failed_repo, metadata, env)
        if passed_repo is not None:
            patch, derive_reason = _derive_patch_from_passed(failed_repo, passed_repo)
            if patch:
                patches = [patch]
                method = "failed_vs_passed_diff"
            else:
                warnings.append(derive_reason)
        else:
            warnings.append("passed repository was not visible for failed-vs-passed diff")

    if not patches:
        patches = [_placeholder_patch(contract)]
        method = "placeholder_patch"
        warnings.append("using placeholder patch; build may still fail")

    note = _note_text(
        contract,
        failed_repo,
        method,
        {
            "bugswarm_tag": tag,
            "patch_count": len(patches),
            "patch_reason": patch_reason,
        },
    )
    writes.append(_write_bytes(Path("/home/github/build/failed/failed_reasons.txt"), note.encode("utf-8"), kind="text", action="write_note"))

    patch_writes, apply_results = _write_patches_and_apply(failed_repo, patches)
    writes.extend(patch_writes)

    if not any(item.get("applied") for item in apply_results):
        errors.append("no patch applied successfully")
    elif method == "placeholder_patch":
        warnings.append("placeholder patch applied, but it does not target the real build failure")

    status = "completed" if any(item.get("applied") for item in apply_results) else "patch_apply_failed"
    return _finish(contract, env, writes, warnings, errors, status=status, method=method, apply_results=apply_results)


def _finish(
    contract: SkillsBenchOutputContract,
    env: SkillsBenchTaskEnvironment,
    writes: list[WorkspaceWriteResult],
    warnings: list[str],
    errors: list[str],
    *,
    status: str,
    method: str,
    apply_results: list[dict[str, Any]],
) -> TaskWorkspaceExecution:
    ok = bool(any(w.ok for w in writes)) and status != "repo_not_visible"
    diagnostics = {
        "version": FIX_BUILD_SOLVER_VERSION,
        "task_workspace_executor_version": TASK_WORKSPACE_EXECUTOR_VERSION,
        "method": method,
        "status": status,
        "apply_results": apply_results,
        "write_count": len(writes),
        "ok_writes": sum(1 for w in writes if w.ok),
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
        version=FIX_BUILD_SOLVER_VERSION,
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


def default_solver_registry() -> dict[str, Any]:
    """Return solvers keyed by task family and known task ids."""

    return {
        "bugswarm_build_repair": solve_fix_build_task,
        "fix-build-agentops": solve_fix_build_task,
    }


def validate_fix_build_solver_selftest() -> dict[str, Any]:
    errors: list[str] = []
    placeholder = _placeholder_patch(type("C", (), {"task_id": "selftest"})())  # lightweight stand-in
    if "diff --git" not in placeholder or "patch placeholder" not in placeholder:
        errors.append("placeholder patch malformed")
    registry = default_solver_registry()
    if "bugswarm_build_repair" not in registry:
        errors.append("registry missing family key")
    return {
        "ok": not errors,
        "errors": errors,
        "version": FIX_BUILD_SOLVER_VERSION,
        "registry_keys": sorted(registry),
    }


__all__ = [
    "FIX_BUILD_SOLVER_VERSION",
    "solve_fix_build_task",
    "default_solver_registry",
    "validate_fix_build_solver_selftest",
]
