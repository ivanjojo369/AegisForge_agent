from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .report import build_report, report_to_json
from .schemas import EvaluationReport, TrackResult
from .tracks import get_evaluator, get_track_names

try:  # Optional held-out enrichments
    from .heldouts.degradation import DegradationAnalyzer
    from .heldouts.generality import GeneralityAnalyzer

    _HAS_HELDOUTS = True
except Exception:  # pragma: no cover
    DegradationAnalyzer = None  # type: ignore[assignment]
    GeneralityAnalyzer = None  # type: ignore[assignment]
    _HAS_HELDOUTS = False


def run_named_track(name: str, payload: Mapping[str, Any] | None = None) -> TrackResult:
    evaluator = get_evaluator(name)
    return evaluator(payload)


def _resolve_track_selection(selected: Sequence[str] | None) -> list[str]:
    available = get_track_names()
    if not selected:
        return available

    unknown = [name for name in selected if name not in available]
    if unknown:
        raise ValueError(
            f"Unknown tracks requested: {', '.join(unknown)}. Available: {', '.join(available)}"
        )
    return list(selected)


def _augment_metadata(
    payload: Mapping[str, Any],
    metadata: dict[str, Any] | None,
    selected_names: list[str],
) -> dict[str, Any]:
    merged = dict(metadata or {})
    merged.setdefault("selected_tracks", list(selected_names))
    merged.setdefault("payload_keys", sorted(payload.keys()))

    if _HAS_HELDOUTS:
        if "baseline_results" in payload and "heldout_results" in payload:
            report = DegradationAnalyzer().analyze(
                payload.get("baseline_results", []),
                payload.get("heldout_results", []),
            )
            merged["heldout_degradation"] = report.as_dict()

        if "suite_success_rates" in payload:
            generality = GeneralityAnalyzer().analyze(payload["suite_success_rates"])
            merged["generality_hint"] = generality.as_dict()

    return merged


def evaluate_tracks(
    payload: Mapping[str, Any] | None = None,
    *,
    selected: Sequence[str] | None = None,
    agent_name: str = "AegisForge",
    metadata: dict[str, Any] | None = None,
) -> EvaluationReport:
    payload = dict(payload or {})
    selected_names = _resolve_track_selection(selected)
    results = [run_named_track(name, payload) for name in selected_names]
    enriched_metadata = _augment_metadata(payload, metadata, selected_names)
    return build_report(results, agent_name=agent_name, metadata=enriched_metadata)


def _load_json_arg(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


def _load_json_file(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in file: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local evaluation helpers for AegisForge tracks.")
    parser.add_argument("--payload", default=None, help="Inline JSON object with input fields for the selected tracks")
    parser.add_argument("--payload-file", default=None, help="Path to a JSON file with the evaluation payload")
    parser.add_argument("--metadata", default=None, help="Inline JSON metadata object to attach to the report")
    parser.add_argument("--metadata-file", default=None, help="Path to a JSON file with extra report metadata")
    parser.add_argument(
        "--tracks",
        nargs="*",
        default=None,
        help=f"Optional subset of tracks. Available: {', '.join(get_track_names())}",
    )
    parser.add_argument("--agent-name", default="AegisForge")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the resulting report JSON")
    args = parser.parse_args()

    payload: dict[str, Any] = {}
    if args.payload_file:
        payload.update(_load_json_file(args.payload_file))
    if args.payload:
        payload.update(_load_json_arg(args.payload))

    metadata: dict[str, Any] = {}
    if args.metadata_file:
        metadata.update(_load_json_file(args.metadata_file))
    if args.metadata:
        metadata.update(_load_json_arg(args.metadata))

    report = evaluate_tracks(
        payload,
        selected=args.tracks,
        agent_name=args.agent_name,
        metadata=metadata or None,
    )
    rendered = report_to_json(report)
    if args.pretty:
        rendered = json.dumps(json.loads(rendered), indent=2, ensure_ascii=False)
    print(rendered)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
