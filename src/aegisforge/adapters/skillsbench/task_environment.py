from __future__ import annotations

"""SkillsBench task-environment discovery for AegisForge.

This module answers the question that artifact_refs probes could not answer:
Can the running AegisForge process see and write the real task filesystem?

SkillsBench/Harbor tasks are evaluated by files written to task-specific paths
such as /root/answer.json, /root/output/*.csv, /app/workspace/solution.py, or
/home/github/build/failed/<repo>/<id>/patch_0.diff. A2A artifacts are useful
for diagnostics, but the verifier usually checks those filesystem outputs.

Design constraints:
- no network access;
- no shell execution;
- no destructive writes;
- no secret printing;
- bounded directory scans;
- safe JSON-serializable diagnostics.
"""

from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping
import json
import os
import re
import tempfile
import time


TASK_ENVIRONMENT_VERSION = "skillsbench_task_environment_v0_1_filesystem_discovery_2026_06_02"


KNOWN_TASK_ROOTS: tuple[str, ...] = (
    "/root",
    "/root/output",
    "/root/workspace",
    "/root/data",
    "/root/input",
    "/root/patches",
    "/app/workspace",
    "/app/data",
    "/app/output",
    "/app/video",
    "/data",
    "/output",
    "/workspace",
    "/home/github/build",
    "/home/github/build/failed",
    "/home/github/build/passed",
    "/logs",
    "/logs/verifier",
)

ENV_PATH_KEYS: tuple[str, ...] = (
    "PWD",
    "HOME",
    "WORKSPACE",
    "GITHUB_WORKSPACE",
    "SKILLSBENCH_OUTPUT_DIR",
    "AEGISFORGE_SKILLSBENCH_OUTPUT_DIR",
    "AMBER_OUTPUT_DIR",
    "BENCHFLOW_OUTPUT_DIR",
    "TASK_OUTPUT_DIR",
    "OUTPUT_DIR",
    "RESULTS_DIR",
    "REPO_ID",
)

ENV_SIGNAL_KEYS: tuple[str, ...] = (
    "AEGISFORGE_FORCE_SKILLSBENCH",
    "AEGISFORGE_ENABLE_SKILLSBENCH",
    "AEGISFORGE_TRACK",
    "AEGISFORGE_BENCHMARK",
    "AEGISFORGE_ADAPTER",
    "AEGISFORGE_TASK_SET",
    "AEGISFORGE_CONDITION",
    "AEGISFORGE_SCENARIO_FAMILY",
    "AEGISFORGE_OUTPUT_PROTOCOL",
    "AGENT_OUTPUT_PROTOCOL",
    "REPO_ID",
    "bugswarm_image_tag",
    "BUGSWARM_IMAGE_TAG",
)

OUTPUT_PATH_RE = re.compile(
    r"(?P<quote>[`\"']?)"
    r"(?P<path>/(?:root|app|data|output|workspace|home/github/build|logs)"
    r"[A-Za-z0-9_./{}<>:+@%=\- ]{0,240}?"
    r"(?:\.json|\.csv|\.txt|\.md|\.py|\.xlsx|\.xls|\.pptx|\.docx|\.pdf|\.dxf|\.zip|\.diff|\.lean|\.yaml|\.yml|/))"
    r"(?P=quote)"
)

PLACEHOLDER_RE = re.compile(r"<[^>/\s]+>|\{[^}/\s]+\}")


@dataclass(frozen=True)
class PathProbe:
    """Bounded facts about one candidate task path."""

    path: str
    exists: bool
    is_dir: bool
    is_file: bool
    readable: bool
    writable_os_access: bool
    executable_os_access: bool
    parent_exists: bool
    parent_writable_os_access: bool
    mkdir_possible: bool = False
    write_probe_attempted: bool = False
    write_probe_ok: bool = False
    error: str = ""
    sample_children: tuple[str, ...] = field(default_factory=tuple)
    suffix_counts: dict[str, int] = field(default_factory=dict)
    file_count_sampled: int = 0

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["sample_children"] = list(self.sample_children)
        return data


@dataclass(frozen=True)
class OutputPathCandidate:
    """A path the task instruction appears to require."""

    path: str
    source: str
    kind: str
    parent: str
    has_placeholder: bool
    absolute: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SkillsBenchTaskEnvironment:
    """Complete filesystem discovery result."""

    version: str
    timestamp: float
    cwd: str
    home: str
    task_id: str
    category: str
    family_hint: str
    env_signals: dict[str, str]
    path_probes: tuple[PathProbe, ...]
    output_candidates: tuple[OutputPathCandidate, ...]
    best_output_roots: tuple[str, ...]
    can_access_task_filesystem: bool
    can_write_known_output: bool
    likely_isolated_a2a_container: bool
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path_probes"] = [probe.as_dict() for probe in self.path_probes]
        data["output_candidates"] = [item.as_dict() for item in self.output_candidates]
        data["best_output_roots"] = list(self.best_output_roots)
        data["notes"] = list(self.notes)
        return data

    def as_context(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "task_id": self.task_id,
            "category": self.category,
            "family_hint": self.family_hint,
            "cwd": self.cwd,
            "home": self.home,
            "can_access_task_filesystem": self.can_access_task_filesystem,
            "can_write_known_output": self.can_write_known_output,
            "likely_isolated_a2a_container": self.likely_isolated_a2a_container,
            "best_output_roots": list(self.best_output_roots),
            "output_candidates": [item.as_dict() for item in self.output_candidates[:40]],
            "path_probes": [probe.as_dict() for probe in self.path_probes],
            "notes": list(self.notes),
        }

    def probe_for(self, path: str) -> PathProbe | None:
        normalized = str(Path(path))
        for probe in self.path_probes:
            if probe.path == normalized or probe.path == path:
                return probe
        return None

    def first_existing_root(self) -> str:
        for root in self.best_output_roots:
            probe = self.probe_for(root)
            if probe and probe.exists:
                return root
        for probe in self.path_probes:
            if probe.exists and probe.is_dir:
                return probe.path
        return self.cwd

    def can_write_path(self, target_path: str) -> bool:
        """Conservative check for whether a target path is plausibly writable."""

        try:
            target = Path(target_path)
            parent = target if str(target_path).endswith("/") else target.parent
            parent_s = str(parent)
            direct = self.probe_for(parent_s)
            if direct is not None:
                return bool(direct.exists and direct.is_dir and (direct.write_probe_ok or direct.writable_os_access))
            for probe in self.path_probes:
                if probe.exists and probe.is_dir and parent_s.startswith(probe.path.rstrip("/") + "/"):
                    return bool(probe.write_probe_ok or probe.writable_os_access)
        except Exception:
            return False
        return False


def _safe_text(value: Any, *, limit: int = 50000) -> str:
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


def _sanitize_env_value(value: str, *, limit: int = 300) -> str:
    """Keep signals useful while avoiding accidental secret disclosure."""

    value = str(value or "")
    if not value:
        return ""
    lowered = value.lower()
    if any(token in lowered for token in ("sk-", "token", "secret", "password", "apikey", "api_key")):
        return "<redacted>"
    if len(value) > limit:
        return value[:limit] + "...<truncated>"
    return value


def env_snapshot() -> dict[str, str]:
    out: dict[str, str] = {}
    keys = list(dict.fromkeys([*ENV_SIGNAL_KEYS, *ENV_PATH_KEYS]))
    for key in keys:
        raw = os.getenv(key)
        if raw is not None:
            out[key] = _sanitize_env_value(raw)
    return out


def extract_output_path_candidates(text: Any, metadata: Mapping[str, Any] | None = None) -> list[OutputPathCandidate]:
    """Extract filesystem output paths from task text and metadata.

    This is intentionally lightweight. output_contract.py will later own the
    full route/schema extraction. task_environment.py only needs enough path
    detection to know which roots matter.
    """

    metadata = _safe_mapping(metadata)
    blobs: list[tuple[str, str]] = [("prompt", _safe_text(text))]
    for key in ("instruction", "description", "prompt", "task", "task_text"):
        value = metadata.get(key)
        if value:
            blobs.append((f"metadata.{key}", _safe_text(value)))

    candidates: list[OutputPathCandidate] = []
    seen: set[str] = set()
    for source, blob in blobs:
        for match in OUTPUT_PATH_RE.finditer(blob):
            raw = match.group("path").strip().rstrip(".,);]")
            if not raw.startswith("/"):
                continue
            has_placeholder = bool(PLACEHOLDER_RE.search(raw))
            path = raw
            parent = str(Path(path).parent)
            if has_placeholder:
                safe_parts: list[str] = []
                for part in Path(path).parts:
                    if PLACEHOLDER_RE.search(part):
                        break
                    safe_parts.append(part)
                parent = str(Path(*safe_parts)) if safe_parts else "/"
            suffix = Path(path).suffix.lower()
            if suffix in {".json", ".csv", ".txt", ".md", ".py", ".xlsx", ".xls", ".pptx", ".docx", ".pdf", ".dxf", ".zip", ".diff", ".lean", ".yaml", ".yml"}:
                kind = suffix.lstrip(".")
            else:
                kind = "directory" if raw.endswith("/") else "path"
            key = f"{path}|{source}"
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                OutputPathCandidate(
                    path=path,
                    source=source,
                    kind=kind,
                    parent=parent,
                    has_placeholder=has_placeholder,
                )
            )
    return candidates[:120]


def _candidate_roots_from_env_and_outputs(
    metadata: Mapping[str, Any],
    output_candidates: Iterable[OutputPathCandidate],
) -> list[str]:
    roots: list[str] = list(KNOWN_TASK_ROOTS)

    env = env_snapshot()
    for key in ENV_PATH_KEYS:
        value = env.get(key)
        if not value:
            continue
        if key == "REPO_ID":
            roots.append(f"/home/github/build/failed/{value}")
            roots.append(f"/home/github/build/passed/{value}")
        elif value.startswith("/"):
            roots.append(value)

    repo_id = str(metadata.get("REPO_ID") or metadata.get("repo_id") or metadata.get("repo") or "").strip()
    if repo_id and "/" in repo_id:
        roots.append(f"/home/github/build/failed/{repo_id}")
        roots.append(f"/home/github/build/passed/{repo_id}")

    for candidate in output_candidates:
        roots.append(candidate.parent)
        parts = Path(candidate.path).parts
        if len(parts) >= 2:
            roots.append("/".join(parts[:2]) or "/")
        if len(parts) >= 3:
            roots.append("/".join(parts[:3]))

    deduped: list[str] = []
    seen: set[str] = set()
    for raw in roots:
        try:
            path = str(Path(raw))
        except Exception:
            continue
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped[:160]


def _sample_directory(path: Path, *, max_children: int = 16, max_files: int = 80) -> tuple[tuple[str, ...], dict[str, int], int, str]:
    try:
        if not path.exists() or not path.is_dir():
            return tuple(), {}, 0, ""
        children: list[str] = []
        suffixes: Counter[str] = Counter()
        file_count = 0
        for index, child in enumerate(path.iterdir()):
            if index < max_children:
                children.append(child.name + ("/" if child.is_dir() else ""))
            if child.is_file():
                file_count += 1
                suffixes[child.suffix.lower() or "<none>"] += 1
            if index >= max_files:
                break
        return tuple(children), dict(sorted(suffixes.items())), file_count, ""
    except Exception as exc:
        return tuple(), {}, 0, str(exc)[:300]


def _probe_write(path: Path) -> tuple[bool, str]:
    """Try a tiny write/delete inside a directory."""

    try:
        if not path.exists() or not path.is_dir():
            return False, "not-existing-directory"
        with tempfile.NamedTemporaryFile(prefix=".aegisforge_probe_", suffix=".tmp", dir=str(path), delete=False) as handle:
            probe_name = handle.name
            handle.write(b"aegisforge skillsbench write probe\n")
        try:
            Path(probe_name).unlink(missing_ok=True)
        except TypeError:
            if Path(probe_name).exists():
                Path(probe_name).unlink()
        return True, ""
    except Exception as exc:
        return False, str(exc)[:300]


def probe_path(raw_path: str, *, write_probe: bool = False, sample: bool = True) -> PathProbe:
    path = Path(raw_path)
    error = ""
    try:
        exists = path.exists()
        is_dir = path.is_dir()
        is_file = path.is_file()
        parent = path.parent
        parent_exists = parent.exists()
        readable = os.access(str(path), os.R_OK) if exists else False
        writable = os.access(str(path), os.W_OK) if exists else False
        executable = os.access(str(path), os.X_OK) if exists else False
        parent_writable = os.access(str(parent), os.W_OK) if parent_exists else False
        mkdir_possible = bool((not exists) and parent_exists and parent_writable)
    except Exception as exc:
        exists = is_dir = is_file = parent_exists = readable = writable = executable = parent_writable = mkdir_possible = False
        error = str(exc)[:300]

    sample_children: tuple[str, ...] = tuple()
    suffix_counts: dict[str, int] = {}
    file_count_sampled = 0
    if sample and exists and is_dir:
        sample_children, suffix_counts, file_count_sampled, sample_error = _sample_directory(path)
        if sample_error and not error:
            error = sample_error

    write_probe_ok = False
    write_probe_attempted = False
    if write_probe and exists and is_dir:
        write_probe_attempted = True
        write_probe_ok, write_error = _probe_write(path)
        if write_error and not error:
            error = write_error

    return PathProbe(
        path=str(path),
        exists=exists,
        is_dir=is_dir,
        is_file=is_file,
        readable=readable,
        writable_os_access=writable,
        executable_os_access=executable,
        parent_exists=parent_exists,
        parent_writable_os_access=parent_writable,
        mkdir_possible=mkdir_possible,
        write_probe_attempted=write_probe_attempted,
        write_probe_ok=write_probe_ok,
        error=error,
        sample_children=sample_children,
        suffix_counts=suffix_counts,
        file_count_sampled=file_count_sampled,
    )


def _infer_family_hint(metadata: Mapping[str, Any], text: str, output_candidates: Iterable[OutputPathCandidate]) -> str:
    blob = " ".join(
        [
            _safe_text(metadata, limit=20000),
            text[:20000],
            " ".join(candidate.path for candidate in output_candidates),
        ]
    ).lower()

    if "/home/github/build" in blob or "patch_" in blob or "build/failed" in blob or "bugswarm" in blob:
        return "bugswarm_build_repair"
    if ".lean" in blob or "lean4" in blob:
        return "formal_lean"
    if ".pptx" in blob or "slides" in blob:
        return "presentation"
    if ".xlsx" in blob or ".xls" in blob or "excel" in blob:
        return "spreadsheet"
    if ".pdf" in blob or "docx" in blob:
        return "document"
    if ".pcap" in blob or "cve" in blob or "vulnerability" in blob:
        return "security"
    if ".mp4" in blob or "video" in blob or "ocr" in blob or "image" in blob:
        return "media"
    if "/app/workspace" in blob or "/root/workspace" in blob or "solution.py" in blob:
        return "code_workspace"
    if "/root/output" in blob or "/app/output" in blob or "/output" in blob:
        return "file_output_task"
    return "general_task_filesystem"


def discover_task_environment(
    metadata: Mapping[str, Any] | None = None,
    text: Any = "",
    *,
    task_id: str = "",
    write_probe: bool = False,
    sample: bool = True,
) -> SkillsBenchTaskEnvironment:
    """Discover whether AegisForge can see the task filesystem."""

    metadata = _safe_mapping(metadata)
    prompt = _safe_text(text)
    output_candidates = extract_output_path_candidates(prompt, metadata)
    roots = _candidate_roots_from_env_and_outputs(metadata, output_candidates)

    probes = tuple(probe_path(root, write_probe=write_probe, sample=sample) for root in roots)

    existing_dirs = [probe.path for probe in probes if probe.exists and probe.is_dir]
    writable_dirs = [
        probe.path
        for probe in probes
        if probe.exists and probe.is_dir and (probe.write_probe_ok or probe.writable_os_access)
    ]

    task_roots_present = any(
        root in existing_dirs
        for root in (
            "/root",
            "/app/workspace",
            "/home/github/build",
            "/data",
            "/workspace",
        )
    )
    output_roots_present = any(
        root in existing_dirs
        for root in (
            "/root/output",
            "/app/output",
            "/output",
            "/logs/verifier",
            "/home/github/build/failed",
        )
    )

    candidate_parent_writable = False
    for candidate in output_candidates:
        for probe in probes:
            if probe.path == candidate.parent and probe.exists and probe.is_dir and (probe.write_probe_ok or probe.writable_os_access):
                candidate_parent_writable = True
                break
            if candidate.parent.startswith(probe.path.rstrip("/") + "/") and probe.exists and probe.is_dir and (probe.write_probe_ok or probe.writable_os_access):
                candidate_parent_writable = True
                break
        if candidate_parent_writable:
            break

    can_access_task_filesystem = bool(task_roots_present or output_roots_present or (output_candidates and existing_dirs))
    can_write_known_output = bool(candidate_parent_writable or any(root in writable_dirs for root in ("/root/output", "/app/output", "/output", "/home/github/build/failed")))
    likely_isolated = not can_access_task_filesystem

    notes: list[str] = []
    if not output_candidates:
        notes.append("No explicit absolute output paths were extracted from the prompt/metadata.")
    if can_access_task_filesystem:
        notes.append("At least one known SkillsBench/Harbor task root is visible.")
    else:
        notes.append("No known SkillsBench/Harbor task root is visible from this process.")
    if can_write_known_output:
        notes.append("At least one expected output parent/root appears writable.")
    else:
        notes.append("No expected output parent/root appears writable via current checks.")
    if not write_probe:
        notes.append("write_probe disabled; writable status is based mostly on os.access and parent checks.")

    return SkillsBenchTaskEnvironment(
        version=TASK_ENVIRONMENT_VERSION,
        timestamp=time.time(),
        cwd=str(Path.cwd()),
        home=str(Path.home()),
        task_id=str(metadata.get("task_id") or metadata.get("id") or task_id or ""),
        category=str(metadata.get("category") or metadata.get("task_category") or ""),
        family_hint=_infer_family_hint(metadata, prompt, output_candidates),
        env_signals=env_snapshot(),
        path_probes=probes,
        output_candidates=tuple(output_candidates),
        best_output_roots=tuple(writable_dirs[:30] or existing_dirs[:30]),
        can_access_task_filesystem=can_access_task_filesystem,
        can_write_known_output=can_write_known_output,
        likely_isolated_a2a_container=likely_isolated,
        notes=tuple(notes),
    )


def validate_task_environment_selftest() -> dict[str, Any]:
    sample_instruction = """
    Write your findings to `/root/answer.json`.
    Also create /root/output/report.md and /app/workspace/solution.py.
    For build repair write /home/github/build/failed/<repo>/<id>/patch_0.diff.
    """
    env = discover_task_environment(
        {"task_id": "selftest", "category": "software-engineering"},
        sample_instruction,
        write_probe=False,
        sample=False,
    )
    paths = [candidate.path for candidate in env.output_candidates]
    errors: list[str] = []
    for expected in ("/root/answer.json", "/root/output/report.md", "/app/workspace/solution.py"):
        if expected not in paths:
            errors.append(f"missing extracted path: {expected}")
    if env.family_hint not in {"bugswarm_build_repair", "code_workspace", "file_output_task"}:
        errors.append(f"unexpected family_hint: {env.family_hint}")
    return {
        "ok": not errors,
        "errors": errors,
        "version": TASK_ENVIRONMENT_VERSION,
        "candidate_count": len(env.output_candidates),
        "probe_count": len(env.path_probes),
        "family_hint": env.family_hint,
        "can_access_task_filesystem": env.can_access_task_filesystem,
        "can_write_known_output": env.can_write_known_output,
        "likely_isolated_a2a_container": env.likely_isolated_a2a_container,
        "sample_candidates": [candidate.as_dict() for candidate in env.output_candidates[:8]],
    }


__all__ = [
    "TASK_ENVIRONMENT_VERSION",
    "PathProbe",
    "OutputPathCandidate",
    "SkillsBenchTaskEnvironment",
    "discover_task_environment",
    "env_snapshot",
    "extract_output_path_candidates",
    "probe_path",
    "validate_task_environment_selftest",
]
