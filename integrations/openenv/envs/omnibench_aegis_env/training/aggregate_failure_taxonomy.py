from __future__ import annotations

"""Aggregate a lightweight failure taxonomy from eval/rollout artifacts."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

class FailureTaxonomyError(RuntimeError):
    pass

def _load_json(path: Path) -> Any:
    with path.open('r', encoding='utf-8') as fh:
        return json.load(fh)

def _iter_result_objects(root: Path):
    if root.is_file():
        payload = _load_json(root)
        if isinstance(payload, Mapping):
            yield root, payload
        elif isinstance(payload, list):
            for index, item in enumerate(payload):
                if isinstance(item, Mapping):
                    yield Path(f'{root}#{index}'), item
        return
    for pattern in ('*.eval.json', '*.rollout.json'):
        for path in sorted(root.glob(pattern)):
            payload = _load_json(path)
            if isinstance(payload, Mapping):
                yield path, payload

def _unwrap_final_state(result: Mapping[str, Any]) -> dict[str, Any]:
    final_state = result.get('final_state')
    if isinstance(final_state, Mapping):
        if isinstance(final_state.get('state'), Mapping):
            return dict(final_state['state'])
        return dict(final_state)
    steps = result.get('steps')
    if isinstance(steps, Sequence) and not isinstance(steps, (str, bytes, bytearray)):
        for step in reversed(steps):
            if not isinstance(step, Mapping):
                continue
            response = step.get('response')
            if not isinstance(response, Mapping):
                continue
            state = response.get('state')
            if isinstance(state, Mapping):
                return dict(state)
    return {}

def _error_class(error: Any) -> str:
    if not error:
        return 'none'
    text = str(error)
    return text.split(':', 1)[0].strip() or 'unknown'

def aggregate_failure_taxonomy(*, inputs: Sequence[Path]) -> dict[str, Any]:
    if not inputs:
        raise FailureTaxonomyError('at least one input path is required')
    status_counter: Counter[str] = Counter()
    failure_mode_counter: Counter[str] = Counter()
    terminal_reason_counter: Counter[str] = Counter()
    error_class_counter: Counter[str] = Counter()
    domain_counter: Counter[str] = Counter()
    scenario_counter: Counter[str] = Counter()
    by_domain: dict[str, Counter[str]] = defaultdict(Counter)
    cases: list[dict[str, Any]] = []
    for input_path in inputs:
        root = input_path.resolve()
        if not root.exists():
            raise FailureTaxonomyError(f'input path does not exist: {root}')
        for path, result in _iter_result_objects(root):
            domain = str(result.get('domain') or 'unknown')
            scenario_id = str(result.get('scenario_id') or 'unknown')
            status = str(result.get('status') or 'unknown')
            error = result.get('error')
            state = _unwrap_final_state(result)
            failure_mode = str(state.get('failure_mode') or 'none')
            terminal_reason = str(state.get('terminal_reason') or 'none')
            err_cls = _error_class(error)
            status_counter[status] += 1
            domain_counter[domain] += 1
            scenario_counter[f'{domain}/{scenario_id}'] += 1
            error_class_counter[err_cls] += 1
            failure_mode_counter[failure_mode] += 1
            terminal_reason_counter[terminal_reason] += 1
            by_domain[domain][failure_mode] += 1
            if status != 'pass' or failure_mode != 'none' or terminal_reason != 'none' or err_cls != 'none':
                cases.append({'file': str(path), 'domain': domain, 'scenario_id': scenario_id, 'status': status, 'failure_mode': failure_mode, 'terminal_reason': terminal_reason, 'error_class': err_cls, 'error': error})
    return {'ok': True, 'status_counts': dict(status_counter), 'domain_counts': dict(domain_counter), 'scenario_counts': dict(scenario_counter), 'error_classes': dict(error_class_counter), 'failure_modes': dict(failure_mode_counter), 'terminal_reasons': dict(terminal_reason_counter), 'failure_modes_by_domain': {domain: dict(counter) for domain, counter in by_domain.items()}, 'cases': cases}

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Aggregate a lightweight failure taxonomy from eval/rollout artifacts.')
    parser.add_argument('inputs', nargs='+', help='One or more result files or directories')
    parser.add_argument('--output', help='Optional path where the taxonomy report should be written')
    parser.add_argument('--json', action='store_true', help='Print the taxonomy report as JSON')
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        report = aggregate_failure_taxonomy(inputs=[Path(value) for value in args.inputs])
    except FailureTaxonomyError as exc:
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
        print('[ok] failure taxonomy aggregated')
        print(f"- statuses: {report['status_counts']}")
        print(f"- failure_modes: {report['failure_modes']}")
        print(f"- terminal_reasons: {report['terminal_reasons']}")
        print(f"- error_classes: {report['error_classes']}")
        print(f"- cases: {len(report['cases'])}")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
