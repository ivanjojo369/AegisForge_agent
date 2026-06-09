#!/usr/bin/env python3
from __future__ import annotations

"""Audit SkillsBench/AgentBeats result JSON shapes.

This tool is intentionally standalone and dependency-free. It helps diagnose the
exact result-shape issue observed in SkillsBench/AgentBeats runs where:

- SQL leaderboard queries expect a nested shard shape:
    results: [
      {"results": [row, row, ...]},
      {"results": [row, row, ...]}
    ]

- legacy/flattened aggregators may produce a flat shape:
    results: [row, row, row, ...]

- transitional pipelines may produce mixed or malformed results.

The audit does not judge task correctness. It only answers:
1. What shape is this result file?
2. Would it satisfy the nested-shard validator contract?
3. How many rows/tasks/shards are present?
4. Are official scoring fields present and internally coherent?
5. Are artifact_refs empty, missing, or populated?

Recommended location:
    tools/skillsbench_result_shape_audit.py
"""

from argparse import ArgumentParser
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import json
import math
import sys


TOOL_VERSION = "skillsbench_result_shape_audit_v0_1_2026_06_09"

REQUIRED_ROW_FIELDS: tuple[str, ...] = (
    "task_id",
    "score_eligible",
    "passed",
    "reward",
)

SHAPE_NESTED_SHARD = "nested_shard"
SHAPE_LEGACY_FLAT = "legacy_flat"
SHAPE_MIXED = "mixed"
SHAPE_INVALID_MISSING_RESULTS = "invalid_missing_results"
SHAPE_INVALID_RESULTS_NOT_LIST = "invalid_results_not_list"
SHAPE_INVALID_EMPTY_RESULTS = "invalid_empty_results"
SHAPE_INVALID_ELEMENTS = "invalid_elements"


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class ResultRowAudit:
    index: int
    task_id: str
    wrapper_index: int | None
    row_index: int | None
    source_shape: str
    score_eligible: bool | None
    passed: bool | None
    reward: Any
    time_used: Any = None
    category: str = ""
    difficulty: str = ""
    artifact_refs_count: int | None = None
    artifact_refs_present: bool = False
    infra_failure_type: str | None = None
    error_type: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> JsonDict:
        data = asdict(self)
        data["warnings"] = list(self.warnings)
        data["errors"] = list(self.errors)
        return data


@dataclass(frozen=True)
class ResultWrapperAudit:
    index: int
    kind: str
    row_count: int = 0
    keys: tuple[str, ...] = field(default_factory=tuple)
    shard_index: Any = None
    errors: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> JsonDict:
        data = asdict(self)
        data["keys"] = list(self.keys)
        data["errors"] = list(self.errors)
        return data


@dataclass(frozen=True)
class ResultShapeAudit:
    version: str
    input_path: str
    shape: str
    validator_compatible: bool
    sql_leaderboard_compatible: bool
    status: str
    participants_agent: str
    top_level_keys: tuple[str, ...]
    results_type: str
    result_wrapper_count: int
    nested_wrapper_count: int
    flat_row_count: int
    invalid_wrapper_count: int
    row_count: int
    unique_task_count: int
    duplicate_task_ids: tuple[str, ...]
    eligible_count: int
    passed_count: int
    failed_count: int
    ineligible_count: int
    reward_sum: float
    reward_max_possible: float | None
    artifact_refs_empty_count: int
    artifact_refs_missing_count: int
    artifact_refs_populated_count: int
    infra_failure_count: int
    error_type_count: int
    category_counts: dict[str, int]
    difficulty_counts: dict[str, int]
    shard_row_counts: dict[str, int]
    row_error_count: int
    row_warning_count: int
    validator_errors: tuple[str, ...]
    validator_warnings: tuple[str, ...]
    wrappers: tuple[ResultWrapperAudit, ...] = field(default_factory=tuple)
    rows: tuple[ResultRowAudit, ...] = field(default_factory=tuple)

    def as_dict(self, *, include_rows: bool = True, include_wrappers: bool = True) -> JsonDict:
        data = asdict(self)
        data["top_level_keys"] = list(self.top_level_keys)
        data["duplicate_task_ids"] = list(self.duplicate_task_ids)
        data["validator_errors"] = list(self.validator_errors)
        data["validator_warnings"] = list(self.validator_warnings)
        data["wrappers"] = [item.as_dict() for item in self.wrappers] if include_wrappers else []
        data["rows"] = [item.as_dict() for item in self.rows] if include_rows else []
        return data

    def summary_dict(self) -> JsonDict:
        return self.as_dict(include_rows=False, include_wrappers=False)


def _json_type(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return type(value).__name__


def _safe_text(value: Any, *, limit: int = 2000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except Exception:
        return str(value)[:limit]


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _clean_counter(counter: Counter[Any]) -> dict[str, int]:
    return {str(key): int(value) for key, value in sorted(counter.items(), key=lambda item: str(item[0]))}


def load_json_file(path: str | Path) -> Any:
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_file(path: str | Path, payload: Any) -> Path:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return file_path


def detect_result_shape(payload: Any) -> str:
    """Return a compact result-shape label for a raw result payload."""

    if not isinstance(payload, Mapping):
        return SHAPE_INVALID_ELEMENTS

    results = payload.get("results")
    if results is None:
        return SHAPE_INVALID_MISSING_RESULTS
    if not isinstance(results, list):
        return SHAPE_INVALID_RESULTS_NOT_LIST
    if not results:
        return SHAPE_INVALID_EMPTY_RESULTS

    nested = 0
    flat = 0
    invalid = 0

    for item in results:
        if isinstance(item, Mapping) and isinstance(item.get("results"), list):
            nested += 1
        elif isinstance(item, Mapping) and "task_id" in item:
            flat += 1
        else:
            invalid += 1

    if invalid:
        return SHAPE_INVALID_ELEMENTS
    if nested and flat:
        return SHAPE_MIXED
    if flat and not nested:
        return SHAPE_LEGACY_FLAT
    if nested and not flat:
        return SHAPE_NESTED_SHARD
    return SHAPE_INVALID_ELEMENTS


def normalize_result_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten nested/mixed result payloads to row dictionaries.

    This helper is intentionally tolerant for diagnostics. It does not imply the
    official validator would accept the same input.
    """

    results = payload.get("results")
    if not isinstance(results, list):
        return []

    rows: list[dict[str, Any]] = []
    for item in results:
        if isinstance(item, Mapping) and isinstance(item.get("results"), list):
            for row in item.get("results") or []:
                if isinstance(row, Mapping):
                    rows.append(dict(row))
        elif isinstance(item, Mapping) and "task_id" in item:
            rows.append(dict(item))
    return rows


def _validate_row(row: Mapping[str, Any], *, source_shape: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    task_id = row.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        errors.append("task_id must be a non-empty string")

    score_eligible = row.get("score_eligible")
    if not isinstance(score_eligible, bool):
        errors.append("score_eligible must be a bool")

    passed = row.get("passed")
    if not isinstance(passed, bool):
        errors.append("passed must be a bool")

    if "reward" not in row:
        errors.append("reward field is missing")
    elif _safe_float(row.get("reward")) is None:
        warnings.append("reward is present but not numeric")

    if "artifact_refs" not in row:
        warnings.append("artifact_refs field is missing")
    elif not isinstance(row.get("artifact_refs"), list):
        warnings.append("artifact_refs is present but not a list")

    if source_shape == SHAPE_LEGACY_FLAT:
        warnings.append("row comes from legacy flat top-level shape")

    return errors, warnings


def audit_result_payload(payload: Any, *, input_path: str = "") -> ResultShapeAudit:
    """Audit a raw result payload."""

    if not isinstance(payload, Mapping):
        return ResultShapeAudit(
            version=TOOL_VERSION,
            input_path=input_path,
            shape=SHAPE_INVALID_ELEMENTS,
            validator_compatible=False,
            sql_leaderboard_compatible=False,
            status="",
            participants_agent="",
            top_level_keys=(),
            results_type=_json_type(payload),
            result_wrapper_count=0,
            nested_wrapper_count=0,
            flat_row_count=0,
            invalid_wrapper_count=1,
            row_count=0,
            unique_task_count=0,
            duplicate_task_ids=(),
            eligible_count=0,
            passed_count=0,
            failed_count=0,
            ineligible_count=0,
            reward_sum=0.0,
            reward_max_possible=None,
            artifact_refs_empty_count=0,
            artifact_refs_missing_count=0,
            artifact_refs_populated_count=0,
            infra_failure_count=0,
            error_type_count=0,
            category_counts={},
            difficulty_counts={},
            shard_row_counts={},
            row_error_count=0,
            row_warning_count=0,
            validator_errors=("top-level payload must be an object",),
            validator_warnings=(),
        )

    shape = detect_result_shape(payload)
    top_level_keys = tuple(sorted(str(key) for key in payload.keys()))
    status = str(payload.get("status") or "")
    participants = payload.get("participants")
    participants_agent = ""
    if isinstance(participants, Mapping):
        participants_agent = str(participants.get("agent") or "")

    validator_errors: list[str] = []
    validator_warnings: list[str] = []

    if not participants_agent.strip():
        validator_errors.append("participants.agent must be a non-empty string")

    results = payload.get("results")
    results_type = _json_type(results)

    if results is None:
        validator_errors.append("top-level results field is missing")
        result_items: list[Any] = []
    elif not isinstance(results, list):
        validator_errors.append("top-level results must be a list")
        result_items = []
    else:
        result_items = list(results)

    if isinstance(results, list) and not results:
        validator_warnings.append("top-level results list is empty")

    wrappers: list[ResultWrapperAudit] = []
    rows: list[ResultRowAudit] = []

    nested_wrapper_count = 0
    flat_row_count = 0
    invalid_wrapper_count = 0
    shard_row_counts: Counter[str] = Counter()

    for wrapper_index, item in enumerate(result_items):
        if isinstance(item, Mapping) and isinstance(item.get("results"), list):
            nested_wrapper_count += 1
            shard_rows = item.get("results") or []
            shard_key = str(item.get("shard_index", item.get("shard", wrapper_index)))
            shard_row_counts[shard_key] += len(shard_rows)
            wrappers.append(
                ResultWrapperAudit(
                    index=wrapper_index,
                    kind="nested_shard",
                    row_count=len(shard_rows),
                    keys=tuple(sorted(str(key) for key in item.keys())),
                    shard_index=item.get("shard_index", item.get("shard")),
                )
            )
            for row_index, row in enumerate(shard_rows):
                if isinstance(row, Mapping):
                    row_errors, row_warnings = _validate_row(row, source_shape=SHAPE_NESTED_SHARD)
                    rows.append(_row_audit_from_mapping(
                        row,
                        index=len(rows),
                        wrapper_index=wrapper_index,
                        row_index=row_index,
                        source_shape=SHAPE_NESTED_SHARD,
                        errors=row_errors,
                        warnings=row_warnings,
                    ))
                else:
                    validator_errors.append(
                        f"results[{wrapper_index}].results[{row_index}] must be an object, got {_json_type(row)}"
                    )
        elif isinstance(item, Mapping) and "task_id" in item:
            flat_row_count += 1
            validator_errors.append(
                f"results[{wrapper_index}] uses legacy direct-row shape; wrap rows under results[{wrapper_index}].results"
            )
            row_errors, row_warnings = _validate_row(item, source_shape=SHAPE_LEGACY_FLAT)
            rows.append(_row_audit_from_mapping(
                item,
                index=len(rows),
                wrapper_index=None,
                row_index=wrapper_index,
                source_shape=SHAPE_LEGACY_FLAT,
                errors=row_errors,
                warnings=row_warnings,
            ))
            wrappers.append(
                ResultWrapperAudit(
                    index=wrapper_index,
                    kind="legacy_flat_row",
                    row_count=1,
                    keys=tuple(sorted(str(key) for key in item.keys())),
                    errors=("legacy direct-row shape",),
                )
            )
        else:
            invalid_wrapper_count += 1
            validator_errors.append(
                f"results[{wrapper_index}] must be a shard object with nested results list, got {_json_type(item)}"
            )
            keys: tuple[str, ...] = tuple(sorted(str(key) for key in item.keys())) if isinstance(item, Mapping) else ()
            wrappers.append(
                ResultWrapperAudit(
                    index=wrapper_index,
                    kind="invalid",
                    row_count=0,
                    keys=keys,
                    errors=(f"invalid wrapper type {_json_type(item)}",),
                )
            )

    # Aggregate rows.
    task_ids = [row.task_id for row in rows if row.task_id]
    task_counter = Counter(task_ids)
    duplicate_task_ids = tuple(sorted(task_id for task_id, count in task_counter.items() if count > 1))

    eligible_count = sum(1 for row in rows if row.score_eligible is True)
    ineligible_count = sum(1 for row in rows if row.score_eligible is False)
    passed_count = sum(1 for row in rows if row.passed is True)
    failed_count = sum(1 for row in rows if row.passed is False)

    reward_values = [_safe_float(row.reward) for row in rows]
    reward_sum = round(sum(value for value in reward_values if value is not None), 6)

    category_counts: Counter[str] = Counter(row.category or "unknown" for row in rows)
    difficulty_counts: Counter[str] = Counter(row.difficulty or "unknown" for row in rows)

    artifact_refs_empty_count = sum(1 for row in rows if row.artifact_refs_present and row.artifact_refs_count == 0)
    artifact_refs_missing_count = sum(1 for row in rows if not row.artifact_refs_present)
    artifact_refs_populated_count = sum(1 for row in rows if row.artifact_refs_present and (row.artifact_refs_count or 0) > 0)

    infra_failure_count = sum(1 for row in rows if row.infra_failure_type)
    error_type_count = sum(1 for row in rows if row.error_type)

    row_error_count = sum(len(row.errors) for row in rows)
    row_warning_count = sum(len(row.warnings) for row in rows)

    if row_error_count:
        validator_errors.append(f"{row_error_count} row validation errors")
    if duplicate_task_ids:
        validator_warnings.append(f"duplicate task_id values: {', '.join(duplicate_task_ids[:20])}")
    if artifact_refs_missing_count:
        validator_warnings.append(f"{artifact_refs_missing_count} rows are missing artifact_refs")
    if artifact_refs_empty_count and rows:
        validator_warnings.append(f"{artifact_refs_empty_count} rows have empty artifact_refs")

    validator_compatible = (
        not validator_errors
        and shape == SHAPE_NESTED_SHARD
        and bool(participants_agent.strip())
        and isinstance(results, list)
    )
    sql_leaderboard_compatible = shape == SHAPE_NESTED_SHARD and invalid_wrapper_count == 0

    return ResultShapeAudit(
        version=TOOL_VERSION,
        input_path=input_path,
        shape=shape,
        validator_compatible=validator_compatible,
        sql_leaderboard_compatible=sql_leaderboard_compatible,
        status=status,
        participants_agent=participants_agent,
        top_level_keys=top_level_keys,
        results_type=results_type,
        result_wrapper_count=len(result_items),
        nested_wrapper_count=nested_wrapper_count,
        flat_row_count=flat_row_count,
        invalid_wrapper_count=invalid_wrapper_count,
        row_count=len(rows),
        unique_task_count=len(task_counter),
        duplicate_task_ids=duplicate_task_ids,
        eligible_count=eligible_count,
        passed_count=passed_count,
        failed_count=failed_count,
        ineligible_count=ineligible_count,
        reward_sum=reward_sum,
        reward_max_possible=float(eligible_count) if rows else None,
        artifact_refs_empty_count=artifact_refs_empty_count,
        artifact_refs_missing_count=artifact_refs_missing_count,
        artifact_refs_populated_count=artifact_refs_populated_count,
        infra_failure_count=infra_failure_count,
        error_type_count=error_type_count,
        category_counts=_clean_counter(category_counts),
        difficulty_counts=_clean_counter(difficulty_counts),
        shard_row_counts=_clean_counter(shard_row_counts),
        row_error_count=row_error_count,
        row_warning_count=row_warning_count,
        validator_errors=tuple(validator_errors),
        validator_warnings=tuple(validator_warnings),
        wrappers=tuple(wrappers),
        rows=tuple(rows),
    )


def _row_audit_from_mapping(
    row: Mapping[str, Any],
    *,
    index: int,
    wrapper_index: int | None,
    row_index: int | None,
    source_shape: str,
    errors: Sequence[str],
    warnings: Sequence[str],
) -> ResultRowAudit:
    artifact_refs_present = "artifact_refs" in row
    artifact_refs_count: int | None = None
    if artifact_refs_present:
        refs = row.get("artifact_refs")
        artifact_refs_count = len(refs) if isinstance(refs, list) else None

    return ResultRowAudit(
        index=index,
        task_id=str(row.get("task_id") or ""),
        wrapper_index=wrapper_index,
        row_index=row_index,
        source_shape=source_shape,
        score_eligible=row.get("score_eligible") if isinstance(row.get("score_eligible"), bool) else None,
        passed=row.get("passed") if isinstance(row.get("passed"), bool) else None,
        reward=row.get("reward"),
        time_used=row.get("time_used"),
        category=str(row.get("category") or ""),
        difficulty=str(row.get("difficulty") or ""),
        artifact_refs_count=artifact_refs_count,
        artifact_refs_present=artifact_refs_present,
        infra_failure_type=str(row.get("infra_failure_type")) if row.get("infra_failure_type") else None,
        error_type=str(row.get("error_type")) if row.get("error_type") else None,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def render_markdown(audit: ResultShapeAudit) -> str:
    lines: list[str] = []
    lines.append("# SkillsBench Result Shape Audit")
    lines.append("")
    lines.append(f"- Version: `{audit.version}`")
    lines.append(f"- Input: `{audit.input_path or '<memory>'}`")
    lines.append(f"- Shape: `{audit.shape}`")
    lines.append(f"- Validator compatible: `{audit.validator_compatible}`")
    lines.append(f"- SQL leaderboard compatible: `{audit.sql_leaderboard_compatible}`")
    lines.append(f"- Status: `{audit.status}`")
    lines.append(f"- Participants agent: `{audit.participants_agent}`")
    lines.append("")
    lines.append("## Counts")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    for label, value in (
        ("result_wrapper_count", audit.result_wrapper_count),
        ("nested_wrapper_count", audit.nested_wrapper_count),
        ("flat_row_count", audit.flat_row_count),
        ("invalid_wrapper_count", audit.invalid_wrapper_count),
        ("row_count", audit.row_count),
        ("unique_task_count", audit.unique_task_count),
        ("eligible_count", audit.eligible_count),
        ("passed_count", audit.passed_count),
        ("failed_count", audit.failed_count),
        ("ineligible_count", audit.ineligible_count),
        ("reward_sum", audit.reward_sum),
        ("artifact_refs_empty_count", audit.artifact_refs_empty_count),
        ("artifact_refs_missing_count", audit.artifact_refs_missing_count),
        ("artifact_refs_populated_count", audit.artifact_refs_populated_count),
        ("infra_failure_count", audit.infra_failure_count),
        ("error_type_count", audit.error_type_count),
    ):
        lines.append(f"| `{label}` | {value} |")

    lines.append("")
    lines.append("## Shards")
    lines.append("")
    if audit.shard_row_counts:
        lines.append("| Shard | Rows |")
        lines.append("|---|---:|")
        for shard, count in audit.shard_row_counts.items():
            lines.append(f"| `{shard}` | {count} |")
    else:
        lines.append("_No nested shard counts detected._")

    if audit.validator_errors:
        lines.append("")
        lines.append("## Validator errors")
        lines.append("")
        for item in audit.validator_errors:
            lines.append(f"- {item}")

    if audit.validator_warnings:
        lines.append("")
        lines.append("## Validator warnings")
        lines.append("")
        for item in audit.validator_warnings:
            lines.append(f"- {item}")

    if audit.duplicate_task_ids:
        lines.append("")
        lines.append("## Duplicate task IDs")
        lines.append("")
        for item in audit.duplicate_task_ids:
            lines.append(f"- `{item}`")

    return "\n".join(lines).rstrip() + "\n"


def validate_result_shape_audit_selftest() -> JsonDict:
    row = {
        "task_id": "example-task",
        "score_eligible": True,
        "passed": False,
        "reward": 0.0,
        "artifact_refs": [],
        "category": "software-engineering",
        "difficulty": "medium",
    }

    nested_payload = {
        "status": "completed",
        "participants": {"agent": "agent-1"},
        "results": [{"shard_index": 0, "results": [row]}],
    }
    flat_payload = {
        "status": "completed",
        "participants": {"agent": "agent-1"},
        "results": [row],
    }
    mixed_payload = {
        "status": "completed",
        "participants": {"agent": "agent-1"},
        "results": [{"shard_index": 0, "results": [row]}, dict(row, task_id="legacy-row")],
    }
    invalid_payload = {
        "status": "completed",
        "participants": {"agent": "agent-1"},
        "results": [{"bad": "wrapper"}],
    }

    nested = audit_result_payload(nested_payload, input_path="<selftest:nested>")
    flat = audit_result_payload(flat_payload, input_path="<selftest:flat>")
    mixed = audit_result_payload(mixed_payload, input_path="<selftest:mixed>")
    invalid = audit_result_payload(invalid_payload, input_path="<selftest:invalid>")

    errors: list[str] = []
    if nested.shape != SHAPE_NESTED_SHARD or not nested.validator_compatible:
        errors.append("nested payload was not recognized as validator-compatible nested_shard")
    if flat.shape != SHAPE_LEGACY_FLAT or flat.validator_compatible:
        errors.append("flat payload was not recognized as legacy_flat validator-incompatible")
    if mixed.shape != SHAPE_MIXED or mixed.validator_compatible:
        errors.append("mixed payload was not recognized as mixed validator-incompatible")
    if invalid.shape != SHAPE_INVALID_ELEMENTS or invalid.validator_compatible:
        errors.append("invalid payload was not recognized as invalid_elements")
    if nested.row_count != 1 or flat.row_count != 1 or mixed.row_count != 2:
        errors.append("row counts are wrong")

    return {
        "ok": not errors,
        "version": TOOL_VERSION,
        "errors": errors,
        "nested": nested.summary_dict(),
        "flat": flat.summary_dict(),
        "mixed": mixed.summary_dict(),
        "invalid": invalid.summary_dict(),
    }


def _build_arg_parser() -> ArgumentParser:
    parser = ArgumentParser(description="Audit SkillsBench/AgentBeats result JSON shape.")
    parser.add_argument("input", nargs="?", help="Path to official results JSON.")
    parser.add_argument("--out", help="Optional path to write full JSON audit report.")
    parser.add_argument("--summary-out", help="Optional path to write compact JSON audit summary.")
    parser.add_argument("--markdown", help="Optional path to write a Markdown audit report.")
    parser.add_argument("--no-rows", action="store_true", help="Omit detailed row entries from stdout/--out report.")
    parser.add_argument("--no-wrappers", action="store_true", help="Omit wrapper entries from stdout/--out report.")
    parser.add_argument("--strict-exit", action="store_true", help="Exit 2 when validator_compatible is false.")
    parser.add_argument("--selftest", action="store_true", help="Run built-in selftest.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON to stdout.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.selftest:
        result = validate_result_shape_audit_selftest()
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("ok") else 1

    if not args.input:
        parser.error("input is required unless --selftest is used")

    input_path = Path(args.input)
    payload = load_json_file(input_path)
    audit = audit_result_payload(payload, input_path=str(input_path))

    report = audit.as_dict(include_rows=not args.no_rows, include_wrappers=not args.no_wrappers)
    summary = audit.summary_dict()

    if args.out:
        write_json_file(args.out, report)
    if args.summary_out:
        write_json_file(args.summary_out, summary)
    if args.markdown:
        md_path = Path(args.markdown)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(audit), encoding="utf-8")

    if args.out or args.summary_out or args.markdown:
        stdout_payload = summary
    else:
        stdout_payload = report

    if args.pretty:
        print(json.dumps(stdout_payload, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(stdout_payload, ensure_ascii=False, sort_keys=True))

    if args.strict_exit and not audit.validator_compatible:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
