#!/usr/bin/env python3
from __future__ import annotations

"""Cross-check SkillsBench task-set metadata against official result rows.

This tool is intentionally standalone and stdlib-only so it can run inside the
AegisForge repo, GitHub Actions artifacts, or a local post-mortem workspace.

Primary use:
    python tools/skillsbench_taskset_crosscheck.py \
      --taskset standard-v1.json \
      --results results/019ead57-c76b-7ce1-93bc-2857c9652f35.json \
      --out artifacts/skillsbench/taskset_crosscheck.json \
      --markdown artifacts/skillsbench/taskset_crosscheck.md

It verifies whether the official SkillsBench/AgentBeats results line up with
public standard-v1 task-set metadata:
- task_id coverage;
- task_digest consistency;
- category/difficulty consistency;
- duplicate result rows;
- score eligibility/pass/reward summaries;
- empty/missing artifact_refs patterns.

The script does not call network APIs, execute external commands, or require
secrets. It only reads local JSON files and writes optional local reports.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import argparse
import json
import sys


SCRIPT_VERSION = "skillsbench_taskset_crosscheck_v0_1_2026_06_09"

JsonDict = dict[str, Any]

TASK_ID_KEYS = (
    "task_id",
    "id",
    "name",
    "slug",
    "task_slug",
    "canonical_task_id",
)
DIGEST_KEYS = (
    "task_digest",
    "digest",
    "sha256",
    "content_digest",
    "task_sha256",
)
CATEGORY_KEYS = (
    "category",
    "domain",
    "task_category",
)
DIFFICULTY_KEYS = (
    "difficulty",
    "level",
    "task_difficulty",
)
TASK_LIST_KEYS = (
    "tasks",
    "items",
    "task_set",
    "taskset",
    "standard_v1",
    "standard-v1",
)


@dataclass(frozen=True)
class TaskSetTask:
    task_id: str
    task_digest: str = ""
    category: str = ""
    difficulty: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    excluded: bool = False
    raw: JsonDict = field(default_factory=dict)

    def as_dict(self) -> JsonDict:
        data = asdict(self)
        data["tags"] = list(self.tags)
        return data


@dataclass(frozen=True)
class ResultRow:
    task_id: str
    task_digest: str = ""
    category: str = ""
    difficulty: str = ""
    score_eligible: bool | None = None
    passed: bool | None = None
    reward: float | None = None
    time_used: float | None = None
    artifact_refs_count: int | None = None
    infra_failure_type: str = ""
    error_type: str = ""
    shard_index: int | None = None
    row_index: int | None = None
    raw: JsonDict = field(default_factory=dict)

    def as_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class TaskComparison:
    task_id: str
    status: str
    taskset: JsonDict | None = None
    result: JsonDict | None = None
    issues: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> JsonDict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "taskset": self.taskset,
            "result": self.result,
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class TasksetCrosscheckReport:
    version: str
    taskset_path: str
    results_path: str
    ok: bool
    summary: JsonDict
    comparisons: tuple[TaskComparison, ...]
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> JsonDict:
        return {
            "version": self.version,
            "taskset_path": self.taskset_path,
            "results_path": self.results_path,
            "ok": self.ok,
            "summary": dict(self.summary),
            "comparisons": [item.as_dict() for item in self.comparisons],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _load_json(path: str | Path) -> Any:
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _dump_json(path: str | Path, payload: Any, *, pretty: bool = True) -> Path:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        payload,
        indent=2 if pretty else None,
        sort_keys=pretty,
        ensure_ascii=False,
        separators=None if pretty else (",", ":"),
    )
    file_path.write_text(text + "\n", encoding="utf-8")
    return file_path


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_text(mapping: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = mapping.get(key)
        text = _text(value)
        if text:
            return text
    return ""


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "pass", "passed", "ok", "success"}:
        return True
    if text in {"0", "false", "no", "n", "fail", "failed", "error"}:
        return False
    return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_list_of_strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple, set)):
        output: list[str] = []
        for item in value:
            text = _text(item)
            if text and text not in output:
                output.append(text)
        return tuple(output)
    text = _text(value)
    return (text,) if text else ()


def _artifact_refs_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, list):
        return len(value)
    if isinstance(value, tuple):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    text = _text(value)
    if not text:
        return 0
    try:
        parsed = json.loads(text)
    except Exception:
        return 1
    if isinstance(parsed, list):
        return len(parsed)
    if isinstance(parsed, dict):
        return len(parsed)
    return 1


def _find_task_list(value: Any) -> list[Any]:
    """Best-effort extraction of task objects from public task-set JSON."""

    if isinstance(value, list):
        return list(value)
    if not isinstance(value, Mapping):
        return []

    for key in ("tasks", "items"):
        candidate = value.get(key)
        if isinstance(candidate, list):
            return list(candidate)

    for key in TASK_LIST_KEYS:
        candidate = value.get(key)
        if isinstance(candidate, Mapping):
            nested = _find_task_list(candidate)
            if nested:
                return nested
        elif isinstance(candidate, list):
            return list(candidate)

    # Fallback: some taskset files use a mapping keyed by task_id.
    mapping_like: list[dict[str, Any]] = []
    for key, child in value.items():
        if isinstance(child, Mapping):
            child_dict = dict(child)
            child_dict.setdefault("task_id", str(key))
            if _first_text(child_dict, TASK_ID_KEYS):
                mapping_like.append(child_dict)
    return mapping_like


def normalize_taskset_tasks(taskset: Any) -> tuple[list[TaskSetTask], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    raw_tasks = _find_task_list(taskset)
    tasks: list[TaskSetTask] = []

    if not raw_tasks:
        errors.append("taskset has no discoverable tasks list")
        return tasks, errors, warnings

    for index, item in enumerate(raw_tasks):
        if not isinstance(item, Mapping):
            warnings.append(f"taskset item {index} is not an object; skipped")
            continue
        raw = dict(item)
        task_id = _first_text(raw, TASK_ID_KEYS)
        if not task_id:
            warnings.append(f"taskset item {index} has no task_id/id/name; skipped")
            continue
        tasks.append(
            TaskSetTask(
                task_id=task_id,
                task_digest=_first_text(raw, DIGEST_KEYS),
                category=_first_text(raw, CATEGORY_KEYS),
                difficulty=_first_text(raw, DIFFICULTY_KEYS),
                tags=_as_list_of_strings(raw.get("tags")),
                excluded=bool(_as_bool(raw.get("excluded")) or _as_bool(raw.get("is_excluded")) or False),
                raw=raw,
            )
        )

    return tasks, errors, warnings


def _is_result_row(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(_first_text(value, TASK_ID_KEYS))


def _normalize_one_result_row(raw: Mapping[str, Any], *, shard_index: int | None, row_index: int | None) -> ResultRow:
    return ResultRow(
        task_id=_first_text(raw, TASK_ID_KEYS),
        task_digest=_first_text(raw, DIGEST_KEYS),
        category=_first_text(raw, CATEGORY_KEYS),
        difficulty=_first_text(raw, DIFFICULTY_KEYS),
        score_eligible=_as_bool(raw.get("score_eligible")),
        passed=_as_bool(raw.get("passed")),
        reward=_as_float(raw.get("reward")),
        time_used=_as_float(raw.get("time_used") if "time_used" in raw else raw.get("duration_seconds")),
        artifact_refs_count=_artifact_refs_count(raw.get("artifact_refs")),
        infra_failure_type=_text(raw.get("infra_failure_type")),
        error_type=_text(raw.get("error_type")),
        shard_index=shard_index,
        row_index=row_index,
        raw=dict(raw),
    )


def normalize_result_rows(results_payload: Any) -> tuple[list[ResultRow], str, list[str], list[str]]:
    """Normalize official results into row objects and classify result shape."""

    errors: list[str] = []
    warnings: list[str] = []

    if isinstance(results_payload, Mapping):
        raw_results = results_payload.get("results")
    else:
        raw_results = results_payload

    if not isinstance(raw_results, list):
        return [], "invalid_missing_or_nonlist_results", ["results is missing or not a list"], warnings

    rows: list[ResultRow] = []
    direct_count = 0
    wrapper_count = 0
    invalid_count = 0

    for outer_index, item in enumerate(raw_results):
        if _is_result_row(item):
            direct_count += 1
            rows.append(_normalize_one_result_row(item, shard_index=None, row_index=outer_index))
            continue

        if isinstance(item, Mapping) and isinstance(item.get("results"), list):
            wrapper_count += 1
            shard_value = item.get("shard_index", item.get("shard", item.get("index", outer_index)))
            try:
                shard_index = int(shard_value)
            except Exception:
                shard_index = outer_index
            for inner_index, child in enumerate(item.get("results") or []):
                if _is_result_row(child):
                    rows.append(_normalize_one_result_row(child, shard_index=shard_index, row_index=inner_index))
                else:
                    invalid_count += 1
                    warnings.append(f"results[{outer_index}].results[{inner_index}] is not a result row")
            continue

        invalid_count += 1
        warnings.append(f"results[{outer_index}] is neither direct row nor shard wrapper")

    if direct_count and wrapper_count:
        shape = "mixed"
    elif wrapper_count:
        shape = "nested_shard"
    elif direct_count:
        shape = "legacy_flat"
    elif invalid_count:
        shape = "invalid_elements"
    else:
        shape = "invalid_empty_results"

    return rows, shape, errors, warnings


def _duplicates(values: Iterable[str]) -> dict[str, int]:
    counts = Counter(v for v in values if v)
    return {key: count for key, count in sorted(counts.items()) if count > 1}


def _row_issue_list(task: TaskSetTask, row: ResultRow) -> list[str]:
    issues: list[str] = []
    if task.task_digest and row.task_digest and task.task_digest != row.task_digest:
        issues.append("task_digest_mismatch")
    if task.category and row.category and task.category != row.category:
        issues.append("category_mismatch")
    if task.difficulty and row.difficulty and task.difficulty != row.difficulty:
        issues.append("difficulty_mismatch")
    if row.artifact_refs_count == 0:
        issues.append("artifact_refs_empty")
    elif row.artifact_refs_count is None:
        issues.append("artifact_refs_missing")
    if row.score_eligible is False:
        issues.append("score_ineligible")
    if row.infra_failure_type:
        issues.append("infra_failure")
    if row.error_type:
        issues.append("error_type_present")
    return issues


def build_taskset_crosscheck(
    taskset_payload: Any,
    results_payload: Any,
    *,
    taskset_path: str = "",
    results_path: str = "",
) -> TasksetCrosscheckReport:
    taskset_tasks, task_errors, task_warnings = normalize_taskset_tasks(taskset_payload)
    result_rows, result_shape, result_errors, result_warnings = normalize_result_rows(results_payload)

    errors = [*task_errors, *result_errors]
    warnings = [*task_warnings, *result_warnings]

    task_by_id: dict[str, TaskSetTask] = {}
    for task in taskset_tasks:
        task_by_id.setdefault(task.task_id, task)

    rows_by_id: dict[str, list[ResultRow]] = defaultdict(list)
    for row in result_rows:
        rows_by_id[row.task_id].append(row)

    task_ids = set(task_by_id)
    result_ids = set(rows_by_id)

    missing_in_results = sorted(task_ids - result_ids)
    extra_in_results = sorted(result_ids - task_ids)
    duplicate_taskset_ids = _duplicates(task.task_id for task in taskset_tasks)
    duplicate_result_ids = {key: len(rows) for key, rows in sorted(rows_by_id.items()) if len(rows) > 1}

    comparisons: list[TaskComparison] = []
    mismatch_counts = Counter()

    for task_id in sorted(task_ids | result_ids):
        task = task_by_id.get(task_id)
        rows = rows_by_id.get(task_id, [])
        if task is None:
            row = rows[0] if rows else None
            comparisons.append(
                TaskComparison(
                    task_id=task_id,
                    status="extra_in_results",
                    result=row.as_dict() if row else None,
                    issues=("extra_in_results",),
                )
            )
            mismatch_counts["extra_in_results"] += 1
            continue

        if not rows:
            comparisons.append(
                TaskComparison(
                    task_id=task_id,
                    status="missing_in_results",
                    taskset=task.as_dict(),
                    issues=("missing_in_results",),
                )
            )
            mismatch_counts["missing_in_results"] += 1
            continue

        row = rows[0]
        issues = _row_issue_list(task, row)
        for issue in issues:
            mismatch_counts[issue] += 1
        if len(rows) > 1:
            issues.append("duplicate_result_rows")
            mismatch_counts["duplicate_result_rows"] += 1

        comparisons.append(
            TaskComparison(
                task_id=task_id,
                status="matched_with_issues" if issues else "matched",
                taskset=task.as_dict(),
                result=row.as_dict(),
                issues=tuple(issues),
            )
        )

    reward_values = [row.reward for row in result_rows if row.reward is not None]
    eligible_rows = [row for row in result_rows if row.score_eligible is True]
    passed_rows = [row for row in result_rows if row.passed is True]
    failed_rows = [row for row in result_rows if row.passed is False]
    zero_reward_rows = [row for row in result_rows if row.reward == 0]

    task_category_counts = Counter(task.category or "unknown" for task in taskset_tasks)
    result_category_counts = Counter(row.category or "unknown" for row in result_rows)
    task_difficulty_counts = Counter(task.difficulty or "unknown" for task in taskset_tasks)
    result_difficulty_counts = Counter(row.difficulty or "unknown" for row in result_rows)
    shard_counts = Counter(str(row.shard_index) if row.shard_index is not None else "flat" for row in result_rows)

    artifact_missing = sum(1 for row in result_rows if row.artifact_refs_count is None)
    artifact_empty = sum(1 for row in result_rows if row.artifact_refs_count == 0)
    artifact_populated = sum(1 for row in result_rows if (row.artifact_refs_count or 0) > 0)

    summary: JsonDict = {
        "result_shape": result_shape,
        "taskset_task_count": len(taskset_tasks),
        "result_row_count": len(result_rows),
        "unique_taskset_task_count": len(task_ids),
        "unique_result_task_count": len(result_ids),
        "missing_in_results_count": len(missing_in_results),
        "extra_in_results_count": len(extra_in_results),
        "duplicate_taskset_ids_count": len(duplicate_taskset_ids),
        "duplicate_result_ids_count": len(duplicate_result_ids),
        "eligible_count": len(eligible_rows),
        "passed_count": len(passed_rows),
        "failed_count": len(failed_rows),
        "zero_reward_count": len(zero_reward_rows),
        "reward_sum": round(sum(reward_values), 6),
        "artifact_refs_missing_count": artifact_missing,
        "artifact_refs_empty_count": artifact_empty,
        "artifact_refs_populated_count": artifact_populated,
        "infra_failure_count": sum(1 for row in result_rows if row.infra_failure_type),
        "error_type_count": sum(1 for row in result_rows if row.error_type),
        "taskset_category_counts": dict(sorted(task_category_counts.items())),
        "result_category_counts": dict(sorted(result_category_counts.items())),
        "taskset_difficulty_counts": dict(sorted(task_difficulty_counts.items())),
        "result_difficulty_counts": dict(sorted(result_difficulty_counts.items())),
        "shard_row_counts": dict(sorted(shard_counts.items())),
        "mismatch_counts": dict(sorted(mismatch_counts.items())),
        "missing_in_results": missing_in_results[:200],
        "extra_in_results": extra_in_results[:200],
        "duplicate_taskset_ids": duplicate_taskset_ids,
        "duplicate_result_ids": duplicate_result_ids,
    }

    if duplicate_taskset_ids:
        errors.append(f"duplicate taskset task_ids: {sorted(duplicate_taskset_ids)}")
    if missing_in_results:
        errors.append(f"{len(missing_in_results)} taskset tasks are missing in official results")
    if extra_in_results:
        errors.append(f"{len(extra_in_results)} official result rows are not in taskset")
    if duplicate_result_ids:
        warnings.append(f"{len(duplicate_result_ids)} task_ids have duplicate official result rows")

    ok = not errors and not any(
        count for key, count in mismatch_counts.items()
        if key in {"task_digest_mismatch", "category_mismatch", "difficulty_mismatch"}
    )

    return TasksetCrosscheckReport(
        version=SCRIPT_VERSION,
        taskset_path=taskset_path,
        results_path=results_path,
        ok=ok,
        summary=summary,
        comparisons=tuple(comparisons),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def render_markdown(report: TasksetCrosscheckReport) -> str:
    summary = report.summary
    lines = [
        "# SkillsBench taskset cross-check",
        "",
        f"- Version: `{report.version}`",
        f"- OK: `{str(report.ok).lower()}`",
        f"- Taskset: `{report.taskset_path or 'inline/selftest'}`",
        f"- Results: `{report.results_path or 'inline/selftest'}`",
        f"- Result shape: `{summary.get('result_shape')}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]

    metric_keys = [
        "taskset_task_count",
        "result_row_count",
        "unique_taskset_task_count",
        "unique_result_task_count",
        "missing_in_results_count",
        "extra_in_results_count",
        "eligible_count",
        "passed_count",
        "failed_count",
        "zero_reward_count",
        "reward_sum",
        "artifact_refs_missing_count",
        "artifact_refs_empty_count",
        "artifact_refs_populated_count",
        "infra_failure_count",
        "error_type_count",
    ]
    for key in metric_keys:
        lines.append(f"| `{key}` | `{summary.get(key, '')}` |")

    lines.extend(["", "## Mismatch counts", ""])
    mismatch_counts = summary.get("mismatch_counts") or {}
    if mismatch_counts:
        lines.extend(["| Issue | Count |", "|---|---:|"])
        for key, value in sorted(mismatch_counts.items()):
            lines.append(f"| `{key}` | `{value}` |")
    else:
        lines.append("No mismatches detected.")

    if report.errors:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {item}" for item in report.errors)
    if report.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in report.warnings)

    interesting = [c for c in report.comparisons if c.issues][:80]
    lines.extend(["", "## First task-level issues", ""])
    if interesting:
        lines.extend(["| task_id | status | issues |", "|---|---|---|"])
        for item in interesting:
            lines.append(f"| `{item.task_id}` | `{item.status}` | `{', '.join(item.issues)}` |")
    else:
        lines.append("No task-level issues detected.")

    lines.append("")
    return "\n".join(lines)


def validate_taskset_crosscheck_selftest() -> JsonDict:
    taskset = {
        "schema_version": "skillsbench.agentbeats.task_set.v1",
        "task_count": 3,
        "tasks": [
            {
                "task_id": "task-a",
                "task_digest": "sha256:a",
                "category": "office-white-collar",
                "difficulty": "easy",
                "tags": ["json"],
            },
            {
                "task_id": "task-b",
                "task_digest": "sha256:b",
                "category": "software-engineering",
                "difficulty": "medium",
            },
            {
                "task_id": "task-c",
                "task_digest": "sha256:c",
                "category": "cybersecurity",
                "difficulty": "hard",
            },
        ],
    }
    results = {
        "participants": {"agent": "agent-id"},
        "results": [
            {
                "shard_index": 0,
                "results": [
                    {
                        "task_id": "task-a",
                        "task_digest": "sha256:a",
                        "category": "office-white-collar",
                        "difficulty": "easy",
                        "score_eligible": True,
                        "passed": False,
                        "reward": 0.0,
                        "artifact_refs": [],
                    },
                    {
                        "task_id": "task-b",
                        "task_digest": "sha256:B-MISMATCH",
                        "category": "software-engineering",
                        "difficulty": "medium",
                        "score_eligible": True,
                        "passed": True,
                        "reward": 1.0,
                        "artifact_refs": ["artifact://task-b/answer.json"],
                    },
                    {
                        "task_id": "task-extra",
                        "score_eligible": True,
                        "passed": False,
                        "reward": 0.0,
                        "artifact_refs": [],
                    },
                ],
            }
        ],
    }
    report = build_taskset_crosscheck(taskset, results, taskset_path="selftest-taskset", results_path="selftest-results")
    summary = report.summary
    expected = {
        "result_shape": summary.get("result_shape") == "nested_shard",
        "missing": summary.get("missing_in_results_count") == 1,
        "extra": summary.get("extra_in_results_count") == 1,
        "digest_mismatch": summary.get("mismatch_counts", {}).get("task_digest_mismatch") == 1,
        "artifact_empty": summary.get("artifact_refs_empty_count") == 2,
    }
    return {
        "ok": all(expected.values()),
        "version": SCRIPT_VERSION,
        "expected": expected,
        "summary": summary,
        "errors": list(report.errors),
        "warnings": list(report.warnings),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cross-check SkillsBench taskset metadata against official results.")
    parser.add_argument("taskset_pos", nargs="?", help="Path to standard-v1 taskset JSON. Alternative to --taskset.")
    parser.add_argument("results_pos", nargs="?", help="Path to official results JSON. Alternative to --results.")
    parser.add_argument("--taskset", help="Path to standard-v1 taskset JSON.")
    parser.add_argument("--results", help="Path to official SkillsBench/AgentBeats results JSON.")
    parser.add_argument("--out", help="Write full cross-check report JSON to this path.")
    parser.add_argument("--summary-out", help="Write summary-only JSON to this path.")
    parser.add_argument("--markdown", help="Write markdown report to this path.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output to stdout.")
    parser.add_argument("--fail-on-mismatch", action="store_true", help="Exit non-zero if cross-check is not OK.")
    parser.add_argument("--selftest", action="store_true", help="Run built-in selftest and exit.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.selftest:
        payload = validate_taskset_crosscheck_selftest()
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        return 0 if payload.get("ok") else 1

    taskset_path = args.taskset or args.taskset_pos
    results_path = args.results or args.results_pos
    if not taskset_path or not results_path:
        print("error: provide --taskset and --results, or two positional paths", file=sys.stderr)
        return 2

    taskset_payload = _load_json(taskset_path)
    results_payload = _load_json(results_path)
    report = build_taskset_crosscheck(
        taskset_payload,
        results_payload,
        taskset_path=str(taskset_path),
        results_path=str(results_path),
    )
    report_dict = report.as_dict()

    if args.out:
        _dump_json(args.out, report_dict, pretty=True)
    if args.summary_out:
        _dump_json(args.summary_out, report.summary, pretty=True)
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(render_markdown(report), encoding="utf-8")

    if args.pretty or not (args.out or args.summary_out or args.markdown):
        print(json.dumps(report_dict if args.pretty else report.summary, indent=2, sort_keys=True, ensure_ascii=False))

    if args.fail_on_mismatch and not report.ok:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
