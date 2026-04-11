from __future__ import annotations

"""Compare two eval summaries and their per-scenario result files."""

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

class CompareEvalRunsError(RuntimeError):
    pass

def _load_json(path: Path) -> Any:
    with path.open('r', encoding='utf-8') as fh:
        return json.load(fh)

def _collect_eval_results(directory: Path) -> dict[tuple[str, str], dict[str, Any]]:
    results: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(directory.glob('*.eval.json')):
        payload = _load_json(path)
        if not isinstance(payload, Mapping):
            continue
        domain = str(payload.get('domain') or '')
        scenario_id = str(payload.get('scenario_id') or '')
        if domain and scenario_id:
            results[(domain, scenario_id)] = dict(payload)
    return results

def _summary_slice(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {'passes': int(summary.get('passes') or 0), 'fails': int(summary.get('fails') or 0), 'warnings': int(summary.get('warnings') or 0), 'count': int(summary.get('count') or 0), 'score_mean': float(summary.get('score_mean') or 0.0), 'score_min': float(summary.get('score_min') or 0.0), 'score_max': float(summary.get('score_max') or 0.0)}

def compare_eval_runs(*, baseline_summary_path: Path, candidate_summary_path: Path, baseline_results_dir: Path | None, candidate_results_dir: Path | None) -> dict[str, Any]:
    baseline_summary = _load_json(baseline_summary_path)
    candidate_summary = _load_json(candidate_summary_path)
    if not isinstance(baseline_summary, Mapping):
        raise CompareEvalRunsError('baseline summary must be a JSON object')
    if not isinstance(candidate_summary, Mapping):
        raise CompareEvalRunsError('candidate summary must be a JSON object')
    baseline_dir = baseline_results_dir or baseline_summary_path.parent
    candidate_dir = candidate_results_dir or candidate_summary_path.parent
    baseline_results = _collect_eval_results(baseline_dir)
    candidate_results = _collect_eval_results(candidate_dir)
    all_keys = sorted(set(baseline_results) | set(candidate_results))
    per_scenario: list[dict[str, Any]] = []
    improved = regressed = unchanged = 0
    for key in all_keys:
        domain, scenario_id = key
        before = baseline_results.get(key, {})
        after = candidate_results.get(key, {})
        before_status = str(before.get('status') or 'missing')
        after_status = str(after.get('status') or 'missing')
        before_score = float(before.get('score') or 0.0)
        after_score = float(after.get('score') or 0.0)
        if after_score > before_score or (before_status != 'pass' and after_status == 'pass'):
            trend = 'improved'; improved += 1
        elif after_score < before_score or (before_status == 'pass' and after_status != 'pass'):
            trend = 'regressed'; regressed += 1
        else:
            trend = 'unchanged'; unchanged += 1
        per_scenario.append({'domain': domain, 'scenario_id': scenario_id, 'baseline_status': before_status, 'candidate_status': after_status, 'baseline_score': before_score, 'candidate_score': after_score, 'score_delta': round(after_score - before_score, 4), 'trend': trend})
    baseline_stats = _summary_slice(baseline_summary)
    candidate_stats = _summary_slice(candidate_summary)
    return {'ok': True, 'baseline_summary': str(baseline_summary_path), 'candidate_summary': str(candidate_summary_path), 'baseline': baseline_stats, 'candidate': candidate_stats, 'delta': {key: round(candidate_stats[key] - baseline_stats[key], 4) for key in ('passes', 'fails', 'warnings', 'count', 'score_mean', 'score_min', 'score_max')}, 'per_scenario': per_scenario, 'counts': {'improved': improved, 'regressed': regressed, 'unchanged': unchanged}}

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Compare two eval summaries and their per-scenario files.')
    parser.add_argument('--baseline-summary', required=True, help='Path to the baseline eval_summary.json')
    parser.add_argument('--candidate-summary', required=True, help='Path to the candidate eval_summary.json')
    parser.add_argument('--baseline-results-dir', help='Optional directory with baseline *.eval.json files')
    parser.add_argument('--candidate-results-dir', help='Optional directory with candidate *.eval.json files')
    parser.add_argument('--output', help='Optional path where the comparison report should be written')
    parser.add_argument('--json', action='store_true', help='Print the comparison report as JSON')
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        report = compare_eval_runs(baseline_summary_path=Path(args.baseline_summary).resolve(), candidate_summary_path=Path(args.candidate_summary).resolve(), baseline_results_dir=Path(args.baseline_results_dir).resolve() if args.baseline_results_dir else None, candidate_results_dir=Path(args.candidate_results_dir).resolve() if args.candidate_results_dir else None)
    except CompareEvalRunsError as exc:
        report = {'ok': False, 'error': str(exc), 'type': 'contract_error'}
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"[fail] {report['error']}")
        return 1
    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open('w', encoding='utf-8') as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
            fh.write('\n')
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print('[ok] eval runs compared')
        print(f"- baseline passes: {report['baseline']['passes']}")
        print(f"- candidate passes: {report['candidate']['passes']}")
        print(f"- score_mean delta: {report['delta']['score_mean']}")
        print(f"- improved: {report['counts']['improved']}")
        print(f"- regressed: {report['counts']['regressed']}")
        print(f"- unchanged: {report['counts']['unchanged']}")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
