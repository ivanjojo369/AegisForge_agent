from __future__ import annotations

"""Report rendering helpers for the B4 Submission Gate.

Public surface kept:
- render_markdown_summary(report)
- write_json_report(report, path)
- write_markdown_report(report, path)
- write_report_bundle(report, json_path=..., markdown_path=...)

This version is intentionally compatible with the earlier compact reporter while
adding richer Sprint 4 handling for:
- benchmark identity;
- controlled-only safety scope;
- submission readiness;
- scenario/domain/upstream/category coverage;
- telemetry identity preservation;
- grouped repo artifact status;
- track/evaluation results;
- scorecard summaries;
- generated artifact/download links.

The renderer accepts plain dicts but is tolerant of dataclasses, pydantic models,
and objects exposing as_dict(), to_dict(), or model_dump().
"""

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping


DEFAULT_TITLE = "Submission Gate Report"

SPRINT4_EXPECTED_SCENARIOS: tuple[str, ...] = (
    "SaleForceOneSpy",
    "WikiWiper",
    "TicketTwister",
    "BidBot",
    "TaxWizTrap",
    "LnkLifter",
    "InventoryInject",
    "DevContainerDoom",
    "StaticShipScam",
    "WhistleBlowerWreck",
    "DocuDoctor",
    "SearchGlitch",
    "GymJailbreak",
    "CodeReviewRuse",
    "CryptoCrash",
    "LawFirmLeak",
)

IDENTITY_KEYS: tuple[str, ...] = (
    "domain",
    "scenario_id",
    "scenario_name",
    "upstream_track",
    "category",
    "adapter",
    "assessment_mode",
    "scenario_family",
    "benchmark",
    "selected_opponent",
    "source_url",
)

TELEMETRY_REQUIRED_FIELDS: tuple[str, ...] = (
    "task_id",
    "track",
    "domain",
    "scenario_id",
    "scenario_name",
    "upstream_track",
    "category",
    "adapter",
    "assessment_mode",
    "scenario_family",
)


def _to_plain(value: Any) -> Any:
    """Convert common structured objects into plain JSON-like data."""

    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_plain(item) for item in value]
    for attr in ("to_dict", "as_dict", "model_dump", "dict"):
        if hasattr(value, attr):
            try:
                dumped = getattr(value, attr)()
            except Exception:
                continue
            return _to_plain(dumped)
    return value


def _as_dict(value: Any) -> dict[str, Any]:
    plain = _to_plain(value)
    return dict(plain) if isinstance(plain, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    plain = _to_plain(value)
    if plain is None:
        return []
    if isinstance(plain, list):
        return plain
    if isinstance(plain, tuple):
        return list(plain)
    if isinstance(plain, set):
        return sorted(plain)
    return [plain]


def _as_text_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


def _coerce_errors(value: Any) -> list[str]:
    return _as_text_list(value)


def _coerce_warnings(value: Any) -> list[str]:
    return _as_text_list(value)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "ok", "ready", "pass", "passed", "success"}
    return bool(value)


def _fmt_bool(value: Any) -> str:
    return "✅ true" if _truthy(value) else "❌ false"


def _fmt_status(value: Any) -> str:
    text = str(value or "unknown").strip()
    lowered = text.lower()
    if lowered in {"ready", "ok", "pass", "passed", "success", "completed", "true"}:
        return f"✅ {text}"
    if lowered in {"blocked", "fail", "failed", "error", "false", "critical"}:
        return f"❌ {text}"
    if lowered in {"warn", "warning", "partial", "needs_review", "review", "skip", "skipped"}:
        return f"⚠️ {text}"
    return text or "unknown"


def _md_cell(value: Any) -> str:
    text = str(value if value not in (None, "") else "").strip()
    return text.replace("|", "\\|").replace("\n", "<br>")


def _code(value: Any) -> str:
    text = str(value if value not in (None, "") else "unknown").strip()
    return f"`{text}`"


def _short_list(items: Any, *, limit: int = 12) -> str:
    values = [str(item).strip() for item in _as_list(items) if str(item).strip()]
    if not values:
        return "none"
    shown = values[:limit]
    suffix = "" if len(values) <= limit else f", ... +{len(values) - limit} more"
    return ", ".join(f"`{item}`" for item in shown) + suffix


def _append_key_values(lines: list[str], payload: Mapping[str, Any], keys: list[str] | tuple[str, ...]) -> None:
    data = _as_dict(payload)
    for key in keys:
        if key not in data:
            continue
        value = data.get(key)
        if isinstance(value, bool):
            rendered = _fmt_bool(value)
        elif isinstance(value, (list, tuple, set)):
            rendered = _short_list(value)
        elif isinstance(value, Mapping):
            continue
        else:
            rendered = _code(value)
        lines.append(f"- {key}: {rendered}")


def _extract_identity(report: Mapping[str, Any]) -> dict[str, Any]:
    """Extract Sprint 4 identity fields from several possible report shapes."""

    root = _as_dict(report)
    identity = _as_dict(root.get("identity"))
    metadata = _as_dict(root.get("metadata"))
    benchmark = _as_dict(root.get("benchmark"))
    scenario = _as_dict(root.get("scenario"))

    merged: dict[str, Any] = {}
    for key in IDENTITY_KEYS:
        merged[key] = (
            identity.get(key)
            or root.get(key)
            or metadata.get(key)
            or benchmark.get(key)
            or scenario.get(key)
            or ""
        )

    if not merged.get("scenario_name") and merged.get("scenario_id"):
        merged["scenario_name"] = merged["scenario_id"]
    return merged


def _render_identity(lines: list[str], report: Mapping[str, Any]) -> None:
    identity = {k: v for k, v in _extract_identity(report).items() if v not in (None, "")}
    if not identity:
        return
    lines.extend(["", "## Sprint 4 Identity"])
    _append_key_values(lines, identity, IDENTITY_KEYS)


def _render_benchmark(lines: list[str], report: Mapping[str, Any]) -> None:
    benchmark = _as_dict(report.get("benchmark"))
    if not benchmark:
        return

    lines.extend(["", "## Benchmark"])
    _append_key_values(
        lines,
        benchmark,
        [
            "name",
            "phase",
            "sprint",
            "assessment_mode",
            "scenario_family",
            "benchmark_scope",
            "competition_role",
            "leaderboard_ready",
            "registration_status",
            "a2a_agent_card_status",
        ],
    )


def _render_safety_scope(lines: list[str], report: Mapping[str, Any]) -> None:
    safety = _as_dict(report.get("safety_scope"))
    if not safety:
        return

    lines.extend(["", "## Safety Scope"])
    _append_key_values(
        lines,
        safety,
        [
            "benchmark_only",
            "controlled_only",
            "no_real_world_targeting",
            "no_secret_extraction_from_real_systems",
            "no_persistence_or_evasion",
            "no_harmful_payloads",
            "no_real_funds",
            "sandbox_only",
        ],
    )
    notes = _as_text_list(safety.get("notes"))
    if notes:
        lines.append("- notes:")
        for note in notes:
            lines.append(f"  - {note}")


def _render_readiness(lines: list[str], report: Mapping[str, Any]) -> None:
    readiness = _as_dict(report.get("submission_readiness"))
    if not readiness:
        return

    lines.extend(["", "## Submission Readiness"])
    lines.append(f"- ok: **{_fmt_bool(readiness.get('ok', False))}**")
    if readiness.get("status") is not None:
        lines.append(f"- status: **{_fmt_status(readiness.get('status'))}**")

    for key, title in (
        ("blocking_issues", "blocking issues"),
        ("warnings", "warnings"),
        ("manual_required", "manual required"),
        ("next_steps", "next steps"),
    ):
        items = _as_text_list(readiness.get(key))
        if items:
            lines.append(f"- {title}:")
            for item in items:
                lines.append(f"  - {item}")


def _render_coverage_block(
    lines: list[str],
    title: str,
    coverage: Mapping[str, Any],
    *,
    present_key: str = "present",
    missing_key: str = "missing",
    expected_key: str = "expected",
) -> None:
    data = _as_dict(coverage)
    if not data:
        return

    lines.extend(["", f"## {title}"])
    if "ok" in data:
        lines.append(f"- ok: **{_fmt_bool(data.get('ok'))}**")

    for key in ("expected_count", "present_count", "missing_count", "extra_count"):
        if key in data:
            lines.append(f"- {key}: **{data.get(key)}**")

    if expected_key in data:
        lines.append(f"- expected: {_short_list(data.get(expected_key), limit=24)}")
    if present_key in data:
        lines.append(f"- present: {_short_list(data.get(present_key), limit=24)}")
    if missing_key in data:
        lines.append(f"- missing: {_short_list(data.get(missing_key), limit=24)}")
    if "extra" in data:
        lines.append(f"- extra: {_short_list(data.get('extra'), limit=24)}")


def _scenario_sort_key(item: tuple[str, Any]) -> tuple[int, str]:
    scenario_id = item[0]
    if scenario_id in SPRINT4_EXPECTED_SCENARIOS:
        return (SPRINT4_EXPECTED_SCENARIOS.index(scenario_id), scenario_id)
    return (999, scenario_id)


def _render_scenario_matrix(lines: list[str], scenario_coverage: Mapping[str, Any]) -> None:
    scenarios = _as_dict(scenario_coverage.get("scenarios"))
    if not scenarios:
        return

    lines.extend(["", "### Scenario Matrix"])
    lines.append("| Scenario | Domain | Upstream | Category | Adapter | Family | Files | Status |")
    lines.append("|---|---|---|---|---|---|---:|---|")

    for scenario_id, raw_record in sorted(scenarios.items(), key=_scenario_sort_key):
        record = _as_dict(raw_record)
        files = _as_list(record.get("files"))
        missing = _as_list(record.get("missing"))
        status = record.get("status") or ("missing" if missing else "present")
        lines.append(
            "| "
            f"`{_md_cell(scenario_id)}` | "
            f"`{_md_cell(record.get('domain', ''))}` | "
            f"`{_md_cell(record.get('upstream_track', ''))}` | "
            f"`{_md_cell(record.get('category', ''))}` | "
            f"`{_md_cell(record.get('adapter', ''))}` | "
            f"`{_md_cell(record.get('scenario_family', ''))}` | "
            f"{len(files)} | "
            f"{_fmt_status(status)} |"
        )


def _render_sprint4_coverage(lines: list[str], report: Mapping[str, Any]) -> None:
    scenario_coverage = _as_dict(report.get("scenario_coverage"))
    _render_coverage_block(
        lines,
        "Sprint 4 Scenario Coverage",
        scenario_coverage,
        present_key="present_scenarios",
        missing_key="missing_scenarios",
        expected_key="expected_scenarios",
    )
    _render_scenario_matrix(lines, scenario_coverage)

    _render_coverage_block(lines, "Domain Coverage", _as_dict(report.get("domain_coverage")))
    _render_coverage_block(lines, "Upstream Track Coverage", _as_dict(report.get("upstream_track_coverage")))
    _render_coverage_block(lines, "Category Coverage", _as_dict(report.get("category_coverage")))


def _render_telemetry(lines: list[str], report: Mapping[str, Any]) -> None:
    telemetry = _as_dict(report.get("telemetry"))
    if not telemetry:
        return

    lines.extend(["", "## Telemetry"])
    _append_key_values(
        lines,
        telemetry,
        [
            "trace_schema",
            "episode_summary",
            "failure_taxonomy",
            "scorecard",
            "emitter",
            "events",
            "preserves_identity",
        ],
    )

    required = _as_text_list(telemetry.get("required_fields")) or list(TELEMETRY_REQUIRED_FIELDS)
    missing = _as_text_list(telemetry.get("missing_fields"))
    lines.append(f"- required_fields: {_short_list(required, limit=24)}")
    if missing:
        lines.append(f"- missing_fields: {_short_list(missing, limit=24)}")


def _render_repo_group(lines: list[str], payload: Mapping[str, Any]) -> None:
    required_groups = _as_dict(payload.get("required_groups"))
    optional_groups = _as_dict(payload.get("optional_groups"))

    if required_groups:
        lines.append("- required artifact groups:")
        for group_name, group_payload in sorted(required_groups.items()):
            group = _as_dict(group_payload)
            lines.append(
                f"  - `{group_name}`: {_fmt_bool(group.get('ok', False))}; "
                f"missing: {_short_list(group.get('missing'))}"
            )

    if optional_groups:
        lines.append("- optional artifact groups:")
        for group_name, group_payload in sorted(optional_groups.items()):
            group = _as_dict(group_payload)
            lines.append(
                f"  - `{group_name}`: {_fmt_bool(group.get('ok', False))}; "
                f"missing: {_short_list(group.get('missing'))}"
            )


def _render_checks(lines: list[str], report: Mapping[str, Any]) -> None:
    checks = report.get("checks") or {}
    lines.extend(["", "## Checks"])

    if not isinstance(checks, Mapping) or not checks:
        lines.extend(["- No checks recorded.", ""])
        return

    for name, raw_payload in checks.items():
        payload = _as_dict(raw_payload) or {"value": raw_payload}
        lines.append(f"### {name}")
        lines.append(f"- ok: **{_fmt_bool(payload.get('ok', False))}**")

        for key in ("url", "status", "status_code", "detail", "duration_ms", "path"):
            value = payload.get(key)
            if value not in (None, ""):
                lines.append(f"- {key}: {_code(value)}" if key != "detail" else f"- detail: {value}")

        for warning in _coerce_warnings(payload.get("warnings")):
            lines.append(f"- warning: {warning}")

        for error in _coerce_errors(payload.get("errors")):
            lines.append(f"- error: {error}")

        if name == "repo":
            _render_repo_group(lines, payload)

        extra = _as_dict(payload.get("extra"))
        if extra:
            lines.append("- extra:")
            for key, value in sorted(extra.items()):
                if isinstance(value, (dict, list, tuple, set)):
                    continue
                lines.append(f"  - {key}: `{value}`")

        lines.append("")


def _render_track_results(lines: list[str], report: Mapping[str, Any]) -> None:
    results = _as_list(report.get("track_results") or report.get("results"))
    if not results:
        return

    lines.extend(["", "## Evaluation Track Results"])
    lines.append("| Track | Status | Score | Summary |")
    lines.append("|---|---|---:|---|")

    for raw in results:
        item = _as_dict(raw)
        if not item:
            continue
        lines.append(
            "| "
            f"`{_md_cell(item.get('track', 'unknown'))}` | "
            f"{_fmt_status(item.get('status'))} | "
            f"{_md_cell(item.get('score', 0.0))} | "
            f"{_md_cell(item.get('summary', ''))} |"
        )


def _render_scorecards(lines: list[str], report: Mapping[str, Any]) -> None:
    raw_scorecards = report.get("scorecards") or report.get("scorecard")
    scorecards = _as_list(raw_scorecards)
    if not scorecards:
        return

    lines.extend(["", "## Scorecards"])
    lines.append("| Task | Track | Domain | Scenario | Status | Correctness | Efficiency | Robustness |")
    lines.append("|---|---|---|---|---|---:|---:|---:|")

    for raw in scorecards:
        item = _as_dict(raw)
        if not item:
            continue
        lines.append(
            "| "
            f"`{_md_cell(item.get('task_id', ''))}` | "
            f"`{_md_cell(item.get('track', ''))}` | "
            f"`{_md_cell(item.get('domain', ''))}` | "
            f"`{_md_cell(item.get('scenario_id') or item.get('scenario_name') or '')}` | "
            f"{_fmt_status(item.get('status'))} | "
            f"{_md_cell(item.get('correctness_hint', ''))} | "
            f"{_md_cell(item.get('efficiency_hint', ''))} | "
            f"{_md_cell(item.get('robustness_hint', ''))} |"
        )


def _render_generated_artifacts(lines: list[str], report: Mapping[str, Any]) -> None:
    artifacts = report.get("generated_artifacts") or report.get("artifacts") or report.get("outputs")
    items = _as_list(artifacts)
    if not items:
        return

    lines.extend(["", "## Generated Artifacts"])
    for raw in items:
        if isinstance(raw, str):
            lines.append(f"- `{raw}`")
            continue
        item = _as_dict(raw)
        if not item:
            continue
        name = item.get("name") or item.get("path") or item.get("file") or "artifact"
        path = item.get("path") or item.get("file")
        kind = item.get("kind") or item.get("type") or ""
        status = item.get("status")
        suffix = []
        if kind:
            suffix.append(f"kind={kind}")
        if status:
            suffix.append(f"status={status}")
        rendered_suffix = f" ({', '.join(str(s) for s in suffix)})" if suffix else ""
        lines.append(f"- `{name}`{rendered_suffix}" + (f" — `{path}`" if path and path != name else ""))


def render_markdown_summary(report: dict[str, Any]) -> str:
    """Render a compact markdown summary for a submission-gate report."""

    report = _as_dict(report)
    lines = [
        f"# {report.get('title', DEFAULT_TITLE)}",
        "",
        f"- Base URL: `{report.get('base_url', 'unknown')}`",
        f"- Timestamp: `{report.get('timestamp', 'unknown')}`",
        f"- Overall OK: **{_fmt_bool(report.get('ok', False))}**",
    ]

    summary = _as_dict(report.get("summary"))
    if summary:
        for key in ("total", "passed", "failed", "warnings", "skipped"):
            if summary.get(key) is not None:
                label = key.replace("_", " ").title()
                lines.append(f"- {label}: **{summary.get(key)}**")

    _render_identity(lines, report)
    _render_benchmark(lines, report)
    _render_safety_scope(lines, report)
    _render_readiness(lines, report)
    _render_sprint4_coverage(lines, report)
    _render_telemetry(lines, report)
    _render_track_results(lines, report)
    _render_scorecards(lines, report)
    _render_generated_artifacts(lines, report)
    _render_checks(lines, report)

    return "\n".join(lines).rstrip() + "\n"


def write_json_report(report: dict[str, Any], path: str | Path) -> Path:
    """Write the raw JSON report to disk."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_to_plain(report), indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return output


def write_markdown_report(report: dict[str, Any], path: str | Path) -> Path:
    """Write the markdown summary report to disk."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown_summary(report), encoding="utf-8")
    return output


def write_report_bundle(
    report: dict[str, Any],
    *,
    json_path: str | Path,
    markdown_path: str | Path,
) -> dict[str, str]:
    """Write both JSON and markdown reports and return their paths."""

    json_output = write_json_report(report, json_path)
    markdown_output = write_markdown_report(report, markdown_path)
    return {
        "json": str(json_output),
        "markdown": str(markdown_output),
    }


__all__ = [
    "DEFAULT_TITLE",
    "SPRINT4_EXPECTED_SCENARIOS",
    "TELEMETRY_REQUIRED_FIELDS",
    "render_markdown_summary",
    "write_json_report",
    "write_markdown_report",
    "write_report_bundle",
]
