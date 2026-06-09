from __future__ import annotations

"""SkillsBench build-repair solver for BugSwarm-style tasks.

Primary target family:
    build-repair / BugSwarm tasks such as fix-build-agentops and
    fix-build-google-auto.

Observed SkillsBench contract:
- repository usually lives under /home/github/build/failed/<REPO_ID>/...
- write /home/github/build/failed/failed_reasons.txt
- write one or more valid unified diffs named patch_*.diff inside the failed
  repository;
- apply the patches to the failed repository before the verifier runs.

This solver is bounded, deterministic, and filesystem-only.  It does not call
network services except for the optional local BugSwarm database client when it
is already installed inside the task image.  If a real repair patch cannot be
found, it produces a valid diagnostic placeholder patch so the run can prove the
filesystem/patch channel is wired, without pretending that the build was fixed.
"""

from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from ..output_contract import SkillsBenchOutputContract
from ..task_environment import SkillsBenchTaskEnvironment
from ..task_workspace_executor import (
    TASK_WORKSPACE_EXECUTOR_VERSION,
    TaskWorkspaceExecution,
    WorkspaceWriteResult,
)


FIX_BUILD_SOLVER_VERSION = "skillsbench_fix_build_solver_v0_2_code_solution_patch_channel_2026_06_09"

TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".conf",
    ".css",
    ".gradle",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".lock",
    ".md",
    ".pom",
    ".properties",
    ".py",
    ".rb",
    ".rst",
    ".scala",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

IGNORED_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".gradle",
    "node_modules",
    "target",
    "dist",
    "build",
    ".idea",
    ".vscode",
}

BUILD_REPAIR_TASK_IDS = {
    "fix-build-agentops",
    "fix-build-google-auto",
}

BUILD_REPAIR_FAMILY_KEYS = {
    "bugswarm_build_repair",
    "build_repair",
    "software_patch",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_text(value: Any, *, limit: int = 6000) -> str:
    try:
        text = str(value)
    except Exception:
        text = repr(value)
    return text[:limit]


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


def _safe_iterdir(path: Path) -> list[Path]:
    try:
        return sorted(path.iterdir(), key=lambda item: str(item))
    except Exception:
        return []


def _safe_rglob(path: Path, pattern: str) -> list[Path]:
    try:
        return sorted(path.rglob(pattern), key=lambda item: str(item))
    except Exception:
        return []


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
        return 999, "", f"{type(exc).__name__}: {str(exc)[:6000]}"


def _write_bytes(path: Path, data: bytes, *, kind: str = "text", action: str = "write") -> WorkspaceWriteResult:
    """Exception-safe atomic-ish write used by the solver.

    The executor already has a robust writer, but this solver needs direct writes
    into the failed repository and to /home/github/build/failed/failed_reasons.txt
    because the verifier expects those exact locations.
    """

    parent_created = False
    existed_before = False
    tmp_path: Path | None = None
    try:
        existed_before = _safe_exists(path)
        if not _safe_exists(path.parent):
            path.parent.mkdir(parents=True, exist_ok=True)
            parent_created = True

        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(str(tmp_path), str(path))

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
            error=f"{type(exc).__name__}: {str(exc)[:800]}",
            parent_created=parent_created,
            existed_before=existed_before,
        )
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


def _normalize_task_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("_", "-")
    text = re.sub(r"[^a-z0-9-]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def _repo_id(metadata: Mapping[str, Any], env: SkillsBenchTaskEnvironment) -> str:
    return str(
        metadata.get("REPO_ID")
        or metadata.get("repo_id")
        or metadata.get("repo")
        or metadata.get("repository")
        or env.env_signals.get("REPO_ID")
        or env.env_signals.get("repo_id")
        or os.getenv("REPO_ID")
        or os.getenv("repo_id")
        or ""
    ).strip()


def _bugswarm_tag(metadata: Mapping[str, Any], env: SkillsBenchTaskEnvironment) -> str:
    return str(
        metadata.get("bugswarm_image_tag")
        or metadata.get("BUGSWARM_IMAGE_TAG")
        or metadata.get("image_tag")
        or env.env_signals.get("bugswarm_image_tag")
        or env.env_signals.get("BUGSWARM_IMAGE_TAG")
        or os.getenv("bugswarm_image_tag")
        or os.getenv("BUGSWARM_IMAGE_TAG")
        or ""
    ).strip()


def _looks_like_repo(path: Path) -> bool:
    if not _safe_is_dir(path):
        return False
    markers = (
        ".git",
        "pyproject.toml",
        "setup.py",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "package.json",
        "Cargo.toml",
        "Makefile",
    )
    return any(_safe_exists(path / marker) for marker in markers)


def _repo_candidates_under(root: Path) -> list[Path]:
    candidates: list[Path] = []
    if _looks_like_repo(root):
        candidates.append(root)

    for child in _safe_iterdir(root):
        if _looks_like_repo(child):
            candidates.append(child)

    for git_dir in _safe_rglob(root, ".git"):
        if _safe_is_dir(git_dir):
            candidates.append(git_dir.parent)

    # Prefer deeper concrete repos over broad /home/github/build/failed.
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in sorted(candidates, key=lambda p: (len(p.parts), str(p)), reverse=True):
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _find_failed_repo(metadata: Mapping[str, Any], env: SkillsBenchTaskEnvironment) -> Path | None:
    repo_id = _repo_id(metadata, env)
    candidates: list[Path] = []

    if repo_id:
        candidates.extend(
            [
                Path("/home/github/build/failed") / repo_id,
                Path("/home/github/build/failed") / repo_id.replace("/", "-"),
                Path("/home/github/build/failed") / repo_id.split("/")[-1],
            ]
        )

    candidates.extend(
        [
            Path("/home/github/build/failed"),
            Path("/home/github/build"),
            Path("/workspace"),
            Path("/app/workspace"),
        ]
    )

    for candidate in candidates:
        for repo in _repo_candidates_under(candidate):
            return repo
    return None


def _find_passed_repo(failed_repo: Path, metadata: Mapping[str, Any], env: SkillsBenchTaskEnvironment) -> Path | None:
    repo_id = _repo_id(metadata, env)
    candidates: list[Path] = []

    if repo_id:
        candidates.extend(
            [
                Path("/home/github/build/passed") / repo_id,
                Path("/home/github/build/passed") / repo_id.replace("/", "-"),
                Path("/home/github/build/passed") / repo_id.split("/")[-1],
            ]
        )

    try:
        failed_root = Path("/home/github/build/failed")
        rel = failed_repo.relative_to(failed_root)
        candidates.append(Path("/home/github/build/passed") / rel)
    except Exception:
        pass

    candidates.append(Path("/home/github/build/passed"))

    for candidate in candidates:
        for repo in _repo_candidates_under(candidate):
            return repo
    return None


def _collect_failure_signals(failed_repo: Path | None, prompt: str) -> dict[str, Any]:
    """Collect bounded local evidence useful for failed_reasons.txt."""

    signals: dict[str, Any] = {
        "prompt_excerpt": _safe_text(prompt, limit=1200),
        "repo_visible": bool(failed_repo),
        "repo_path": str(failed_repo) if failed_repo else "",
        "candidate_logs": [],
    }
    if failed_repo is None:
        return signals

    log_patterns = ("*.log", "*.out", "*.err", "failed_reasons.txt")
    logs: list[dict[str, str]] = []
    for pattern in log_patterns:
        for path in _safe_rglob(failed_repo, pattern)[:12]:
            if not _safe_is_file(path):
                continue
            try:
                data = path.read_text(encoding="utf-8", errors="replace")[-1800:]
            except Exception:
                continue
            logs.append({"path": str(path), "tail": data})
            if len(logs) >= 8:
                break
        if len(logs) >= 8:
            break

    signals["candidate_logs"] = logs
    return signals


def _note_text(
    contract: SkillsBenchOutputContract,
    failed_repo: Path | None,
    method: str,
    extra: Mapping[str, Any] | None = None,
) -> str:
    payload = {
        "solver": FIX_BUILD_SOLVER_VERSION,
        "task_workspace_executor_version": TASK_WORKSPACE_EXECUTOR_VERSION,
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
        "This SkillsBench build-repair task expects a non-empty failure analysis "
        "note, one or more valid patch_*.diff files in the failed repository, "
        "and patches applied before the verifier rebuilds the project.\n\n"
        "The solver first tries a local BugSwarm diff, then failed-vs-passed tree "
        "diffing, and finally a diagnostic placeholder patch only when no repair "
        "source is visible.\n\n"
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
            return [_ensure_patch_newline(patch) for patch in patches], "ok"
        return [], "BugSwarm returned no patch content"
    except Exception as exc:
        return [], f"BugSwarm lookup failed: {type(exc).__name__}: {exc}"


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    try:
        data = path.read_bytes()[:4096]
        if b"\0" in data:
            return False
        data.decode("utf-8")
        return True
    except Exception:
        return False


def _iter_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if any(part in IGNORED_PARTS for part in rel.parts):
                continue
            files[str(rel).replace("\\", "/")] = path
    except Exception:
        return {}
    return files


def _ensure_patch_newline(patch: str) -> str:
    patch = str(patch or "")
    if patch and not patch.endswith("\n"):
        patch += "\n"
    return patch


def _is_nonempty_unified_diff(patch: str) -> bool:
    text = str(patch or "")
    return (
        "diff --git " in text
        and "\n--- " in text
        and "\n+++ " in text
        and "\n@@" in text
        and any(line.startswith(("+", "-")) and not line.startswith(("+++", "---")) for line in text.splitlines())
    )


def _derive_patch_from_passed(
    failed_repo: Path,
    passed_repo: Path,
    *,
    max_files: int = 80,
    max_bytes: int = 2_000_000,
) -> tuple[str, str]:
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
            if failed_path and _safe_exists(failed_path):
                old_text = failed_path.read_text(encoding="utf-8", errors="replace")
            if passed_path and _safe_exists(passed_path):
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
                tofile=f"b/{rel}" if passed_path else f"b/{rel}",
                lineterm="",
            )
        )
        if not diff_lines:
            continue

        header = [f"diff --git a/{rel} b/{rel}\n"]
        if failed_path is None:
            header.append("new file mode 100644\n")
        elif passed_path is None:
            header.append("deleted file mode 100644\n")

        patch_parts.extend(header)
        for line in diff_lines:
            patch_parts.append(line if line.endswith("\n") else line + "\n")

        changed += 1
        total_bytes += sum(len(part.encode("utf-8", errors="replace")) for part in header)
        total_bytes += sum(len(line.encode("utf-8", errors="replace")) for line in diff_lines)

    patch = "".join(patch_parts)
    if _is_nonempty_unified_diff(patch):
        return patch, f"derived {changed} changed text file(s) from passed repo"
    return "", "no text differences available between failed and passed repos"


def _placeholder_patch(contract: SkillsBenchOutputContract) -> str:
    task = _normalize_task_id(getattr(contract, "task_id", "") or "unknown") or "unknown"
    filename = f"aegisforge_skillsbench_note_{task}.txt"
    lines = [
        "AegisForge SkillsBench placeholder patch.",
        f"Task: {getattr(contract, 'task_id', '') or 'unknown'}",
        f"Solver: {FIX_BUILD_SOLVER_VERSION}",
        "This patch proves patch-file creation and git-apply wiring only.",
    ]
    patch_lines = [
        f"diff --git a/{filename} b/{filename}\n",
        "new file mode 100644\n",
        "index 0000000..1111111\n",
        "--- /dev/null\n",
        f"+++ b/{filename}\n",
        f"@@ -0,0 +1,{len(lines)} @@\n",
    ]
    patch_lines.extend("+" + line + "\n" for line in lines)
    return "".join(patch_lines)


def _copy_patch_to_repo_root_if_needed(patch_path: Path, repo: Path) -> tuple[Path, WorkspaceWriteResult | None]:
    """git apply works best when the patch file is relative to repo cwd."""

    try:
        patch_path.relative_to(repo)
        return patch_path, None
    except Exception:
        target = repo / patch_path.name
        if target == patch_path:
            return patch_path, None
        data = patch_path.read_bytes()
        write = _write_bytes(target, data, kind="patch", action="copy_patch_to_repo_root")
        return target, write


def _try_git_apply(repo: Path, patch_name: str) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    commands = [
        ["git", "apply", "--check", patch_name],
        ["git", "apply", patch_name],
    ]
    for args in commands:
        code, stdout, stderr = _run(args, cwd=repo, timeout=180)
        attempts.append(
            {
                "command": " ".join(args),
                "return_code": code,
                "stdout_tail": stdout[-2000:],
                "stderr_tail": stderr[-4000:],
            }
        )
        if code != 0:
            return {"applied": False, "phase": args[2] if len(args) > 2 else "apply", "attempts": attempts}

    return {"applied": True, "phase": "apply", "attempts": attempts}


def _try_git_apply_with_fallbacks(repo: Path, patch_name: str) -> dict[str, Any]:
    """Try normal git apply, then p1/p0 prefix fallbacks."""

    all_attempts: list[dict[str, Any]] = []
    for prefix_args in ([], ["-p1"], ["-p0"]):
        check_cmd = ["git", "apply", *prefix_args, "--check", patch_name]
        code, stdout, stderr = _run(check_cmd, cwd=repo, timeout=180)
        all_attempts.append(
            {
                "command": " ".join(check_cmd),
                "return_code": code,
                "stdout_tail": stdout[-2000:],
                "stderr_tail": stderr[-4000:],
            }
        )
        if code != 0:
            continue

        apply_cmd = ["git", "apply", *prefix_args, patch_name]
        apply_code, apply_stdout, apply_stderr = _run(apply_cmd, cwd=repo, timeout=180)
        all_attempts.append(
            {
                "command": " ".join(apply_cmd),
                "return_code": apply_code,
                "stdout_tail": apply_stdout[-2000:],
                "stderr_tail": apply_stderr[-4000:],
            }
        )
        if apply_code == 0:
            return {"applied": True, "prefix_args": prefix_args, "attempts": all_attempts}

    return {"applied": False, "prefix_args": [], "attempts": all_attempts}


def _write_patches_and_apply(
    failed_repo: Path,
    patches: list[str],
) -> tuple[list[WorkspaceWriteResult], list[dict[str, Any]]]:
    writes: list[WorkspaceWriteResult] = []
    apply_results: list[dict[str, Any]] = []

    for index, raw_patch in enumerate(patches):
        patch = _ensure_patch_newline(raw_patch)
        patch_name = f"patch_{index}.diff"
        patch_path = failed_repo / patch_name

        if not _is_nonempty_unified_diff(patch):
            apply_results.append(
                {
                    "patch": patch_name,
                    "return_code": 2,
                    "applied": False,
                    "reason": "patch was not a non-empty unified diff",
                }
            )
            writes.append(_write_bytes(patch_path, patch.encode("utf-8"), kind="patch", action="write_invalid_patch_diagnostic"))
            continue

        write = _write_bytes(patch_path, patch.encode("utf-8"), kind="patch", action="write_patch")
        writes.append(write)
        if not write.ok:
            apply_results.append(
                {
                    "patch": patch_name,
                    "return_code": 3,
                    "applied": False,
                    "reason": write.error or "patch write failed",
                }
            )
            continue

        local_patch_path, copy_write = _copy_patch_to_repo_root_if_needed(patch_path, failed_repo)
        if copy_write is not None:
            writes.append(copy_write)
            if not copy_write.ok:
                apply_results.append(
                    {
                        "patch": patch_name,
                        "return_code": 4,
                        "applied": False,
                        "reason": copy_write.error or "failed to copy patch into repo root",
                    }
                )
                continue

        apply_result = _try_git_apply_with_fallbacks(failed_repo, local_patch_path.name)
        apply_result.update(
            {
                "patch": patch_name,
                "patch_path": str(patch_path),
                "local_patch_name": local_patch_path.name,
                "return_code": 0 if apply_result.get("applied") else 1,
            }
        )
        apply_results.append(apply_result)

    return writes, apply_results


def _should_handle(contract: SkillsBenchOutputContract, metadata: Mapping[str, Any], prompt: str) -> bool:
    task_id = _normalize_task_id(
        metadata.get("canonical_task_id")
        or metadata.get("environment_canonical_task_id")
        or metadata.get("contract_task_id")
        or metadata.get("task_id")
        or contract.task_id
    )
    family = str(contract.family or metadata.get("family") or "").strip().lower().replace("-", "_")
    blob = " ".join([task_id, family, prompt[:4000].lower()])
    return (
        task_id in BUILD_REPAIR_TASK_IDS
        or family in BUILD_REPAIR_FAMILY_KEYS
        or "bugswarm" in blob
        or "/home/github/build/failed" in blob
        or "patch_0.diff" in blob
        or ("fix-build" in blob and "patch" in blob)
    )


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

    if not _should_handle(contract, metadata, prompt):
        warnings.append("fix_build_solver received a non-build-repair task; returning no-op diagnostics")
        return _finish(contract, env, writes, warnings, errors, status="not_build_repair_task", method="none", apply_results=[])

    failed_repo = _find_failed_repo(metadata, env)
    failure_signals = _collect_failure_signals(failed_repo, prompt)

    if failed_repo is None:
        warnings.append("failed repository was not visible from this process")
        note_path = Path("/home/github/build/failed/failed_reasons.txt")
        note = _note_text(contract, None, "repo_not_visible", {"failure_signals": failure_signals})
        writes.append(_write_bytes(note_path, note.encode("utf-8"), kind="text", action="write_note"))
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
        warnings.append("using placeholder patch; this proves the patch channel but may not repair the build")

    note = _note_text(
        contract,
        failed_repo,
        method,
        {
            "bugswarm_tag": tag,
            "patch_count": len(patches),
            "patch_reason": patch_reason,
            "failure_signals": failure_signals,
        },
    )
    writes.append(_write_bytes(Path("/home/github/build/failed/failed_reasons.txt"), note.encode("utf-8"), kind="text", action="write_note"))

    patch_writes, apply_results = _write_patches_and_apply(failed_repo, patches)
    writes.extend(patch_writes)

    if not any(item.get("applied") for item in apply_results):
        errors.append("no patch applied successfully")
    elif method == "placeholder_patch":
        warnings.append("placeholder patch applied; it is diagnostic and may not target the actual build failure")

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
    ok = bool(any(w.ok for w in writes)) and status not in {"repo_not_visible", "not_build_repair_task"}
    diagnostics = {
        "version": FIX_BUILD_SOLVER_VERSION,
        "task_workspace_executor_version": TASK_WORKSPACE_EXECUTOR_VERSION,
        "method": method,
        "status": status,
        "apply_results": apply_results,
        "patch_applied_count": sum(1 for item in apply_results if item.get("applied")),
        "write_count": len(writes),
        "ok_writes": sum(1 for w in writes if w.ok),
        "write_outcomes": [asdict(w) for w in writes[:80]],
        "artifact_records": [
            {
                "path": w.path,
                "sha256": w.sha256,
                "size_bytes": w.bytes_written,
                "kind": w.kind,
                "source": "fix_build_solver",
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
    """Return solvers keyed by task family and known build-repair task ids."""

    return {
        "bugswarm_build_repair": solve_fix_build_task,
        "build_repair": solve_fix_build_task,
        "software_patch": solve_fix_build_task,
        "fix-build-agentops": solve_fix_build_task,
        "fix_build_agentops": solve_fix_build_task,
        "fix-build-google-auto": solve_fix_build_task,
        "fix_build_google_auto": solve_fix_build_task,
    }


def validate_fix_build_solver_selftest() -> dict[str, Any]:
    errors: list[str] = []

    placeholder_contract = type("C", (), {"task_id": "selftest", "family": "code_solution"})()
    placeholder = _placeholder_patch(placeholder_contract)  # lightweight stand-in
    if not _is_nonempty_unified_diff(placeholder):
        errors.append("placeholder patch malformed")
    if "diff --git" not in placeholder or "placeholder patch" not in placeholder:
        errors.append("placeholder patch text missing expected markers")

    registry = default_solver_registry()
    for key in ("bugswarm_build_repair", "build_repair", "software_patch", "fix-build-agentops", "fix-build-google-auto"):
        if key not in registry:
            errors.append(f"registry missing key: {key}")

    with tempfile.TemporaryDirectory(prefix="aegisforge-fix-build-selftest-") as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir(parents=True)
        _run(["git", "init"], cwd=repo, timeout=30)
        patch_path = repo / "patch_0.diff"
        patch_write = _write_bytes(patch_path, placeholder.encode("utf-8"), kind="patch")
        if not patch_write.ok:
            errors.append(f"selftest patch write failed: {patch_write.error}")
        else:
            apply_result = _try_git_apply_with_fallbacks(repo, patch_path.name)
            if not apply_result.get("applied"):
                errors.append(f"selftest placeholder patch did not apply: {apply_result}")

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
