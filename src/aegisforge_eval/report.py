from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from .schemas import EvaluationReport, TrackResult


def _totals(track_results: list[TrackResult]) -> dict[str, int | float]:
    total = len(track_results)
    counts = {
        "pass": sum(1 for item in track_results if item.status == "pass"),
        "fail": sum(1 for item in track_results if item.status == "fail"),
        "warn": sum(1 for item in track_results if item.status == "warn"),
        "skip": sum(1 for item in track_results if item.status == "skip"),
    }
    average_score = (
        round(sum(item.score for item in track_results) / total, 4) if total else 0.0
    )
    return {"total": total, **counts, "average_score": average_score}


def build_report(
    track_results: list[TrackResult],
    *,
    agent_name: str = "AegisForge",
    run_id: str | None = None,
    metadata: dict | None = None,
) -> EvaluationReport:
    report = EvaluationReport(
        agent_name=agent_name,
        run_id=run_id or str(uuid4()),
        track_results=track_results,
        totals=_totals(track_results),
        metadata=metadata or {},
    )
    return report


def report_to_json(report: EvaluationReport, *, indent: int = 2) -> str:
    return json.dumps(report.to_dict(), indent=indent, ensure_ascii=False, sort_keys=True)


def save_report(report: EvaluationReport, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report_to_json(report), encoding="utf-8")
    return output
