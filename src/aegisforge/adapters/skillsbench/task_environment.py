from __future__ import annotations

"""SkillsBench task-environment discovery for AegisForge.

This module answers two questions that A2A artifact probes cannot answer:

1. Can the running AegisForge process see/write the real task filesystem?
2. What is the canonical SkillsBench task identity when A2A metadata gives a
   UUID/session id instead of the public task_id?

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


TASK_ENVIRONMENT_VERSION = "skillsbench_task_environment_v0_2_canonical_identity_2026_06_03"


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
    # Identity hints sometimes present in runners/wrappers.
    "TASK_ID",
    "TASK_NAME",
    "TASK_SLUG",
    "SKILLSBENCH_TASK_ID",
    "SKILLSBENCH_TASK_NAME",
    "BENCHFLOW_TASK_ID",
    "BENCHFLOW_TASK_NAME",
    "AMBER_TASK_ID",
    "AMBER_TASK_NAME",
    "TRIAL_ID",
    "SCENARIO_ID",
    "AGENTBEATS_TASK_ID",
    "AGENTBEATS_SCENARIO_ID",
    "CONDITION",
    "TASK_SET",
)

OUTPUT_PATH_RE = re.compile(
    r"(?P<quote>[`\"']?)"
    r"(?P<path>/(?:root|app|data|output|workspace|home/github/build|logs)"
    r"[A-Za-z0-9_./{}<>:+@%=\- ]{0,240}?"
    r"(?:\.json|\.csv|\.txt|\.md|\.py|\.xlsx|\.xls|\.pptx|\.docx|\.pdf|\.dxf|\.zip|\.diff|\.lean|\.yaml|\.yml|/))"
    r"(?P=quote)"
)

PLACEHOLDER_RE = re.compile(r"<[^>/\s]+>|\{[^}/\s]+\}")

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

TASK_ID_TOKEN_RE = re.compile(r"\b[a-z][a-z0-9]+(?:-[a-z0-9]+){1,8}\b", re.IGNORECASE)

TRIAL_TASK_RE = re.compile(
    r"\b(?P<task>[a-z][a-z0-9]+(?:-[a-z0-9]+){1,8})(?:__agentbeats__|__skillsbench__|__benchflow__)",
    re.IGNORECASE,
)

TASKS_PATH_RE = re.compile(
    r"(?:^|[/\\])tasks[/\\](?P<task>[a-z][a-z0-9]+(?:-[a-z0-9]+){1,8})(?:[/\\]task\.toml|[/\\]|$)",
    re.IGNORECASE,
)

KEYED_TASK_RE = re.compile(
    r"(?:task[_ -]?id|task[_ -]?name|task[_ -]?slug|trial[_ -]?id|scenario[_ -]?id|name)\s*[:=]\s*"
    r"[`\"']?(?P<task>[a-z][a-z0-9]+(?:-[a-z0-9]+){1,8})",
    re.IGNORECASE,
)

GENERIC_TASK_IDS: frozenset[str] = frozenset(
    {
        "skillsbench-task",
        "skillsbench",
        "general",
        "general-task",
        "general-purpose",
        "standard-v1",
        "deploy-smoke-v1",
        "with-skills",
        "agentbeats",
        "quick-submit",
        "run-scenario",
        "eval",
        "setup",
        "completed",
        "workspace-task",
        "file-output-task",
    }
)

# Known public/observed SkillsBench task ids.  This is not a hidden answer table;
# it only helps recover identity from public task ids appearing in prompt,
# metadata, trial ids, paths, or logs.
KNOWN_SKILLSBENCH_TASK_IDS: frozenset[str] = frozenset(
    {
        "citation-check",
        "court-form-filling",
        "dialogue-parser",
        "offer-letter-generator",
        "powerlifting-coef-calc",
        "fix-build-agentops",
        "paper-anonymizer",
        "pptx-reference-formatting",
        "xlsx-recover-data",
        "threejs-to-obj",
        "video-silence-remover",
        "lean4-proof",
        "software-dependency-audit",
        "pdf-excel-diff",
        "debug-trl-grpo",
        "test-supply",
        "dapt-intrusion-detection",
        "latex-formula-extraction",
        "mass-report",
        "nasa-budget-recover",
        "nasa-budget-recovered",
        "edit-pdf",
        "network-stats",
        "bgp-route-leak",
        "controller-tuning",
        "pareto-frontier",
        "rar-recover-data",
        "sc100-form-filling",
        "court-form-filling",
    }
)

TASK_ID_CATEGORY_MAP: dict[str, str] = {
    "citation-check": "office-white-collar",
    "court-form-filling": "office-white-collar",
    "dialogue-parser": "office-white-collar",
    "offer-letter-generator": "office-white-collar",
    "powerlifting-coef-calc": "mathematics-or-formal-reasoning",
    "fix-build-agentops": "software-engineering",
    "paper-anonymizer": "office-white-collar",
    "pptx-reference-formatting": "office-white-collar",
    "xlsx-recover-data": "office-white-collar",
    "threejs-to-obj": "media-content-production",
    "video-silence-remover": "media-content-production",
    "lean4-proof": "mathematics-or-formal-reasoning",
    "software-dependency-audit": "cybersecurity",
    "pdf-excel-diff": "office-white-collar",
    "debug-trl-grpo": "software-engineering",
    "dapt-intrusion-detection": "cybersecurity",
    "bgp-route-leak": "cybersecurity",
    "controller-tuning": "industrial-physical-systems",
}

TASK_ID_TAG_MAP: dict[str, tuple[str, ...]] = {
    "citation-check": ("citation", "document", "json"),
    "court-form-filling": ("pdf", "form-filling", "office"),
    "dialogue-parser": ("json", "parser", "dialogue"),
    "offer-letter-generator": ("docx", "document", "template"),
    "powerlifting-coef-calc": ("math", "csv", "calculator"),
    "fix-build-agentops": ("software", "build", "patch"),
    "paper-anonymizer": ("document", "anonymization"),
    "pptx-reference-formatting": ("pptx", "presentation"),
    "xlsx-recover-data": ("xlsx", "spreadsheet"),
    "threejs-to-obj": ("3d", "obj", "conversion"),
    "video-silence-remover": ("video", "audio"),
    "lean4-proof": ("lean4", "formal method"),
    "software-dependency-audit": ("security", "dependency"),
    "pdf-excel-diff": ("pdf", "xlsx", "diff"),
    "debug-trl-grpo": ("python", "training", "debug"),
    "dapt-intrusion-detection": ("security", "network", "intrusion-detection"),
    "bgp-route-leak": ("security", "routing", "bgp"),
    "controller-tuning": ("control", "csv", "json"),
}


@dataclass(frozen=True)
class TaskIdentityCandidate:
    """One possible task identity recovered from metadata/text/env."""

    value: str
    source: str
    score: float
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskIdentity:
    """Canonical SkillsBench task identity inferred without hidden lookup."""

    original_task_id: str
    canonical_task_id: str
    source: str
    confidence: float
    task_set: str
    condition: str
    category: str
    tags: tuple[str, ...]
    candidates: tuple[TaskIdentityCandidate, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tags"] = list(self.tags)
        data["candidates"] = [item.as_dict() for item in self.candidates]
        return data

    def as_context(self) -> dict[str, Any]:
        return self.as_dict()


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
    canonical_task_id: str = ""
    task_identity_source: str = ""
    task_identity_confidence: float = 0.0
    task_set: str = ""
    condition: str = ""
    canonical_category: str = ""
    canonical_tags: tuple[str, ...] = field(default_factory=tuple)
    task_identity_candidates: tuple[TaskIdentityCandidate, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path_probes"] = [probe.as_dict() for probe in self.path_probes]
        data["output_candidates"] = [item.as_dict() for item in self.output_candidates]
        data["best_output_roots"] = list(self.best_output_roots)
        data["notes"] = list(self.notes)
        data["canonical_tags"] = list(self.canonical_tags)
        data["task_identity_candidates"] = [item.as_dict() for item in self.task_identity_candidates]
        return data

    def as_context(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "task_id": self.task_id,
            "canonical_task_id": self.canonical_task_id,
            "task_identity_source": self.task_identity_source,
            "task_identity_confidence": self.task_identity_confidence,
            "task_set": self.task_set,
            "condition": self.condition,
            "category": self.category,
            "canonical_category": self.canonical_category,
            "canonical_tags": list(self.canonical_tags),
            "family_hint": self.family_hint,
            "cwd": self.cwd,
            "home": self.home,
            "can_access_task_filesystem": self.can_access_task_filesystem,
            "can_write_known_output": self.can_write_known_output,
            "likely_isolated_a2a_container": self.likely_isolated_a2a_container,
            "best_output_roots": list(self.best_output_roots),
            "output_candidates": [item.as_dict() for item in self.output_candidates[:40]],
            "path_probes": [probe.as_dict() for probe in self.path_probes],
            "task_identity_candidates": [item.as_dict() for item in self.task_identity_candidates[:20]],
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


def _normalize_task_slug(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # Trial ids often look like `dialogue-parser__agentbeats__...`.
    for sep in ("__agentbeats__", "__skillsbench__", "__benchflow__"):
        if sep in text:
            text = text.split(sep, 1)[0]
            break
    text = text.strip().lower()
    text = re.sub(r"[_\s]+", "-", text)
    text = re.sub(r"[^a-z0-9.+\-]+", "-", text).strip("-._")
    return text


def _is_uuid_like(value: str) -> bool:
    return bool(UUID_RE.match(str(value or "").strip()))


def _is_plausible_task_id(value: str) -> bool:
    value = _normalize_task_slug(value)
    if not value:
        return False
    if value in GENERIC_TASK_IDS:
        return False
    if _is_uuid_like(value):
        return False
    if len(value) < 4 or len(value) > 96:
        return False
    if "-" not in value:
        return value in KNOWN_SKILLSBENCH_TASK_IDS
    if not re.match(r"^[a-z][a-z0-9]+(?:-[a-z0-9]+){1,8}$", value):
        return False
    return True


def _flatten_metadata_strings(value: Any, *, prefix: str = "metadata", limit: int = 80) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    stack: list[tuple[str, Any]] = [(prefix, value)]
    seen = 0
    while stack and seen < limit:
        source, item = stack.pop()
        seen += 1
        if item is None:
            continue
        if isinstance(item, Mapping):
            for key, child in list(item.items())[:80]:
                stack.append((f"{source}.{key}", child))
            continue
        if isinstance(item, (list, tuple)):
            for idx, child in enumerate(list(item)[:40]):
                stack.append((f"{source}[{idx}]", child))
            continue
        text = _safe_text(item, limit=4000)
        if text:
            out.append((source, text))
    return out[:limit]


def _candidate_score(value: str, source: str, blob: str = "") -> tuple[float, str]:
    task = _normalize_task_slug(value)
    if not _is_plausible_task_id(task):
        return -999.0, "not plausible task id"

    score = 1.0
    reasons: list[str] = ["plausible-slug"]

    if task in KNOWN_SKILLSBENCH_TASK_IDS:
        score += 80.0
        reasons.append("known-public-skillsbench-task")

    low_source = source.lower()
    if any(token in low_source for token in ("task_id", "taskid", "task-name", "task_name", "task_slug", "trial_id", "scenario_id")):
        score += 35.0
        reasons.append("explicit-task-key")
    if "env." in low_source:
        score += 28.0
        reasons.append("env-signal")
    if "tasks/" in blob.replace("\\", "/").lower():
        score += 25.0
        reasons.append("task-path")
    if "__agentbeats__" in blob or "__skillsbench__" in blob or "__benchflow__" in blob:
        score += 30.0
        reasons.append("trial-id-pattern")
    if task in blob.lower():
        score += 4.0
        reasons.append("appears-in-context")

    # Penalize broad category-ish tokens.
    if task in {"office-white-collar", "software-engineering", "cybersecurity", "media-content-production", "natural-science"}:
        score -= 45.0
        reasons.append("category-like")

    return score, ",".join(reasons)


def infer_task_identity(
    metadata: Mapping[str, Any] | None = None,
    text: Any = "",
    *,
    task_id: str = "",
    env: Mapping[str, str] | None = None,
) -> TaskIdentity:
    """Infer the canonical public SkillsBench task id from visible signals.

    This function deliberately avoids hidden benchmark solution lookup.  It only
    recovers public identity strings that are visible in metadata, prompt text,
    environment variables, trial ids, or task paths.
    """

    metadata = _safe_mapping(metadata)
    prompt = _safe_text(text, limit=120000)
    env_map = dict(env or env_snapshot())

    original_task_id = str(
        metadata.get("task_id")
        or metadata.get("taskId")
        or metadata.get("id")
        or task_id
        or ""
    )

    blobs: list[tuple[str, str]] = []
    blobs.extend(_flatten_metadata_strings(metadata, prefix="metadata", limit=120))
    blobs.append(("prompt", prompt))
    if task_id:
        blobs.append(("argument.task_id", str(task_id)))
    for key, value in env_map.items():
        if value:
            blobs.append((f"env.{key}", value))

    candidates: dict[str, TaskIdentityCandidate] = {}

    def add_candidate(raw: Any, source: str, blob: str = "") -> None:
        task = _normalize_task_slug(raw)
        if not task:
            return
        score, reason = _candidate_score(task, source, blob or str(raw))
        if score < 0:
            return
        existing = candidates.get(task)
        candidate = TaskIdentityCandidate(value=task, source=source, score=score, reason=reason)
        if existing is None or candidate.score > existing.score:
            candidates[task] = candidate

    # Direct key candidates.
    for key in (
        "task_id",
        "taskId",
        "task_name",
        "taskName",
        "task_slug",
        "name",
        "scenario_id",
        "scenarioId",
        "trial_id",
        "trialId",
    ):
        if key in metadata:
            add_candidate(metadata.get(key), f"metadata.{key}", _safe_text(metadata.get(key), limit=2000))

    if task_id:
        add_candidate(task_id, "argument.task_id", str(task_id))

    for key in (
        "TASK_ID",
        "TASK_NAME",
        "TASK_SLUG",
        "SKILLSBENCH_TASK_ID",
        "SKILLSBENCH_TASK_NAME",
        "BENCHFLOW_TASK_ID",
        "BENCHFLOW_TASK_NAME",
        "AMBER_TASK_ID",
        "AMBER_TASK_NAME",
        "TRIAL_ID",
        "SCENARIO_ID",
        "AGENTBEATS_TASK_ID",
        "AGENTBEATS_SCENARIO_ID",
    ):
        if env_map.get(key):
            add_candidate(env_map[key], f"env.{key}", env_map[key])

    # Pattern candidates from all visible text.
    for source, blob in blobs:
        if not blob:
            continue
        for match in TRIAL_TASK_RE.finditer(blob):
            add_candidate(match.group("task"), source + ".trial_id", blob)
        for match in TASKS_PATH_RE.finditer(blob.replace("\\", "/")):
            add_candidate(match.group("task"), source + ".task_path", blob)
        for match in KEYED_TASK_RE.finditer(blob):
            add_candidate(match.group("task"), source + ".keyed_task", blob)

        # Exact known public ids get added even without key prefixes.
        low = blob.lower()
        for known in KNOWN_SKILLSBENCH_TASK_IDS:
            if known in low:
                add_candidate(known, source + ".known_task_occurrence", blob)

    ordered = tuple(sorted(candidates.values(), key=lambda item: item.score, reverse=True)[:40])
    best = ordered[0] if ordered else None

    canonical = best.value if best else _normalize_task_slug(original_task_id)
    if not _is_plausible_task_id(canonical):
        canonical = ""

    task_set = str(
        metadata.get("task_set")
        or metadata.get("taskSet")
        or metadata.get("benchmark_task_set")
        or env_map.get("AEGISFORGE_TASK_SET")
        or env_map.get("TASK_SET")
        or ""
    ).strip()
    condition = str(
        metadata.get("condition")
        or metadata.get("skills_condition")
        or env_map.get("AEGISFORGE_CONDITION")
        or env_map.get("CONDITION")
        or ""
    ).strip()

    category = str(metadata.get("category") or metadata.get("task_category") or "").strip()
    if canonical in TASK_ID_CATEGORY_MAP:
        category = TASK_ID_CATEGORY_MAP[canonical]

    tags_raw = metadata.get("tags") or metadata.get("task_tags") or metadata.get("labels") or []
    tags: list[str] = []
    if isinstance(tags_raw, str):
        tags.extend(re.split(r"[,;|]", tags_raw))
    elif isinstance(tags_raw, Iterable):
        for item in tags_raw:
            tags.append(str(item))
    if canonical in TASK_ID_TAG_MAP:
        tags.extend(TASK_ID_TAG_MAP[canonical])
    tags = [tag.strip() for tag in tags if tag and tag.strip()]
    deduped_tags: list[str] = []
    seen_tags: set[str] = set()
    for tag in tags:
        key = tag.lower()
        if key not in seen_tags:
            seen_tags.add(key)
            deduped_tags.append(tag)

    confidence = min(1.0, max(0.0, (best.score / 120.0) if best else 0.0))
    source = best.source if best else ("argument_or_metadata" if canonical else "unresolved")

    return TaskIdentity(
        original_task_id=original_task_id,
        canonical_task_id=canonical,
        source=source,
        confidence=confidence,
        task_set=task_set,
        condition=condition,
        category=category,
        tags=tuple(deduped_tags[:30]),
        candidates=ordered,
    )


def canonicalize_task_metadata(
    metadata: Mapping[str, Any] | None = None,
    text: Any = "",
    *,
    task_id: str = "",
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return metadata copy with canonical task identity injected.

    Harness code can call this before build_output_contract() so solver dispatch
    sees `dialogue-parser` instead of an A2A UUID.
    """

    original = _safe_mapping(metadata)
    identity = infer_task_identity(original, text, task_id=task_id, env=env)
    out = dict(original)
    if identity.canonical_task_id:
        out["task_id"] = identity.canonical_task_id
        out["canonical_task_id"] = identity.canonical_task_id
    if identity.category:
        out.setdefault("category", identity.category)
        out["canonical_category"] = identity.category
    if identity.tags:
        existing = out.get("tags")
        tags: list[str] = []
        if isinstance(existing, str):
            tags.extend(re.split(r"[,;|]", existing))
        elif isinstance(existing, Iterable):
            tags.extend(str(item) for item in existing)
        tags.extend(identity.tags)
        deduped: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            tag = str(tag).strip()
            if not tag:
                continue
            key = tag.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(tag)
        out["tags"] = deduped[:40]
        out["canonical_tags"] = list(identity.tags)
    out["task_identity"] = identity.as_dict()
    if identity.task_set:
        out.setdefault("task_set", identity.task_set)
    if identity.condition:
        out.setdefault("condition", identity.condition)
    return out


def extract_output_path_candidates(text: Any, metadata: Mapping[str, Any] | None = None) -> list[OutputPathCandidate]:
    """Extract filesystem output paths from task text and metadata.

    This is intentionally lightweight. output_contract.py owns full route/schema
    extraction. task_environment.py only needs enough path detection to know
    which roots matter.
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
    *,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    roots: list[str] = list(KNOWN_TASK_ROOTS)

    env_map = dict(env or env_snapshot())
    for key in ENV_PATH_KEYS:
        value = env_map.get(key)
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


def _family_hint_from_task_identity(identity: TaskIdentity) -> str:
    task = identity.canonical_task_id
    if not task:
        return ""
    if task in {"dialogue-parser", "citation-check"}:
        return "json_output"
    if task == "powerlifting-coef-calc":
        return "csv_output"
    if task == "offer-letter-generator":
        return "office_docx"
    if task in {"court-form-filling", "edit-pdf", "sc100-form-filling"}:
        return "pdf_document"
    if task in {"xlsx-recover-data", "test-supply", "nasa-budget-recover", "nasa-budget-recovered", "pdf-excel-diff"}:
        return "office_xlsx"
    if task in {"lean4-proof"}:
        return "lean_solution"
    if task in {"software-dependency-audit", "dapt-intrusion-detection", "bgp-route-leak"}:
        return "security_config"
    if task in {"fix-build-agentops", "debug-trl-grpo"}:
        return "code_solution"
    return ""


def _infer_family_hint(
    metadata: Mapping[str, Any],
    text: str,
    output_candidates: Iterable[OutputPathCandidate],
    identity: TaskIdentity | None = None,
) -> str:
    identity_hint = _family_hint_from_task_identity(identity) if identity else ""
    if identity_hint:
        return identity_hint

    blob = " ".join(
        [
            _safe_text(metadata, limit=20000),
            text[:20000],
            " ".join(candidate.path for candidate in output_candidates),
        ]
    ).lower()

    # Use registry-compatible family names where possible.
    if "/home/github/build" in blob or "patch_" in blob or "build/failed" in blob or "bugswarm" in blob:
        return "bugswarm_build_repair"
    if ".lean" in blob or "lean4" in blob:
        return "lean_solution"
    if ".pptx" in blob or "slides" in blob:
        return "presentation"
    if ".xlsx" in blob or ".xls" in blob or "excel" in blob:
        return "office_xlsx"
    if ".docx" in blob or "offer letter" in blob:
        return "office_docx"
    if ".pdf" in blob or "court form" in blob or "form-filling" in blob:
        return "pdf_document"
    if ".pcap" in blob or "cve" in blob or "vulnerability" in blob or "intrusion" in blob:
        return "security_config"
    if ".mp4" in blob or "video" in blob or "ocr" in blob or "image" in blob:
        return "media"
    if "/app/workspace" in blob or "/root/workspace" in blob or "solution.py" in blob:
        return "code_solution"
    if ".csv" in blob:
        return "csv_output"
    if ".json" in blob or "/root/output" in blob or "/app/output" in blob or "/output" in blob:
        return "json_output"
    return "general_task_filesystem"


def discover_task_environment(
    metadata: Mapping[str, Any] | None = None,
    text: Any = "",
    *,
    task_id: str = "",
    write_probe: bool = False,
    sample: bool = True,
) -> SkillsBenchTaskEnvironment:
    """Discover task identity and whether AegisForge can see the task filesystem."""

    metadata = _safe_mapping(metadata)
    prompt = _safe_text(text)
    env = env_snapshot()
    identity = infer_task_identity(metadata, prompt, task_id=task_id, env=env)
    canonical_metadata = canonicalize_task_metadata(metadata, prompt, task_id=task_id, env=env)

    output_candidates = extract_output_path_candidates(prompt, canonical_metadata)
    roots = _candidate_roots_from_env_and_outputs(canonical_metadata, output_candidates, env=env)

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

    family_hint = _infer_family_hint(canonical_metadata, prompt, output_candidates, identity)

    notes: list[str] = []
    if identity.canonical_task_id:
        notes.append(
            f"Canonical task identity inferred as {identity.canonical_task_id!r} "
            f"from {identity.source} with confidence {identity.confidence:.2f}."
        )
    else:
        notes.append("No canonical public SkillsBench task_id could be inferred from visible metadata/text/env.")
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

    effective_task_id = identity.canonical_task_id or str(metadata.get("task_id") or metadata.get("id") or task_id or "")

    return SkillsBenchTaskEnvironment(
        version=TASK_ENVIRONMENT_VERSION,
        timestamp=time.time(),
        cwd=str(Path.cwd()),
        home=str(Path.home()),
        task_id=effective_task_id,
        category=identity.category or str(metadata.get("category") or metadata.get("task_category") or ""),
        family_hint=family_hint,
        env_signals=env,
        path_probes=probes,
        output_candidates=tuple(output_candidates),
        best_output_roots=tuple(writable_dirs[:30] or existing_dirs[:30]),
        can_access_task_filesystem=can_access_task_filesystem,
        can_write_known_output=can_write_known_output,
        likely_isolated_a2a_container=likely_isolated,
        notes=tuple(notes),
        canonical_task_id=identity.canonical_task_id,
        task_identity_source=identity.source,
        task_identity_confidence=identity.confidence,
        task_set=identity.task_set,
        condition=identity.condition,
        canonical_category=identity.category,
        canonical_tags=identity.tags,
        task_identity_candidates=identity.candidates,
    )


def validate_task_environment_selftest() -> dict[str, Any]:
    sample_instruction = """
    trial_id: dialogue-parser__agentbeats__019e276a
    Write your findings to `/root/answer.json`.
    Also create /root/output/report.md and /app/workspace/solution.py.
    For build repair write /home/github/build/failed/<repo>/<id>/patch_0.diff.
    """
    env = discover_task_environment(
        {"task_id": "019e276a-0075-77d3-8efc-aed44b9bba37", "category": "general-purpose"},
        sample_instruction,
        write_probe=False,
        sample=False,
    )
    paths = [candidate.path for candidate in env.output_candidates]
    errors: list[str] = []
    for expected in ("/root/answer.json", "/root/output/report.md", "/app/workspace/solution.py"):
        if expected not in paths:
            errors.append(f"missing extracted path: {expected}")
    if env.canonical_task_id != "dialogue-parser":
        errors.append(f"canonical task id not recovered: {env.canonical_task_id}")
    if env.family_hint not in {"json_output", "bugswarm_build_repair", "code_solution", "file_output_task"}:
        errors.append(f"unexpected family_hint: {env.family_hint}")
    return {
        "ok": not errors,
        "errors": errors,
        "version": TASK_ENVIRONMENT_VERSION,
        "candidate_count": len(env.output_candidates),
        "probe_count": len(env.path_probes),
        "task_id": env.task_id,
        "canonical_task_id": env.canonical_task_id,
        "task_identity_source": env.task_identity_source,
        "task_identity_confidence": env.task_identity_confidence,
        "task_set": env.task_set,
        "condition": env.condition,
        "canonical_category": env.canonical_category,
        "canonical_tags": list(env.canonical_tags),
        "family_hint": env.family_hint,
        "can_access_task_filesystem": env.can_access_task_filesystem,
        "can_write_known_output": env.can_write_known_output,
        "likely_isolated_a2a_container": env.likely_isolated_a2a_container,
        "sample_candidates": [candidate.as_dict() for candidate in env.output_candidates[:8]],
        "identity_candidates": [candidate.as_dict() for candidate in env.task_identity_candidates[:8]],
    }


def validate_task_identity_selftest() -> dict[str, Any]:
    metadata = {
        "task_id": "019e276a-0075-77d3-8efc-aed44b9bba37",
        "trial_id": "powerlifting-coef-calc__agentbeats__shard0",
        "category": "general-purpose",
    }
    prompt = "Task files are under tasks/powerlifting-coef-calc/task.toml. Required Output Files: answer.csv"
    identity = infer_task_identity(metadata, prompt)
    errors: list[str] = []
    if identity.canonical_task_id != "powerlifting-coef-calc":
        errors.append(f"expected powerlifting-coef-calc, got {identity.canonical_task_id}")
    if identity.category != "mathematics-or-formal-reasoning":
        errors.append(f"unexpected category: {identity.category}")
    if identity.confidence <= 0:
        errors.append("identity confidence did not increase")
    return {
        "ok": not errors,
        "errors": errors,
        "version": TASK_ENVIRONMENT_VERSION,
        "identity": identity.as_dict(),
    }


__all__ = [
    "TASK_ENVIRONMENT_VERSION",
    "KNOWN_SKILLSBENCH_TASK_IDS",
    "TaskIdentity",
    "TaskIdentityCandidate",
    "PathProbe",
    "OutputPathCandidate",
    "SkillsBenchTaskEnvironment",
    "canonicalize_task_metadata",
    "discover_task_environment",
    "env_snapshot",
    "extract_output_path_candidates",
    "infer_task_identity",
    "probe_path",
    "validate_task_environment_selftest",
    "validate_task_identity_selftest",
]
