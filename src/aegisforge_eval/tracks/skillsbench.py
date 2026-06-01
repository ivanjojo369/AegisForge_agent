from __future__ import annotations

"""Offline SkillsBench track helpers for AegisForge.

This module is intentionally local/evaluation-only.  It does not call the real
SkillsBench leaderboard and does not consume API credits.  Its purpose is to
simulate the most important contract signal we saw in the logs:

- a task may require evaluator-visible artifacts;
- prose-only responses should not be treated as sufficient for file tasks;
- artifact_refs must not remain empty for artifact-native tasks.

The runtime artifact bridge still belongs in src/aegisforge/executor.py.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


SKILLSBENCH_TRACK = "skillsbench"

SKILLSBENCH_CATEGORIES = (
    "software-engineering",
    "office-white-collar",
    "natural-science",
    "industrial-physical-systems",
    "media-content-production",
    "finance-economics",
    "mathematics-or-formal-reasoning",
    "cybersecurity",
)

ARTIFACT_NATIVE_TASK_HINTS = (
    "fix-build",
    "xlsx",
    "excel",
    "spreadsheet",
    "pptx",
    "presentation",
    "slides",
    "docx",
    "document",
    "pdf",
    "anonymizer",
    "threejs",
    "obj",
    "video",
    "audio",
    "lean4",
    "proof",
    "dependency-audit",
    "patch",
    "diff",
)


@dataclass(slots=True)
class SkillsBenchTask:
    task_id: str
    category: str = "general"
    instruction: str = ""
    tags: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    expected_output: str = ""
    requires_artifact: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "track": SKILLSBENCH_TRACK,
            "task_id": self.task_id,
            "category": self.category,
            "instruction": self.instruction,
            "tags": list(self.tags),
            "files": list(self.files),
            "expected_output": self.expected_output,
            "requires_artifact": self.requires_artifact,
        }


@dataclass(slots=True)
class SkillsBenchLocalCheck:
    passed: bool
    score: float
    reason: str
    artifact_refs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "reason": self.reason,
            "artifact_refs": list(self.artifact_refs),
            "warnings": list(self.warnings),
        }


def normalize_task(payload: Mapping[str, Any]) -> SkillsBenchTask:
    task_id = str(
        payload.get("task_id")
        or payload.get("id")
        or payload.get("name")
        or "skillsbench_local_task"
    ).strip()

    category = str(payload.get("category") or payload.get("task_category") or "general").strip()

    instruction = str(
        payload.get("instruction")
        or payload.get("instructions")
        or payload.get("prompt")
        or payload.get("question")
        or ""
    )

    tags_raw = payload.get("tags") or []
    if isinstance(tags_raw, str):
        tags = [tags_raw]
    elif isinstance(tags_raw, list):
        tags = [str(x) for x in tags_raw if str(x).strip()]
    else:
        tags = []

    files_raw = payload.get("files") or payload.get("attachments") or payload.get("input_files") or []
    if isinstance(files_raw, str):
        files = [files_raw]
    elif isinstance(files_raw, list):
        files = [str(x) for x in files_raw if str(x).strip()]
    else:
        files = []

    expected_output = str(
        payload.get("expected_output")
        or payload.get("output_format")
        or payload.get("deliverable")
        or ""
    )

    requires_artifact = bool(
        payload.get("requires_artifact")
        or payload.get("artifact_required")
        or payload.get("has_skills")
        or _looks_artifact_native(task_id, category, instruction, tags, files, expected_output)
    )

    return SkillsBenchTask(
        task_id=task_id,
        category=category,
        instruction=instruction,
        tags=tags,
        files=files,
        expected_output=expected_output,
        requires_artifact=requires_artifact,
    )


def infer_expected_artifact_kind(task: SkillsBenchTask) -> str:
    blob = " ".join(
        [
            task.task_id,
            task.category,
            task.instruction,
            task.expected_output,
            " ".join(task.tags),
            " ".join(task.files),
        ]
    ).lower()

    if "xlsx" in blob or "excel" in blob or "spreadsheet" in blob:
        return "xlsx"
    if "pptx" in blob or "presentation" in blob or "slides" in blob:
        return "pptx"
    if "docx" in blob or "offer-letter" in blob:
        return "docx"
    if "pdf" in blob or "anonymizer" in blob:
        return "pdf"
    if "lean4" in blob or "lean" in blob or "proof" in blob:
        return "lean"
    if "threejs" in blob or " obj" in blob or blob.endswith("obj"):
        return "obj"
    if "audio" in blob or "audiobook" in blob or "mp3" in blob or "wav" in blob:
        return "audio"
    if "video" in blob:
        return "video"
    if "patch" in blob or "diff" in blob or "fix-build" in blob:
        return "patch"
    if "json" in blob:
        return "json"
    if "csv" in blob:
        return "csv"
    return "text"


def score_local_result(
    task_payload: Mapping[str, Any],
    result_payload: Mapping[str, Any],
    *,
    workspace: str | Path | None = None,
) -> SkillsBenchLocalCheck:
    task = normalize_task(task_payload)

    artifact_refs = _extract_artifact_refs(result_payload)
    warnings: list[str] = []

    if task.requires_artifact and not artifact_refs:
        return SkillsBenchLocalCheck(
            passed=False,
            score=0.0,
            reason="artifact_required_but_artifact_refs_empty",
            artifact_refs=[],
            warnings=[
                "This mirrors the remote SkillsBench failure pattern: task completed but artifact_refs was empty.",
            ],
        )

    if not task.requires_artifact:
        text = str(result_payload.get("final_text") or result_payload.get("text") or "")
        passed = bool(text.strip() or artifact_refs)
        return SkillsBenchLocalCheck(
            passed=passed,
            score=1.0 if passed else 0.0,
            reason="text_or_artifact_present" if passed else "empty_result",
            artifact_refs=artifact_refs,
        )

    if workspace is not None:
        missing = [
            ref for ref in artifact_refs
            if not _artifact_exists(ref, Path(workspace))
        ]
        if missing:
            warnings.append(f"Some artifact refs do not exist under workspace: {missing[:5]}")

    expected_kind = infer_expected_artifact_kind(task)
    if expected_kind != "text" and not _artifact_kind_present(artifact_refs, expected_kind):
        warnings.append(f"Expected artifact kind '{expected_kind}' was not obvious from artifact_refs.")

    return SkillsBenchLocalCheck(
        passed=True,
        score=1.0 if not warnings else 0.7,
        reason="artifact_refs_present",
        artifact_refs=artifact_refs,
        warnings=warnings,
    )


def build_smoke_tasks() -> list[SkillsBenchTask]:
    return [
        SkillsBenchTask(
            task_id="fix-build-agentops",
            category="software-engineering",
            instruction="Repair a failing build and return a minimal patch.",
            tags=["patch", "software-engineering"],
            expected_output="patch",
            requires_artifact=True,
        ),
        SkillsBenchTask(
            task_id="xlsx-recover-data",
            category="office-white-collar",
            instruction="Recover spreadsheet data and return an xlsx deliverable.",
            tags=["xlsx", "spreadsheet"],
            expected_output="xlsx",
            requires_artifact=True,
        ),
        SkillsBenchTask(
            task_id="pptx-reference-formatting",
            category="office-white-collar",
            instruction="Format references in a slide deck and return a pptx file.",
            tags=["pptx", "slides"],
            expected_output="pptx",
            requires_artifact=True,
        ),
        SkillsBenchTask(
            task_id="lean4-proof",
            category="mathematics-or-formal-reasoning",
            instruction="Complete the Lean 4 proof and return a .lean file.",
            tags=["lean4", "proof"],
            expected_output="lean",
            requires_artifact=True,
        ),
    ]


def _looks_artifact_native(*values: Any) -> bool:
    blob = " ".join(_flatten_text(value) for value in values).lower()
    return any(hint in blob for hint in ARTIFACT_NATIVE_TASK_HINTS)


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return " ".join(str(k) + " " + _flatten_text(v) for k, v in value.items())
    if isinstance(value, list | tuple | set):
        return " ".join(_flatten_text(v) for v in value)
    return str(value)


def _extract_artifact_refs(result_payload: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []

    direct = result_payload.get("artifact_refs")
    if isinstance(direct, list):
        refs.extend(str(x) for x in direct if str(x).strip())

    artifacts = result_payload.get("artifacts")
    if isinstance(artifacts, list):
        for item in artifacts:
            if isinstance(item, Mapping):
                for key in ("ref", "path", "name", "filename", "uri"):
                    value = item.get(key)
                    if value:
                        refs.append(str(value))
                        break
            elif item:
                refs.append(str(item))

    # Stable de-duplication.
    seen: set[str] = set()
    out: list[str] = []
    for ref in refs:
        clean = ref.strip()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def _artifact_exists(ref: str, workspace: Path) -> bool:
    path = Path(ref)
    if path.is_absolute():
        return path.exists()
    return (workspace / path).exists()


def _artifact_kind_present(refs: list[str], expected_kind: str) -> bool:
    suffixes = {
        "xlsx": (".xlsx",),
        "pptx": (".pptx",),
        "docx": (".docx",),
        "pdf": (".pdf",),
        "lean": (".lean",),
        "obj": (".obj",),
        "audio": (".wav", ".mp3", ".m4a", ".ogg"),
        "video": (".mp4", ".mov", ".webm", ".mkv"),
        "patch": (".patch", ".diff"),
        "json": (".json",),
        "csv": (".csv",),
        "text": (".txt", ".md"),
    }.get(expected_kind, (f".{expected_kind}",))

    lowered = [ref.lower() for ref in refs]
    return any(ref.endswith(suffixes) for ref in lowered)
