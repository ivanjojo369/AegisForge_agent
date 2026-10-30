"""Supervised engineering host: repository -> mission -> reviewed repair.

Install: src/aegisforge/nexus/mission_control/mission_engineering_host.py

Uses the ORIGINAL NexusOrchestrator through NexusMissionOrchestratorBridge.
It reviews proposals using the existing engineering modules and can stage a
prebuilt, sanitized, separately hosted candidate checkout for sandbox validation.
It never applies patches, changes the live repository, grants its own approvals,
or contacts a model automatically. Explicit execution is delegated to the
existing Trust/Sandbox bridge and requires an independent host effects grant. Model planning requires
an explicit opt-in, metadata-export opt-in, a bridge planning authorizer, and
host-owned workspace authorization. NO source bytes are sent to the model.

The repository index covers Python and JS/TS heuristically; it does not prove
full repository understanding. This is NOT an isolated code execution boundary.
Source bytes may be sent to a repair model ONLY with explicit model and
source-export authorization. SHA-256 checks consistency, not authenticity.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
from secrets import token_hex
import tempfile
from threading import RLock
from typing import Any, Callable

from .mission_orchestrator_bridge import (
    NexusMissionOrchestratorBridge, NexusMissionOrchestratorBridgeError,
    verify_bridge_receipt,
)
from .mission_service import NexusMissionServiceError
from ..orchestrator import PreparedNexusPlan
from ..workspace.manager import NexusWorkspaceManager, NexusWorkspaceError
from ..workspace.repo_index import NexusRepositoryIndex, NexusRepositoryIndexError
from ..workspace.workspace_policy import (
    NexusWorkspacePolicy, NexusWorkspacePolicyError, WorkspaceRule,
)
from ..engineering.code_proposer import (
    CodeProposal, NexusCodeProposer, NexusCodeProposalError, ProposalChatClient, ProposalContext,
)
from ..engineering.diff_builder import (
    NexusDiffBuilder, NexusDiffBuilderError, PatchBundle,
)

ENGINEERING_HOST_SCHEMA = "aegisforge.nexus.mission_control.engineering_host.v1"
_SHA = re.compile(r"^[a-f0-9]{64}$")
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_MAX_SESSIONS = 32
_MAX_TASK = 2000


class NexusMissionEngineeringHostError(ValueError):
    """Operation cannot be safely bound, is stale, or lacks host authority."""


def _check(ok: bool, reason: str) -> None:
    if not ok:
        raise NexusMissionEngineeringHostError(reason)


def _digest(data: Any) -> str:
    raw = json.dumps(data, sort_keys=True, ensure_ascii=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()


def _repository_stamp(index: dict[str, Any]) -> str:
    # Whole INDEXED code set, never an assertion about files outside its coverage.
    return _digest([(row["relative_path"], row["source_text_sha256"])
                    for row in sorted(index["files"], key=lambda v: v["relative_path"])])


@dataclass(slots=True)
class _EngineeringSession:
    token: str
    actor: str
    mission_id: str
    product: str
    task: str
    target: str
    target_sha256: str
    index_sha256: str
    index_complete: bool
    target_indexed: bool
    registry_sha256: str
    mission_head_sha256: str
    stage: str = "inspected"
    bridge_id: str | None = None
    bound_receipt: dict[str, Any] | None = None
    completed_receipt: dict[str, Any] | None = None
    linked: bool = False
    # In-memory, host-owned review; no changes are applied to the workspace.
    repair_context: ProposalContext | None = None
    repair_proposal: CodeProposal | None = None
    repair_builder: NexusDiffBuilder | None = None
    repair_patch: PatchBundle | None = None
    repair_model_attempted: bool = False
    repair_model_completed: bool = False
    # Exact session links to independently prepared patched-checkout recipes.
    repair_sandbox_bridge: NexusMissionSandboxExecutionBridge | None = None
    repair_sandbox_runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidate_write_attempted: bool = False
    candidate_write_confirmed: bool = False
    verified_regression_test: bool = False
    repair_proof_graph_sha256: str | None = None


# Trusted host callback; actor ID alone does NOT authenticate a caller.
# (actor_id, mission_id, canonical_root_sha256, target_path_sha256) -> literal True.
WorkspaceAuthorizer = Callable[[str, str, str, str], bool]
# (actor, mission, canonical_root_sha256, path_sha256, source_sha256) -> literal True
# A trusted host must inspect the candidate source for secrets before allowing export.
SourceExportAuthorizer = Callable[[str, str, str, str, str], bool]
# (authenticated actor, mission, patchset digest, sandbox action digest) -> True
# Independent of the sandbox approval gate's authenticated human review.
RepairEffectsAuthorizer = Callable[[str, str, str, str], bool]


class NexusMissionEngineeringHost:
    """Repository-to-mission coordinator. No implicit external effects.

    The host MUST protect instances, provide authenticated actors to MissionService,
    and authorize access to the repository independently of registry permissions.
    Do not expose methods directly through an unauthenticated HTTP endpoint.
    """

    def __init__(self, bridge: NexusMissionOrchestratorBridge, *,
                 workspace_root: str | Path,
                 workspace_authorizer: WorkspaceAuthorizer,
                 repair_client: ProposalChatClient | None = None,
                 source_export_authorizer: SourceExportAuthorizer | None = None,
                 repair_effects_authorizer: RepairEffectsAuthorizer | None = None,
                 allow_text_targets: bool = False) -> None:
        _check(type(bridge) is NexusMissionOrchestratorBridge, "expected original bridge")
        _check(callable(workspace_authorizer), "workspace host authorizer required")
        _check(repair_client is None or callable(getattr(repair_client, "chat", None)),
               "repair client must provide chat")
        _check(source_export_authorizer is None or callable(source_export_authorizer),
               "source export authorizer must be callable")
        _check(repair_effects_authorizer is None or callable(repair_effects_authorizer),
               "repair effects authorizer must be callable")
        # Validation here rejects symlink/junction roots and missing directories.
        try:
            workspace = NexusWorkspaceManager(workspace_root)
            index = NexusRepositoryIndex(workspace, include_text_targets=allow_text_targets)
        except (NexusWorkspaceError, NexusRepositoryIndexError, OSError, ValueError) as exc:
            raise NexusMissionEngineeringHostError("workspace unavailable") from exc
        self.bridge = bridge
        self._workspace = workspace
        self._index = index
        self._authorizer = workspace_authorizer
        self._repair_client = repair_client
        self._source_export_authorizer = source_export_authorizer
        if type(allow_text_targets) is not bool:
            raise NexusMissionEngineeringHostError("allow_text_targets must be bool")
        self._allow_text_targets = allow_text_targets
        self._repair_effects_authorizer = repair_effects_authorizer
        self._root_sha = sha256(str(workspace.root).encode("utf-8")).hexdigest()
        self._sessions: dict[str, _EngineeringSession] = {}
        self._lock = RLock()

    def _mission(self, mission_id: str, actor_id: str) -> tuple[dict[str, Any], str]:
        _check(type(mission_id) is str and _ID.fullmatch(mission_id) is not None,
               "invalid mission ID")
        _check(type(actor_id) is str and _ID.fullmatch(actor_id) is not None,
               "invalid actor ID")
        try:
            anchor = self.bridge.service.registry_version(actor_id=actor_id)
            mission = self.bridge.service.get_mission(mission_id, actor_id=actor_id)
            unchanged = self.bridge.service.registry_version(actor_id=actor_id) == anchor
        except NexusMissionServiceError as exc:
            raise NexusMissionEngineeringHostError("mission access denied") from exc
        _check(unchanged, "mission registry changed during read")
        _check(mission["status"] in {"draft", "ready", "paused", "awaiting_review"},
               "mission state is not eligible for planning")
        return mission, anchor

    def _authorize_workspace(self, actor: str, mission: str, target: str) -> None:
        # Authorize BEFORE inspecting even a single target byte.
        try:
            allowed = self._authorizer(actor, mission, self._root_sha,
                                       sha256(target.encode("utf-8")).hexdigest())
        except Exception as exc:
            raise NexusMissionEngineeringHostError("workspace authorization unavailable") from exc
        _check(allowed is True, "workspace authorization denied")

    def _scan(self, target: str) -> tuple[str, str, dict[str, Any]]:
        try:
            source = self._workspace.read_text(target)
            index = self._index.build()
        except (NexusWorkspaceError, NexusRepositoryIndexError, OSError,
                UnicodeError, ValueError) as exc:
            raise NexusMissionEngineeringHostError("repository inspection unavailable") from exc
        # read_text can clip, so a fully read target must be below its upper bound.
        _check(len(source) < self._workspace.limits.max_text_chars and "\ufffd" not in source,
               "target text is truncated or not decodable")
        _check(len(source) > 0, "target file is empty")
        _check(type(index.get("files")) is list, "invalid repository index")
        target_hash = sha256(source.encode("utf-8")).hexdigest()
        # Catch a concurrent change between the target read and index scan.
        for row in index["files"]:
            if row["relative_path"] == target.replace("\\", "/"):
                _check(hmac.compare_digest(target_hash, row["source_text_sha256"]),
                       "target changed during indexing")
        return target_hash, _repository_stamp(index), index

    def inspect(self, mission_id: str, *, actor_id: str, task: str,
                target_file: str) -> dict[str, Any]:
        """Authorize and index an existing repo. NO model calls or writes."""
        with self._lock:
            _check(type(task) is str and 0 < len(task.strip()) <= _MAX_TASK,
                   "invalid engineering task")
            _check(type(target_file) is str and target_file.strip(), "unsafe target file")
            target_file = target_file.replace("\\", "/")
            _check(not target_file.startswith("/") and ":" not in target_file and
                   all(part not in ("", ".", "..") for part in target_file.split("/")),
                   "unsafe target file")
            _check(len(self._sessions) < _MAX_SESSIONS, "host session capacity reached")
            mission, anchor = self._mission(mission_id, actor_id)
            self._authorize_workspace(actor_id, mission_id, target_file)
            target_hash, idx_hash, index = self._scan(target_file)
            _check(self._mission(mission_id, actor_id)[1] == anchor,
                   "mission changed during repository inspection")
            target_indexed = any(row["relative_path"] == target_file
                                 for row in index["files"])
            session_id = token_hex(16)
            s = _EngineeringSession(
                session_id, actor_id, mission_id, mission["product"], task.strip(),
                target_file, target_hash, idx_hash, bool(index["complete"]),
                target_indexed, anchor,
                mission["head_sha256"],
            )
            self._sessions[session_id] = s
            return self._summary(s, index=index)

    def _current(self, session_id: str, actor_id: str, *,
                 require_fresh_registry: bool = True) -> _EngineeringSession:
        _check(type(session_id) is str and re.fullmatch(r"[0-9a-f]{32}", session_id) is not None,
               "invalid host session")
        try:
            s = self._sessions[session_id]
        except KeyError as exc:
            raise NexusMissionEngineeringHostError("unknown host session") from exc
        _check(actor_id == s.actor, "actor mismatch")
        self._authorize_workspace(actor_id, s.mission_id, s.target)
        mission, anchor = self._mission(s.mission_id, actor_id)
        if require_fresh_registry:
            _check(hmac.compare_digest(anchor, s.registry_sha256) and
                   hmac.compare_digest(mission["head_sha256"], s.mission_head_sha256),
                   "mission changed after inspection")
        target_hash, idx_hash, _ = self._scan(s.target)
        _check(hmac.compare_digest(target_hash, s.target_sha256) and
               hmac.compare_digest(idx_hash, s.index_sha256),
               "repository changed after inspection")
        return s

    def _summary(self, s: _EngineeringSession, *,
                 index: dict[str, Any] | None = None) -> dict[str, Any]:
        body = {
            "schema": ENGINEERING_HOST_SCHEMA,
            "session_id": s.token, "mission_id": s.mission_id,
            "product": s.product, "stage": s.stage,
            "task_sha256": sha256(s.task.encode("utf-8")).hexdigest(),
            "target_path_sha256": sha256(s.target.encode("utf-8")).hexdigest(),
            "target_sha256": s.target_sha256,
            "indexed_repository_sha256": s.index_sha256,
            "index_complete": s.index_complete,
            "target_indexed": s.target_indexed,
            "registry_sha256": s.registry_sha256,
            "mission_head_sha256": s.mission_head_sha256,
            "bridge_session_id": s.bridge_id,
            "bridge_phase": (s.completed_receipt or s.bound_receipt or {}).get("phase"),
            "integrity_only": True, "authenticity_verified": False,
            "effects_executed": s.candidate_write_attempted or any(
                entry.get("result", {}).get("effects_attempted") is True
                for entry in s.repair_sandbox_runs.values()
            ),
            "authorization_granted": any(
                entry.get("result", {}).get("approval_consumed") is True
                for entry in s.repair_sandbox_runs.values()
            ),
            "repository_file_contents_uploaded": s.repair_model_completed,
            "repository_file_contents_export_attempted": s.repair_model_attempted,
            "independent_verification_performed": s.verified_regression_test,
            "repair_review_status": (
                "review_required" if s.repair_patch is not None else
                "model_attempted" if s.repair_model_attempted else "not_reviewed"
            ),
            "repair_patchset_sha256": (
                s.repair_patch.patchset_sha256 if s.repair_patch is not None else None
            ),
            "repair_sandbox_statuses": {
                sid: (entry.get("result") or entry["staged"])["status"]
                for sid, entry in s.repair_sandbox_runs.items()
            },
            "patched_snapshot_verified": False,  # a passing recipe is not a full verification
            "candidate_write_attempted": s.candidate_write_attempted,
            "candidate_write_confirmed": s.candidate_write_confirmed,
            "verified_regression_test": s.verified_regression_test,
            "repair_proof_graph_sha256": s.repair_proof_graph_sha256,
        }
        if index is not None:
            body["indexed_files"] = index["indexed_file_count"]
            body["indexed_symbols"] = index["symbol_count"]
            body["index_warnings_count"] = len(index["warnings"]) + len(index["skipped_files"])
        body["receipt_sha256"] = _digest(body)
        return body

    def bind_prepared(self, session_id: str, *, actor_id: str,
                      prepared: PreparedNexusPlan) -> dict[str, Any]:
        """Attach an independently prepared plan from the original orchestrator."""
        with self._lock:
            s = self._current(session_id, actor_id)
            _check(s.stage == "inspected", "session is already bound")
            try:
                receipt = self.bridge.bind_prepared(
                    s.mission_id, actor_id=actor_id, prepared=prepared,
                    expected_registry_sha256=s.registry_sha256).to_dict()
            except NexusMissionOrchestratorBridgeError as exc:
                raise NexusMissionEngineeringHostError("plan binding rejected") from exc
            _check(verify_bridge_receipt(receipt), "bridge receipt failed consistency check")
            s.bridge_id = receipt["session_id"]
            s.bound_receipt = receipt
            s.stage = "bound"
            return self._summary(s)

    def prepare_plan(self, session_id: str, *, actor_id: str,
                     allow_model_call: bool = False,
                     allow_metadata_export: bool = False) -> dict[str, Any]:
        """Potentially billable. User task and bounded repo metadata go to the model.

        The bridge must have an independent planning_authorizer. The selected
        source TEXT is NEVER uploaded; this is not source-level repair yet.
        """
        with self._lock:
            _check(allow_model_call is True and allow_metadata_export is True,
                   "model planning and metadata export require explicit opt-in")
            s = self._current(session_id, actor_id)
            _check(s.stage == "inspected", "session is already bound")
            # This includes a repo-relative path and digests, not source content.
            prompt = (f"Task: {s.task}\nRepository-relative target: {s.target}\n"
                      f"Target SHA-256: {s.target_sha256}\n"
                      f"Indexed repository SHA-256: {s.index_sha256}\n"
                      "No repository source text was provided. Do not claim to have "
                      "inspected implementation. Propose only a reviewable plan.")
            s.stage = "model_attempted"  # prevent repeated model charges on uncertain failures
            try:
                receipt = self.bridge.prepare(
                    s.mission_id, actor_id=actor_id, task=prompt,
                    expected_registry_sha256=s.registry_sha256,
                    allow_model_call=True,
                    constraints=("No tool execution", "No unverified success claims"),
                ).to_dict()
            except NexusMissionOrchestratorBridgeError as exc:
                raise NexusMissionEngineeringHostError("model planning unavailable") from exc
            _check(verify_bridge_receipt(receipt), "bridge receipt failed consistency check")
            s.bridge_id = receipt["session_id"]
            s.bound_receipt = receipt
            s.stage = "bound"
            return self._summary(s)

    def preview(self, session_id: str, *, actor_id: str) -> dict[str, Any]:
        with self._lock:
            s = self._current(session_id, actor_id)
            _check(s.stage == "bound" and s.bridge_id is not None,
                   "session has no active plan")
            try:
                receipt = self.bridge.preview(s.bridge_id, actor_id=actor_id).to_dict()
            except NexusMissionOrchestratorBridgeError as exc:
                raise NexusMissionEngineeringHostError("plan preview failed") from exc
            _check(verify_bridge_receipt(receipt), "bridge preview receipt failed")
            return receipt

    def finish_offline(self, session_id: str, *, actor_id: str) -> dict[str, Any]:
        """Consume a plan once, ALWAYS execute_tools=False, without checks."""
        with self._lock:
            s = self._current(session_id, actor_id)
            _check(s.stage == "bound" and s.bridge_id is not None,
                   "session not ready for offline completion")
            s.stage = "consumed"  # before invoking any potentially failing callback
            try:
                receipt = self.bridge.run_offline(s.bridge_id, actor_id=actor_id).to_dict()
            except NexusMissionOrchestratorBridgeError as exc:
                raise NexusMissionEngineeringHostError("offline plan was consumed without completion") from exc
            _check(verify_bridge_receipt(receipt), "bridge final receipt failed")
            s.completed_receipt = receipt
            s.stage = "completed_read_only"
            return receipt

    def proof_snapshot(self, session_id: str, *, actor_id: str) -> dict[str, Any]:
        with self._lock:
            s = self._current(session_id, actor_id)
            _check(s.stage == "completed_read_only" and s.bridge_id is not None,
                   "session not completed")
            try:
                return self.bridge.proof_snapshot(s.bridge_id, actor_id=actor_id)
            except NexusMissionOrchestratorBridgeError as exc:
                raise NexusMissionEngineeringHostError("proof snapshot unavailable") from exc

    def link_evidence(self, session_id: str, *, actor_id: str) -> dict[str, Any]:
        """Host-authorized registry reference mutation. Never automatic."""
        with self._lock:
            s = self._current(session_id, actor_id)
            _check(s.stage == "completed_read_only" and not s.linked and s.bridge_id is not None,
                   "no evidence available to link")
            s.linked = True  # even an ambiguous result must not be retried automatically
            s.stage = "link_unconfirmed"
            try:
                result = self.bridge.link_evidence(
                    s.bridge_id, actor_id=actor_id,
                    expected_registry_sha256=s.registry_sha256)
            except NexusMissionOrchestratorBridgeError as exc:
                raise NexusMissionEngineeringHostError("host rejected evidence link") from exc
            s.stage = "evidence_linked"
            return result

    def review_repair_candidate(self, session_id: str, *, actor_id: str,
                                candidate_json: str | None = None,
                                allow_model_call: bool = False,
                                allow_source_export: bool = False) -> dict[str, Any]:
        """Build a review-only patch through the existing engineering modules.

        Offline: provide untrusted candidate_json and use the default flags.
        Live: omit candidate_json and explicitly enable BOTH flags. A separate
        trusted host callback must authorize export of the source bytes and a
        client must have been injected at construction. No action is applied.
        A failed/ambiguous model attempt cannot be automatically repeated.
        """
        with self._lock:
            _check(type(allow_model_call) is bool and type(allow_source_export) is bool,
                   "repair flags must be booleans")
            live = candidate_json is None
            if live:
                _check(allow_model_call and allow_source_export,
                       "model call and source export require explicit opt-in")
                _check(self._repair_client is not None and
                       self._source_export_authorizer is not None,
                       "live repair is not configured")
            else:
                _check(type(candidate_json) is str and
                       not allow_model_call and not allow_source_export,
                       "offline repair cannot enable model or export")
            s = self._current(session_id, actor_id)
            _check(s.repair_patch is None, "repair was already reviewed")
            if live:
                _check(not s.repair_model_attempted,
                       "model request was already attempted")
            try:
                policy = NexusWorkspacePolicy(
                    self._workspace,
                    rules=(WorkspaceRule(
                        s.mission_id, s.target, frozenset({"read_text"})
                    ),),
                )
                proposer = NexusCodeProposer(
                    policy, client=self._repair_client if live else None,
                    allow_text_targets=self._allow_text_targets,
                )
                context = proposer.prepare(
                    s.mission_id, s.task, target_files=(s.target,)
                )
                _check(context.sources[0].source_text_sha256 == s.target_sha256,
                       "target changed after inspection")
                if live:
                    try:
                        permitted = self._source_export_authorizer(
                            actor_id, s.mission_id, self._root_sha,
                            sha256(s.target.encode("utf-8")).hexdigest(),
                            s.target_sha256,
                        )
                    except Exception as exc:
                        raise NexusMissionEngineeringHostError(
                            "source export authorization unavailable"
                        ) from exc
                    _check(permitted is True, "source export denied")
                    # Set BEFORE the potentially paid request; never auto-retry.
                    s.repair_model_attempted = True
                    proposal = proposer.propose(context)
                    s.repair_model_completed = True
                else:
                    proposal = proposer.review_response(context, candidate_json)
                _check(bool(proposal.edits), "candidate contains no repair")
                builder = NexusDiffBuilder(proposer)
                patch = builder.build(context, proposal, require_fresh=True)
                _check(builder.verify(patch, context, proposal, require_fresh=True),
                       "patch verification failed")
            except (NexusWorkspacePolicyError, NexusCodeProposalError,
                    NexusDiffBuilderError, NexusWorkspaceError, ValueError) as exc:
                raise NexusMissionEngineeringHostError(
                    "repair candidate rejected"
                ) from exc
            except Exception as exc:
                # Includes transport failures: model request may have incurred costs.
                raise NexusMissionEngineeringHostError(
                    "repair model request failed; do not retry automatically"
                ) from exc
            s.repair_context = context
            s.repair_proposal = proposal
            s.repair_builder = builder
            s.repair_patch = patch
            return patch.to_dict(include_diffs=False)

    def repair_preview(self, session_id: str, *, actor_id: str,
                       include_diffs: bool = False) -> dict[str, Any]:
        """Recheck source freshness; expose diff text only with explicit opt-in."""
        with self._lock:
            _check(type(include_diffs) is bool, "invalid diff-export option")
            s = self._current(session_id, actor_id)
            _check(s.repair_patch is not None and s.repair_builder is not None
                   and s.repair_context is not None and s.repair_proposal is not None,
                   "no reviewed repair")
            try:
                _check(s.repair_builder.verify(
                    s.repair_patch, s.repair_context, s.repair_proposal,
                    require_fresh=True,
                ), "patch verification failed")
            except (NexusDiffBuilderError, NexusCodeProposalError) as exc:
                raise NexusMissionEngineeringHostError(
                    "reviewed repair is no longer valid"
                ) from exc
            return s.repair_patch.to_dict(include_diffs=include_diffs)

    def materialize_repair_candidate(self, session_id: str, *, actor_id: str,
                                     sandbox_bridge: NexusMissionSandboxExecutionBridge,
                                     allow_candidate_write: bool = False) -> dict[str, Any]:
        """Apply one reviewed patch ONLY to an existing, trusted candidate tree.

        The host must provision and sanitize TWO separate trees beforehand:
        the policy workspace and the Docker-mounted checkout, both external to
        the live repo, with no secrets or untrusted concurrent writers. No
        directories or live repository files are created/changed here. Candidate writes
        require an explicit caller opt-in and an independent host authorization.
        No Docker, model calls, sandbox approvals or tests are invoked here.
        A failed/ambiguous write is never automatically retried.
        """
        from .mission_sandbox_execution_bridge import NexusMissionSandboxExecutionBridge
        from ..trust.sandbox_runner import DockerSandboxBackend
        with self._lock:
            _check(allow_candidate_write is True, "explicit candidate-write opt-in required")
            _check(type(sandbox_bridge) is NexusMissionSandboxExecutionBridge,
                   "trusted sandbox bridge required")
            s = self._current(session_id, actor_id)
            _check(not s.candidate_write_attempted and not any(
                row.get("phase") != "baseline" or "result" not in row
                for row in s.repair_sandbox_runs.values()),
                   "candidate was already written or baseline validation remains active")
            _check(s.repair_sandbox_bridge is None or
                   s.repair_sandbox_bridge is sandbox_bridge,
                   "candidate must use the baseline sandbox bridge")
            _check(self._repair_effects_authorizer is not None,
                   "candidate write requires a trusted host authorizer")
            _check(s.repair_patch is not None and s.repair_builder is not None and
                   s.repair_context is not None and s.repair_proposal is not None,
                   "no reviewed repair is available")
            _check(len(s.repair_patch.files) == 1 and
                   s.repair_patch.files[0].relative_path == s.target,
                   "only single-target reviewed repairs can be materialized")
            _check(sandbox_bridge.registry is self.bridge.service._registry,
                   "sandbox and engineering missions must share a registry")
            candidate_workspace = sandbox_bridge.runner.policy.workspace
            backend = sandbox_bridge.runner.backend
            _check(type(backend) is DockerSandboxBackend,
                   "candidate materialization requires trusted Docker backend")
            candidate_root = candidate_workspace.root
            docker_root = backend.sandbox_checkout
            live_root = self._workspace.root
            roots = (live_root, candidate_root, docker_root)
            _check(all(left != right and left not in right.parents and
                       right not in left.parents
                       for i, left in enumerate(roots) for right in roots[i + 1:]),
                   "live, sandbox policy and Docker trees must not overlap")
            patch_file = s.repair_patch.files[0]
            try:
                # Docker operates on a separate, read-only mounted checkout.
                # Both staging trees must independently match the reviewed base.
                docker_workspace = NexusWorkspaceManager(docker_root)
                live_index = self._index.build()
                original_files = {item["relative_path"]: item["source_text_sha256"]
                                  for item in live_index["files"]}
                _check(bool(live_index["complete"]) and s.index_complete and
                       s.target in original_files,
                       "incomplete live index; candidate cannot be verified")
                for tree in (candidate_workspace, docker_workspace):
                    tree_index = NexusRepositoryIndex(tree, include_text_targets=self._allow_text_targets).build()
                    staged_files = {item["relative_path"]: item["source_text_sha256"]
                                    for item in tree_index["files"]}
                    _check(bool(tree_index["complete"]) and
                           original_files == staged_files,
                           "candidate baseline differs from reviewed repository")
                # Check Docker's independent sanitized-checkout restrictions.
                backend._validate_checkout()
                updated = s.repair_builder.render_reviewed_text(
                    s.repair_patch, s.repair_context, s.repair_proposal,
                    s.target, require_fresh=True,
                )
                updated_bytes = updated.encode("utf-8")
                _check(sha256(updated_bytes).hexdigest() == patch_file.proposed_text_sha256,
                       "reviewed candidate bytes have an unexpected hash")
                original_source = s.repair_context.sources[0]._source.encode("utf-8")
                _check(sha256(original_source).hexdigest() == patch_file.base_text_sha256,
                       "captured source bytes disagree with reviewed base")
                targets = tuple(tree._resolve(s.target, require_file=True)
                                for tree in (candidate_workspace, docker_workspace))
                _check(all(target.stat().st_size == len(original_source) and
                           target.read_bytes() == original_source for target in targets),
                       "candidate file bytes differ from reviewed base")
            except (NexusWorkspaceError, NexusRepositoryIndexError,
                    NexusDiffBuilderError, OSError, ValueError) as exc:
                raise NexusMissionEngineeringHostError(
                    "candidate checkouts preflight rejected") from exc

            action_digest = _digest({
                "action": "materialize_reviewed_repair_candidate.v1",
                "mission_id": s.mission_id,
                "candidate_root_sha256": sha256(str(candidate_root).encode("utf-8")).hexdigest(),
                "docker_root_sha256": sha256(str(docker_root).encode("utf-8")).hexdigest(),
                "target_path_sha256": sha256(s.target.encode("utf-8")).hexdigest(),
                "base_sha256": patch_file.base_text_sha256,
                "proposed_sha256": patch_file.proposed_text_sha256,
                "patchset_sha256": s.repair_patch.patchset_sha256,
            })
            try:
                authorized = self._repair_effects_authorizer(
                    actor_id, s.mission_id, s.repair_patch.patchset_sha256,
                    action_digest,
                )
            except Exception as exc:
                raise NexusMissionEngineeringHostError(
                    "candidate write authorization unavailable") from exc
            _check(authorized is True, "host denied candidate write")

            s.candidate_write_attempted = True  # fail closed on ambiguous effects
            try:
                # Host must prevent concurrent untrusted writes. A partial
                # two-tree application is NOT usable and must be discarded.
                for target in targets:
                    temporary: str | None = None
                    try:
                        _check(target.read_bytes() == original_source,
                               "candidate changed before materialization")
                        fd, temporary = tempfile.mkstemp(
                            prefix=".colossus-candidate-", suffix=".tmp",
                            dir=target.parent,
                        )
                        with os.fdopen(fd, "wb") as handle:
                            handle.write(updated_bytes)
                            handle.flush()
                            os.fsync(handle.fileno())
                        # Preserve the checkout's original readable mode.
                        # mkstemp's default 0600 would deny Docker's 65534 user.
                        os.chmod(temporary, target.stat().st_mode & 0o777)
                        _check(target.read_bytes() == original_source,
                               "candidate changed during materialization")
                        os.replace(temporary, target)
                        temporary = None
                    finally:
                        if temporary is not None:
                            try:
                                os.unlink(temporary)
                            except OSError:
                                pass
                _check(all(sha256(target.read_bytes()).hexdigest() ==
                           patch_file.proposed_text_sha256 for target in targets),
                       "candidate write could not be verified")
                backend._validate_checkout()
                s.candidate_write_confirmed = True
            except (OSError, ValueError) as exc:
                raise NexusMissionEngineeringHostError(
                    "candidate write failed or became ambiguous; discard both checkouts") from exc

            return {
                "patchset_sha256": s.repair_patch.patchset_sha256,
                "candidate_target_sha256": patch_file.proposed_text_sha256,
                "candidate_write_confirmed": True,
                "live_repository_modified": False,
                "sandbox_executed": False,
                "verified_repair": False,
                "next_step": "stage_repair_validation",
            }

    def stage_repair_validation(self, session_id: str, *, actor_id: str,
                                sandbox_bridge: NexusMissionSandboxExecutionBridge,
                                recipe: str, tool_id: str,
                                test_file: str | None = None,
                                baseline: bool = False) -> dict[str, Any]:
        """Stage a one-use recipe on a baseline or patched candidate checkout.

        The trusted host independently constructs the candidate workspace and
        Docker checkout; this method NEVER writes the proposed patch. In baseline
        mode (before any materialization) the checkout must match the original.
        In candidate mode the reviewed target must match the proposed SHA-256.
        Incomplete indexes are rejected instead of treating unknown changes as safe.
        Test recipes must target unchanged test files in that candidate snapshot.
        No approval is granted, no tools run, and a passing syntax check alone
        must never be interpreted as a verified repair.
        """
        # Late import keeps all existing read-only host workflows independent
        # of the optional Trust/Sandbox wiring at module-import time.
        from .mission_sandbox_execution_bridge import (
            NexusMissionSandboxExecutionBridge, NexusMissionSandboxBridgeError,
            verify_sandbox_bridge_receipt,
        )
        from ..trust.sandbox_runner import DockerSandboxBackend, NexusSandboxError
        with self._lock:
            _check(type(sandbox_bridge) is NexusMissionSandboxExecutionBridge,
                   "trusted sandbox bridge required")
            _check(type(recipe) is str and recipe in {
                "python_ast", "node_check", "pytest_file", "node_test_file"
            }, "unsupported validation recipe")
            _check(type(tool_id) is str and bool(tool_id), "tool ID required")
            _check(type(baseline) is bool, "baseline flag must be boolean")
            s = self._current(session_id, actor_id)
            _check(s.repair_patch is not None and s.repair_builder is not None and
                   s.repair_context is not None and s.repair_proposal is not None,
                   "repair must be reviewed before validation")
            _check(len(s.repair_patch.files) == 1 and
                   s.repair_patch.files[0].relative_path == s.target,
                   "only single-target reviewed repairs may be staged")
            _check(len(s.repair_sandbox_runs) < 4,
                   "validation recipe session limit reached")
            _check(not baseline or not s.candidate_write_attempted,
                   "baseline must run before patch materialization")
            _check(baseline or not any(
                row.get("phase") == "baseline" and "result" not in row
                for row in s.repair_sandbox_runs.values()),
                   "finish baseline validations before staging candidate tests")
            _check(s.repair_sandbox_bridge is None or
                   s.repair_sandbox_bridge is sandbox_bridge,
                   "all candidate recipes must use the same sandbox bridge")
            _check(sandbox_bridge.registry is self.bridge.service._registry,
                   "sandbox and engineering missions must share a registry")
            _check(sandbox_bridge.effects_enabled is False or
                   self._repair_effects_authorizer is not None,
                   "effect-enabled bridge requires independent host authorization")
            patched_root = sandbox_bridge.runner.policy.workspace.root
            live_root = self._workspace.root
            _check(patched_root != live_root and
                   patched_root not in live_root.parents and
                   live_root not in patched_root.parents,
                   "candidate must use a separate, non-overlapping workspace")
            file = s.repair_patch.files[0]
            try:
                _check(s.repair_builder.verify(s.repair_patch, s.repair_context,
                       s.repair_proposal, require_fresh=True),
                       "repair patch no longer matches source")
                candidate_index = NexusRepositoryIndex(
                    sandbox_bridge.runner.policy.workspace,
                    include_text_targets=self._allow_text_targets).build()
                live_index = self._index.build()
                _check(bool(candidate_index["complete"]) and
                       bool(live_index["complete"]) and s.index_complete,
                       "incomplete checkout index; cannot bind repair")
                live_files = {r["relative_path"]: r["source_text_sha256"]
                              for r in live_index["files"]}
                candidate_files = {r["relative_path"]: r["source_text_sha256"]
                                   for r in candidate_index["files"]}
                _check(set(live_files) == set(candidate_files) and
                       file.relative_path in live_files,
                       "candidate checkout indexed paths differ")
                expected_target_sha = (file.base_text_sha256 if baseline
                                       else file.proposed_text_sha256)
                _check(all(candidate_files[path] == (
                    expected_target_sha if path == s.target else digest
                ) for path, digest in live_files.items()),
                       "candidate contains unreviewed source changes")
                # A byte-accurate check is necessary because the sandbox hashes
                # the raw file bytes, while indexed text can normalize newlines.
                _check(sandbox_bridge.runner._source_sha(
                    s.mission_id, s.target) == expected_target_sha,
                    "candidate target bytes do not match reviewed baseline or patch")
                backend = sandbox_bridge.runner.backend
                if type(backend) is DockerSandboxBackend:
                    # The runner's normal recipe preflight checks the chosen
                    # test file, not the imported/repaired target. Check BOTH.
                    backend.preflight(patched_root, s.target,
                                      expected_target_sha,
                                      sandbox_bridge.runner.limits)
                if recipe in {"python_ast", "node_check"}:
                    _check(test_file is None, "syntax recipe targets the repaired file")
                    path = s.target
                else:
                    _check(type(test_file) is str and test_file in live_files and
                           test_file != s.target,
                           "test must be an unchanged indexed file")
                    _check(candidate_files[test_file] == live_files[test_file] and
                           sandbox_bridge.runner._source_sha(s.mission_id, test_file)
                           == live_files[test_file],
                           "test bytes changed or cannot be verified")
                    path = test_file
                receipt = sandbox_bridge.stage(
                    s.mission_id, recipe, path, tool_id=tool_id,
                    expected_registry_sha256=s.registry_sha256)
                _check(verify_sandbox_bridge_receipt(receipt) and
                       receipt["status"] == "review_required",
                       "sandbox rejected staged validation")
            except (NexusRepositoryIndexError, NexusWorkspaceError,
                    NexusDiffBuilderError, NexusMissionSandboxBridgeError,
                    NexusSandboxError, ValueError, OSError) as exc:
                raise NexusMissionEngineeringHostError(
                    "patched candidate validation staging rejected") from exc
            sandbox_id = receipt["session_id"]
            if s.repair_sandbox_bridge is None:
                s.repair_sandbox_bridge = sandbox_bridge
            s.repair_sandbox_runs[sandbox_id] = {
                "staged": receipt, "recipe": recipe,
                "phase": "baseline" if baseline else "candidate",
                "path_sha256": sha256(path.encode("utf-8")).hexdigest(),
            }
            return {
                "patchset_sha256": s.repair_patch.patchset_sha256,
                "sandbox_receipt": receipt, "patch_applied_by_host": False,
                "effects_executed": False, "candidate_checkout_unmodified": True,
                "phase": "baseline" if baseline else "candidate",
                "verified_repair": False,
            }

    def request_repair_validation_review(self, session_id: str, *, actor_id: str,
                                         sandbox_session_id: str) -> dict[str, Any]:
        """Request exact human review; NEVER grant it automatically."""
        from .mission_sandbox_execution_bridge import NexusMissionSandboxBridgeError
        with self._lock:
            s = self._current(session_id, actor_id)
            _check(sandbox_session_id in s.repair_sandbox_runs,
                   "validation does not belong to this repair")
            bridge = s.repair_sandbox_bridge
            _check(bridge is not None, "sandbox bridge is unavailable")
            try:
                return bridge.request_review(sandbox_session_id)
            except NexusMissionSandboxBridgeError as exc:
                raise NexusMissionEngineeringHostError("review request rejected") from exc

    def run_repair_validation(self, session_id: str, *, actor_id: str,
                              sandbox_session_id: str,
                              allow_effects: bool = False) -> dict[str, Any]:
        """One explicit attempt after independent host grant AND gate approval.

        The existing sandbox bridge admits only its vetted Docker backend.
        No code is executed in this engineering host or the live repository.
        """
        from .mission_sandbox_execution_bridge import (
            NexusMissionSandboxBridgeError, verify_sandbox_bridge_receipt,
        )
        from ..trust.sandbox_runner import DockerSandboxBackend, NexusSandboxError
        with self._lock:
            _check(allow_effects is True, "explicit effects opt-in required")
            s = self._current(session_id, actor_id)
            entry = s.repair_sandbox_runs.get(sandbox_session_id)
            _check(entry is not None and "result" not in entry and
                   not entry.get("attempted", False),
                   "validation unknown or already attempted")
            _check(self._repair_effects_authorizer is not None,
                   "trusted effects authorizer is unavailable")
            bridge = s.repair_sandbox_bridge
            _check(bridge is not None and s.repair_patch is not None,
                   "staged repair unavailable")
            try:
                current = bridge.inspect(sandbox_session_id)
            except NexusMissionSandboxBridgeError as exc:
                raise NexusMissionEngineeringHostError("sandbox inspection failed") from exc
            _check(current["status"] == "approval_pending",
                   "sandbox stage is stale or not awaiting approval")
            _check(type(bridge.runner.backend) is DockerSandboxBackend,
                   "Docker backend required for repair execution")
            expected_sha = (s.repair_patch.files[0].base_text_sha256
                            if entry["phase"] == "baseline" else
                            s.repair_patch.files[0].proposed_text_sha256)
            try:
                _check(bridge.runner._source_sha(s.mission_id, s.target)
                       == expected_sha, "policy candidate source is stale")
                bridge.runner.backend.preflight(
                    bridge.runner.policy.workspace.root, s.target,
                    expected_sha, bridge.runner.limits)
            except (NexusSandboxError, ValueError, OSError) as exc:
                raise NexusMissionEngineeringHostError(
                    "Docker candidate target changed before execution") from exc
            try:
                permitted = self._repair_effects_authorizer(
                    actor_id, s.mission_id, s.repair_patch.patchset_sha256,
                    entry["staged"]["action_sha256"])
            except Exception as exc:
                raise NexusMissionEngineeringHostError(
                    "host validation authorization unavailable") from exc
            _check(permitted is True, "host denied validation effects")
            entry["attempted"] = True  # Fail closed on ambiguous external effects
            try:
                report = bridge.run_approved(sandbox_session_id)
                _check(verify_sandbox_bridge_receipt(report),
                       "sandbox report failed integrity check")
            except (NexusMissionSandboxBridgeError, ValueError) as exc:
                raise NexusMissionEngineeringHostError(
                    "validation execution rejected; retry requires a new stage") from exc
            entry["result"] = report
            return {
                "patchset_sha256": s.repair_patch.patchset_sha256,
                "sandbox_receipt": report,
                "phase": entry["phase"],
                "validated_candidate_recipe": (entry["phase"] == "candidate" and
                                               report["status"] == "sandbox_passed"),
                "verified_repair": False,  # Requires independent functional checks.
                "live_repository_modified": False,
            }

    def repair_verification_proof(self, session_id: str, *, actor_id: str,
                                  proof_graph: "NexusProofGraph") -> dict[str, Any]:
        """Record reproducible test evidence; never infer success from syntax alone.

        A narrowly scoped regression is supported only when the exact same test
        recipe/file fails on an unmodified baseline and passes on the reviewed
        patched candidate. Both executions must have consumed real approvals.
        This cannot establish correctness outside that test or authenticity of
        host-supplied sources. ProofGraph remains a metadata ledger, not a seal.
        """
        from ..proof.proof_graph import NexusProofGraph, NexusProofGraphError
        from .mission_sandbox_execution_bridge import verify_sandbox_bridge_receipt
        from ..trust.sandbox_runner import DockerSandboxBackend, NexusSandboxError

        with self._lock:
            _check(type(proof_graph) is NexusProofGraph,
                   "existing ProofGraph required")
            s = self._current(session_id, actor_id)
            _check(proof_graph.mission_id == s.mission_id,
                   "ProofGraph mission mismatch")
            _check(s.repair_patch is not None, "no reviewed patch")
            rows = []
            for sandbox_id, entry in sorted(s.repair_sandbox_runs.items()):
                report = entry.get("result")
                if report is None:
                    continue
                _check(verify_sandbox_bridge_receipt(report) and
                       report["session_id"] == sandbox_id and
                       report["action_sha256"] == entry["staged"]["action_sha256"] and
                       report["sandbox_plan_sha256"] ==
                       entry["staged"]["sandbox_plan_sha256"] and
                       report["approval_consumed"] is True and
                       report["effects_attempted"] is True,
                       "sandbox evidence is incomplete or inconsistent")
                rows.append((sandbox_id, entry, report))
            _check(rows, "no executed sandbox evidence")

            candidate_tests = {(r["recipe"], r["path_sha256"])
                               for _, r, o in rows
                               if r["phase"] == "candidate" and
                               r["recipe"] in {"pytest_file", "node_test_file"} and
                               o["status"] == "sandbox_passed"}
            baseline_tests = {(r["recipe"], r["path_sha256"])
                              for _, r, o in rows
                              if r["phase"] == "baseline" and
                              r["recipe"] in {"pytest_file", "node_test_file"} and
                              o["status"] == "sandbox_failed"}
            matches = candidate_tests & baseline_tests
            verified = bool(matches) and s.candidate_write_confirmed
            if verified:
                bridge = s.repair_sandbox_bridge
                _check(bridge is not None and
                       type(bridge.runner.backend) is DockerSandboxBackend,
                       "Docker validation backend unavailable")
                target = s.repair_patch.files[0]
                try:
                    _check(bridge.runner._source_sha(s.mission_id, s.target)
                           == target.proposed_text_sha256,
                           "candidate source has changed after testing")
                    bridge.runner.backend.preflight(
                        bridge.runner.policy.workspace.root, s.target,
                        target.proposed_text_sha256, bridge.runner.limits)
                except (NexusSandboxError, OSError, ValueError) as exc:
                    raise NexusMissionEngineeringHostError(
                        "patched checkout no longer matches reviewed repair") from exc

            # Validate every receipt before adding any node. A graph snapshot is
            # bounded, deterministic and contains no raw source, output or paths.
            try:
                mission = proof_graph.add_node("mission", s.mission_id,
                                               status="observed")
                run = proof_graph.add_node("run", s.token, status="observed",
                                           source="engineering")
                patch = proof_graph.add_node(
                    "patch", s.repair_patch.patchset_sha256,
                    status="review_required", source="engineering", stage="review",
                    digest_sha256=s.repair_patch.patchset_sha256)
                proof_graph.add_edge("contains", mission, run)
                for sandbox_id, entry, report in rows:
                    outcome = report["status"]
                    status = ("passed" if outcome == "sandbox_passed" else
                              "failed" if outcome == "sandbox_failed" else "inconclusive")
                    check = proof_graph.add_node(
                        "test_run", sandbox_id, status=status, source="trust",
                        stage="test", digest_sha256=report["receipt_sha256"])
                    evidence = proof_graph.add_node(
                        "evidence", report["receipt_sha256"], status=status,
                        source="trust", stage="verification",
                        digest_sha256=report["receipt_sha256"])
                    check_key = _digest({
                        "patchset": s.repair_patch.patchset_sha256,
                        "sandbox_id": sandbox_id,
                        "phase": entry["phase"],
                        "recipe": entry["recipe"],
                        "path_sha256": entry["path_sha256"],
                    })
                    assertion = proof_graph.add_node(
                        "check", check_key, status=status,
                        source="engineering", stage="verification")
                    proof_graph.add_edge("contains", run, check)
                    proof_graph.add_edge("evaluates", assertion, check)
                    proof_graph.add_edge("evaluates", assertion, patch)
                    proof_graph.add_edge("documents", evidence, check)
                    proof_graph.add_edge("documents", evidence, assertion)
                if verified:
                    key = _digest({"patch": s.repair_patch.patchset_sha256,
                                   "checks": sorted(matches)})
                    claim = proof_graph.add_node(
                        "claim", key, status="verified", source="engineering",
                        stage="verification", digest_sha256=key,
                        metrics={"independent_checks_run": len(matches),
                                 "independent_checks_passed": len(matches)})
                    for _, entry, report in rows:
                        if (entry["recipe"], entry["path_sha256"]) in matches and (
                            (entry["phase"] == "baseline" and
                             report["status"] == "sandbox_failed") or
                            (entry["phase"] == "candidate" and
                             report["status"] == "sandbox_passed")
                        ):
                            node = proof_graph.add_node(
                                "evidence", report["receipt_sha256"],
                                status=("failed" if entry["phase"] == "baseline"
                                        else "passed"), source="trust",
                                stage="verification",
                                digest_sha256=report["receipt_sha256"])
                            proof_graph.add_edge("supports", node, claim)
                snapshot = proof_graph.snapshot()
                _check(NexusProofGraph.verify_snapshot(snapshot),
                       "ProofGraph snapshot integrity failed")
            except NexusProofGraphError as exc:
                raise NexusMissionEngineeringHostError(
                    "repair proof recording rejected") from exc
            s.verified_regression_test = verified
            s.repair_proof_graph_sha256 = snapshot["graph_sha256"]
            return {
                "patchset_sha256": s.repair_patch.patchset_sha256,
                "verified_repair": verified,
                "verification_scope": ("matching_sandbox_test_only" if verified
                                       else "insufficient_before_after_evidence"),
                "matching_regression_tests": len(matches) if verified else 0,
                "executed_validation_count": len(rows),
                "proof_graph_sha256": snapshot["graph_sha256"],
                "proof_graph_snapshot": snapshot,
                "authenticity_verified": False,
                "live_repository_modified": False,
            }

    def status(self, session_id: str, *, actor_id: str) -> dict[str, Any]:
        with self._lock:
            s = self._current(session_id, actor_id, require_fresh_registry=False)
            return self._summary(s)


__all__ = ["ENGINEERING_HOST_SCHEMA", "NexusMissionEngineeringHost",
           "NexusMissionEngineeringHostError", "WorkspaceAuthorizer",
           "SourceExportAuthorizer", "RepairEffectsAuthorizer"]
