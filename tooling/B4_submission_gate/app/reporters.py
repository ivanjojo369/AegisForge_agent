from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_TITLE = "Submission Gate Report"


def _coerce_errors(value: Any) -> list[str]:
    """Return a normalized list of error strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]



def render_markdown_summary(report: dict[str, Any]) -> str:
    """Render a compact markdown summary for a submission-gate report."""
    checks = report.get("checks") or {}
    lines = [
        f"# {report.get('title', DEFAULT_TITLE)}",
        "",
        f"- Base URL: `{report.get('base_url', 'unknown')}`",
        f"- Timestamp: `{report.get('timestamp', 'unknown')}`",
        f"- Overall OK: **{bool(report.get('ok', False))}**",
    ]

    summary = report.get("summary")
    if isinstance(summary, dict) and summary:
        total = summary.get("total")
        passed = summary.get("passed")
        failed = summary.get("failed")
        if total is not None:
            lines.append(f"- Total checks: **{total}**")
        if passed is not None:
            lines.append(f"- Passed: **{passed}**")
        if failed is not None:
            lines.append(f"- Failed: **{failed}**")

    lines.extend(["", "## Checks"])

    if not isinstance(checks, dict) or not checks:
        lines.extend(["- No checks recorded.", ""])
        return "\n".join(lines).rstrip() + "\n"

    for name, payload in checks.items():
        payload = payload if isinstance(payload, dict) else {"value": payload}
        lines.append(f"### {name}")
        lines.append(f"- ok: **{bool(payload.get('ok', False))}**")

        url = payload.get("url")
        if url:
            lines.append(f"- url: `{url}`")

        status_code = payload.get("status_code")
        if status_code is not None:
            lines.append(f"- status_code: `{status_code}`")

        detail = payload.get("detail")
        if detail:
            lines.append(f"- detail: {detail}")

        for error in _coerce_errors(payload.get("errors")):
            lines.append(f"- error: {error}")

        extra = payload.get("extra")
        if isinstance(extra, dict) and extra:
            lines.append("- extra:")
            for key, value in extra.items():
                lines.append(f"  - {key}: `{value}`")

        lines.append("")

    return "\n".join(lines).rstrip() + "\n"



def write_json_report(report: dict[str, Any], path: str | Path) -> Path:
    """Write the raw JSON report to disk."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output



def write_markdown_report(report: dict[str, Any], path: str | Path) -> Path:
    """Write the markdown summary report to disk."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown_summary(report), encoding="utf-8")
    return output


__all__ = [
    "render_markdown_summary",
    "write_json_report",
    "write_markdown_report",
]
