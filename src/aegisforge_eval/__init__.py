"""Evaluation helpers for AegisForge.

This package is intentionally separate from ``aegisforge`` so the core Purple
runtime stays clean while benchmark-facing evaluation logic remains modular.
"""

from .report import build_report, report_to_json, save_report
from .runner import evaluate_tracks, run_named_track
from .schemas import EvaluationReport, TrackConfig, TrackResult

__all__ = [
    "EvaluationReport",
    "TrackConfig",
    "TrackResult",
    "build_report",
    "evaluate_tracks",
    "report_to_json",
    "run_named_track",
    "save_report",
]
