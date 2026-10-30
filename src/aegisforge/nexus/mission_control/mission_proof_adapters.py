"""Bounded, read-only ProofGraph/Provenance inspection for Nexus Mission Control.

Mission Coordinator's legacy operation `proof.collect_evidence` is mapped to
an inspection of ALREADY-EXISTING metadata. No evidence, artifact or permission
is created. Optional, host-injected ProofGraph, ArtifactManifest and evidence
trail are cross-checked by NexusReplay; the basic ledger-only use remains valid.

Snapshot digests establish structural integrity of *reported metadata*, not
independent truth, authenticity, access authority, or live file integrity.
No raw records, paths, prompts, source code, or evidence contents are exported.

Contract v2: previously prepared bridge plans and receipts must be regenerated.
No filesystem, network, Docker, model or external-tool operation occurs here.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from typing import Any

from .mission_bridge import MissionBridgeCatalog, MissionBridgePort
from .mission_read_adapters import READ_ADAPTER_SCHEMA, NexusMissionReadAdapterError
from .mission_trust_adapters import TRUST_READ_CATALOG, NexusMissionTrustReadAdapters
from .mission_registry import NexusMissionRegistry
from ..proof.provenance import NexusProvenanceLedger, NexusProvenanceError
from ..proof.artifact_manifest import NexusArtifactManifest, NexusArtifactManifestError
from ..proof.replay import NexusReplay, NexusReplayError, ReplayLimits
from ..evidence import NexusEvidenceTrail

PROOF_INSPECT_SCHEMA = 'aegisforge.nexus.mission_control.proof_inspect.v2'
_MAX_SOURCE_BYTES = 2_000_000
_MAX_RECORDS = 4096
_MAX_SUMMARY_BYTES = 8192
_MAX_WARNINGS = 16


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise NexusMissionReadAdapterError(message)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise NexusMissionReadAdapterError('invalid proof metadata') from exc


def _sha(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    # All labels have already been checked against the ledger's allowlists.
    return dict(sorted(Counter(row[field] for row in rows).items()))


_PROOF_FIELDS = (
    'record_count', 'terminal_sha256', 'provenance_snapshot_sha256',
    'chain_consistent', 'evidence_created_by_adapter',
    'subject_counts', 'origin_counts', 'capture_method_counts',
    'operation_counts', 'parent_link_count', 'source_event_link_count',
    'graph_present', 'graph_sha256', 'graph_node_count', 'graph_edge_count',
    'manifest_present', 'manifest_sha256', 'manifest_record_count',
    'manifest_provenance_matches', 'manifest_unmatched_count',
    'evidence_trail_present', 'evidence_event_count',
    'cross_source_status', 'cross_source_checks', 'cross_source_warnings',
    'metadata_integrity_verified', 'authenticity_verified',
    'artifact_contents_rechecked', 'effects_executed',
)
_PROOF_PORT = MissionBridgePort(
    'proof', 'collect_evidence', 'local.proof_metadata_inspection.v2',
    _sha({'schema': READ_ADAPTER_SCHEMA, 'component': 'proof',
         'operation': 'collect_evidence',
         'adapter_id': 'local.proof_metadata_inspection.v2',
         'read_only': True, 'summary_fields': list(_PROOF_FIELDS)}),
)
PROOF_READ_CATALOG = MissionBridgeCatalog(tuple(sorted(
    (*TRUST_READ_CATALOG.ports, _PROOF_PORT),
    key=lambda p: (p.component, p.operation))))


class NexusMissionProofReadAdapters(NexusMissionTrustReadAdapters):
    """Summarize an existing mission ledger and optional related proof sources.

    The host injects sources explicitly and remains responsible for their
    authenticity. inspect() and verify_receipt() are inherited from Mission
    Read Adapters; they re-check sources and the registry best-effort, but
    cannot make reads across independent objects atomic.
    """

    def __init__(
        self, registry: NexusMissionRegistry, *,
        provenance: NexusProvenanceLedger | None = None,
        artifact_manifest: NexusArtifactManifest | None = None,
        evidence_trail: NexusEvidenceTrail | None = None,
        **source_kwargs: Any,
    ) -> None:
        super().__init__(registry, **source_kwargs)
        _require(provenance is None or type(provenance) is NexusProvenanceLedger,
                 'invalid provenance source')
        _require(artifact_manifest is None or type(artifact_manifest) is NexusArtifactManifest,
                 'invalid artifact manifest source')
        _require(evidence_trail is None or isinstance(evidence_trail, NexusEvidenceTrail),
                 'invalid evidence trail source')
        self._provenance = provenance
        self._artifact_manifest = artifact_manifest
        self._evidence_trail = evidence_trail
        self._replay_limits = ReplayLimits(max_source_bytes=_MAX_SOURCE_BYTES,
                                           max_timeline_entries=_MAX_RECORDS,
                                           max_warnings=64)

    @property
    def catalog(self) -> MissionBridgeCatalog:
        return PROOF_READ_CATALOG

    def _proof_data(self, mission_id: str) -> tuple[str, dict[str, Any]]:
        _require(self._provenance is not None, 'provenance source missing')
        try:
            ledger = self._provenance.snapshot()
            _require(type(ledger) is dict and ledger.get('mission_id') == mission_id,
                     'provenance belongs to another mission')
            _require(len(_canonical(ledger)) <= _MAX_SOURCE_BYTES,
                     'provenance snapshot exceeds read budget')
            _require(NexusProvenanceLedger.verify_snapshot(
                ledger, max_bytes=_MAX_SOURCE_BYTES), 'invalid provenance snapshot')
            rows = ledger['records']
            _require(type(rows) is list and len(rows) <= _MAX_RECORDS,
                     'provenance record limit exceeded')

            graph_snapshot = None
            if self._proof_graph is not None:
                graph_snapshot = self._proof_graph.snapshot()
                _require(type(graph_snapshot) is dict and
                         graph_snapshot.get('mission_id') == mission_id,
                         'ProofGraph belongs to another mission')
                _require(len(_canonical(graph_snapshot)) <= _MAX_SOURCE_BYTES,
                         'ProofGraph snapshot exceeds read budget')

            artifact_snapshot = None
            if self._artifact_manifest is not None:
                artifact_snapshot = self._artifact_manifest.snapshot()
                _require(type(artifact_snapshot) is dict and
                         artifact_snapshot.get('mission_id') == mission_id,
                         'artifact manifest belongs to another mission')
                _require(len(_canonical(artifact_snapshot)) <= _MAX_SOURCE_BYTES,
                         'artifact manifest snapshot exceeds read budget')

            replay = NexusReplay(mission_id, limits=self._replay_limits)
            report = replay.reconstruct(
                provenance_snapshot=ledger, graph_snapshot=graph_snapshot,
                artifact_snapshot=artifact_snapshot,
                evidence_trail=self._evidence_trail,
            )
            _require(NexusReplay.verify_report(report), 'invalid replay report')
            warnings = list(report['warnings'])
            matched = report['links']['manifest_provenance_matches']
            total_artifacts = report['source_counts']['artifact_records']
            if 0 < matched < total_artifacts:
                warnings.append('some_manifest_artifacts_unlinked')
            _require(len(warnings) <= _MAX_WARNINGS,
                     'too many cross-source warnings for receipt')
        except NexusMissionReadAdapterError:
            raise
        except (NexusProvenanceError, NexusArtifactManifestError, NexusReplayError,
                TypeError, ValueError, KeyError, OSError, OverflowError,
                RecursionError) as exc:
            raise NexusMissionReadAdapterError('proof source integrity check failed') from exc

        # Merely supplying two snapshots does not prove that they describe
        # linked records. Only advertise checks that NexusReplay actually made.
        cross_checks = []
        if artifact_snapshot is not None and artifact_snapshot['record_count']:
            cross_checks.append('artifact_digest_links')
        if self._evidence_trail is not None:
            cross_checks.append('source_event_links')
        # Source hashes are stable content descriptors, not authenticated seals.
        summary = {
            'record_count': ledger['record_count'],
            'terminal_sha256': ledger['terminal_sha256'],
            'provenance_snapshot_sha256': ledger['snapshot_sha256'],
            'chain_consistent': True,
            'evidence_created_by_adapter': False,
            'subject_counts': _counts(rows, 'subject_kind'),
            'origin_counts': _counts(rows, 'origin_type'),
            'capture_method_counts': _counts(rows, 'capture_method'),
            'operation_counts': _counts(rows, 'operation'),
            'parent_link_count': sum(len(row['parent_record_ids']) for row in rows),
            'source_event_link_count': sum(row['source_event_sha256'] is not None
                                           for row in rows),
            'graph_present': graph_snapshot is not None,
            'graph_sha256': graph_snapshot['graph_sha256'] if graph_snapshot else None,
            'graph_node_count': report['source_counts']['graph_nodes'],
            'graph_edge_count': report['source_counts']['graph_edges'],
            'manifest_present': artifact_snapshot is not None,
            'manifest_sha256': artifact_snapshot['snapshot_sha256']
                               if artifact_snapshot else None,
            'manifest_record_count': report['source_counts']['artifact_records'],
            'manifest_provenance_matches': matched,
            'manifest_unmatched_count': total_artifacts - matched
                                        if artifact_snapshot is not None else 0,
            'evidence_trail_present': self._evidence_trail is not None,
            'evidence_event_count': report['source_counts']['evidence_events'],
            'cross_source_status': ('partial' if warnings else 'consistent')
                                   if cross_checks else 'not_assessed',
            'cross_source_checks': cross_checks,
            'cross_source_warnings': list(warnings),
            'metadata_integrity_verified': True,
            'authenticity_verified': False,
            'artifact_contents_rechecked': False,
            'effects_executed': False,
        }
        _require(len(_canonical(summary)) <= _MAX_SUMMARY_BYTES,
                 'proof metadata summary exceeds receipt budget')
        source_sha = _sha({
            'schema': PROOF_INSPECT_SCHEMA,
            'sources': report['source_sha256'],
            'replay_report_sha256': report['report_sha256'],
        })
        return source_sha, summary

    def _source_summary(self, component: str, operation: str, mission_id: str,
                        product: str) -> tuple[str, dict[str, Any]]:
        if (component, operation) == ('proof', 'collect_evidence'):
            return self._proof_data(mission_id)
        return super()._source_summary(component, operation, mission_id, product)

    def _current_source_sha(self, component: str, operation: str) -> str:
        if (component, operation) == ('proof', 'collect_evidence'):
            _require(self._provenance is not None, 'provenance source missing')
            return self._proof_data(self._provenance.mission_id)[0]
        return super()._current_source_sha(component, operation)


__all__ = [
    'PROOF_INSPECT_SCHEMA', 'PROOF_READ_CATALOG',
    'NexusMissionProofReadAdapters',
]
