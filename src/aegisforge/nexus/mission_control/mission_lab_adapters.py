"""Read-only verification of an existing Nexus Laboratory scorecard.

Coordinator operation `lab.evaluate` is intentionally restricted to verifying
an ALREADY-BUILT scorecard against host-supplied source reports. No scenarios
are run, no observations are generated, and no external tools are invoked.
Scorecard rates are descriptive and do NOT prove real-world correctness.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from .mission_bridge import MissionBridgeCatalog, MissionBridgePort
from .mission_read_adapters import READ_ADAPTER_SCHEMA, NexusMissionReadAdapterError
from .mission_proof_adapters import PROOF_READ_CATALOG, NexusMissionProofReadAdapters
from .mission_registry import NexusMissionRegistry
from ..lab.scorecard import NexusScorecard, ScorecardComparison, ScorecardAblation

LAB_INSPECT_SCHEMA = 'aegisforge.nexus.mission_control.lab_inspect.v1'


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise NexusMissionReadAdapterError(message)


def _sha(value: Any) -> str:
    try:
        return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                 ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise NexusMissionReadAdapterError('invalid scorecard metadata') from exc


_LAB_FIELDS = ('scorecard_sha256', 'report_count', 'regression_count',
               'ablation_count', 'scorecard_status', 'evaluations', 'passed',
               'failed', 'inconclusive', 'review_flag_count', 'verified_against_sources',
               'scenarios_executed_by_adapter')
_LAB_PORT = MissionBridgePort(
    'lab', 'evaluate', 'local.scorecard_verification.v1',
    _sha({'schema': READ_ADAPTER_SCHEMA, 'component': 'lab',
          'operation': 'evaluate', 'adapter_id': 'local.scorecard_verification.v1',
          'read_only': True, 'summary_fields': list(_LAB_FIELDS)}),
)
LAB_READ_CATALOG = MissionBridgeCatalog(tuple(sorted(
    (*PROOF_READ_CATALOG.ports, _LAB_PORT),
    key=lambda p: (p.component, p.operation))))


class NexusMissionLabReadAdapters(NexusMissionProofReadAdapters):
    """Verify scorecards against *all* host-supplied original source reports."""

    def __init__(self, registry: NexusMissionRegistry, *,
                 scorecard_engine: NexusScorecard | None = None,
                 scorecard_report: Mapping[str, Any] | None = None,
                 scorecard_evaluations: Sequence[Mapping[str, Any]] = (),
                 scorecard_comparisons: Sequence[ScorecardComparison] = (),
                 scorecard_ablations: Sequence[ScorecardAblation] = (),
                 **source_kwargs: Any) -> None:
        super().__init__(registry, **source_kwargs)
        _require(scorecard_engine is None or type(scorecard_engine) is NexusScorecard,
                 'invalid scorecard engine')
        _require(scorecard_report is None or type(scorecard_report) is dict,
                 'invalid scorecard report')
        _require(type(scorecard_evaluations) in (tuple, list) and
                 len(scorecard_evaluations) <= 128,
                 'invalid evaluation source list')
        _require(type(scorecard_comparisons) in (tuple, list) and
                 len(scorecard_comparisons) <= 64,
                 'invalid regression source list')
        _require(type(scorecard_ablations) in (tuple, list) and
                 len(scorecard_ablations) <= 32,
                 'invalid ablation source list')
        self._scorecard_engine = scorecard_engine
        self._scorecard_report = scorecard_report
        self._scorecard_evaluations = tuple(scorecard_evaluations)
        self._scorecard_comparisons = tuple(scorecard_comparisons)
        self._scorecard_ablations = tuple(scorecard_ablations)

    @property
    def catalog(self) -> MissionBridgeCatalog:
        return LAB_READ_CATALOG

    def _lab_data(self) -> tuple[str, dict[str, Any]]:
        engine, report = self._scorecard_engine, self._scorecard_report
        _require(engine is not None and report is not None and
                 type(report.get('scorecard_id')) is str,
                 'scorecard sources missing')
        _require(engine.verify(report, report['scorecard_id'],
                               self._scorecard_evaluations,
                               comparisons=self._scorecard_comparisons,
                               ablations=self._scorecard_ablations),
                 'scorecard is inconsistent with original reports')
        aggregate = report['aggregate']
        summary = {
            'scorecard_sha256': report['scorecard_sha256'],
            'report_count': report['evaluation_report_count'],
            'regression_count': report['regression_report_count'],
            'ablation_count': report['ablation_study_count'],
            'scorecard_status': report['status'],
            'evaluations': aggregate['evaluations'],
            'passed': aggregate['passed'], 'failed': aggregate['failed'],
            'inconclusive': aggregate['inconclusive'],
            'review_flag_count': len(report['review_flags']),
            'verified_against_sources': True,
            'scenarios_executed_by_adapter': False,
        }
        _require(len(json.dumps(summary, ensure_ascii=True)) <= 4096,
                 'scorecard summary too large')
        return report['scorecard_sha256'], summary

    def _source_summary(self, component: str, operation: str, mission_id: str,
                        product: str) -> tuple[str, dict[str, Any]]:
        if (component, operation) == ('lab', 'evaluate'):
            return self._lab_data()
        return super()._source_summary(component, operation, mission_id, product)

    def _current_source_sha(self, component: str, operation: str) -> str:
        if (component, operation) == ('lab', 'evaluate'):
            return self._lab_data()[0]
        return super()._current_source_sha(component, operation)


__all__ = ['LAB_INSPECT_SCHEMA', 'LAB_READ_CATALOG',
           'NexusMissionLabReadAdapters']
