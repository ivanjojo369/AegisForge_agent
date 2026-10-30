"""Read-only Workspace/Engineering extensions for Nexus Mission Control.

Destination: src/aegisforge/nexus/mission_control/mission_workspace_engineering_adapters.py

Adds exactly two *allowlisted* inspect operations to the existing in-memory
Mission Read Adapters:

  workspace.inventory: bounded metadata-only filesystem INVENTORY of a trusted
      host-selected NexusWorkspaceManager root/scope, never file content.
  engineering.propose_change: inspect an ALREADY-PREPARED CodeProposal object;
      NEVER call a model, propose a change, apply a patch, or execute tests.

Bridge plans are checked against the *expanded* catalog and current mission
registry before each read; reports state integrity-only, not authentication,
permissions or approval. File listings are not content integrity proofs:
a same-size file modification may be invisible to metadata-only inventory.
A post-read source recheck mitigates races but is not an atomic transaction.
The host must select a minimal, approved workspace root and inject authentic
objects; never accept paths, source objects, or a proposal from untrusted input.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping

from .mission_bridge import MissionBridgeCatalog, MissionBridgePort
from .mission_read_adapters import (
    READ_ADAPTER_SCHEMA,
    READ_ONLY_CATALOG,
    NexusMissionReadAdapters,
    NexusMissionReadAdapterError,
    MissionReadReceipt,
)
from .mission_registry import NexusMissionRegistry
from ..workspace.manager import NexusWorkspaceManager, NexusWorkspaceError
from ..engineering.code_proposer import CodeProposal, ProposedEdit
from ..lab.scenario_catalog import NexusScenarioCatalog
from ..proof.proof_graph import NexusProofGraph

EXTENDED_READ_SCHEMA = "aegisforge.nexus.mission_control.workspace_engineering_read.v1"
_MAX_PROPOSAL_METADATA_BYTES = 65_536
_MAX_INVENTORY_FILES = 4096


def _reject(unless: bool, message: str) -> None:
    if not unless:
        raise NexusMissionReadAdapterError(message)


def _encode(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusMissionReadAdapterError("invalid source metadata") from exc


def _digest(value: Any) -> str:
    return sha256(_encode(value)).hexdigest()


# Reuse the existing contract format so Mission Bridge can validate it without
# custom hooks or changes to any existing Colossus module.
_ADDITIONAL_DESCRIPTORS = (
    ("workspace", "inventory", "local.workspace_inventory.v1",
     ("file_count", "total_bytes", "text_candidate_count", "partial", "metadata_only")),
    ("engineering", "propose_change", "local.engineering_proposal_preview.v1",
     ("proposal_status", "edit_count", "needs_review", "context_sha256",
      "proposal_metadata_sha256", "changes_applied_by_adapter")),
)


def _extended_catalog() -> MissionBridgeCatalog:
    ports = list(READ_ONLY_CATALOG.ports)
    for component, operation, adapter_id, fields in _ADDITIONAL_DESCRIPTORS:
        descriptor = {
            "schema": READ_ADAPTER_SCHEMA,
            "component": component,
            "operation": operation,
            "adapter_id": adapter_id,
            "read_only": True,
            "summary_fields": list(fields),
        }
        ports.append(MissionBridgePort(component, operation, adapter_id,
                                       _digest(descriptor)))
    ports.sort(key=lambda p: (p.component, p.operation))
    return MissionBridgeCatalog(tuple(ports))


WORKSPACE_ENGINEERING_READ_CATALOG = _extended_catalog()


class NexusMissionWorkspaceEngineeringReadAdapters(NexusMissionReadAdapters):
    """Read-only bridge covering the original 3 sources plus two new sources.

    Source objects MUST be injected by a trusted host, never instantiated from
    a user-provided path or based solely on untrusted registry claims.
    """

    def __init__(
        self,
        registry: NexusMissionRegistry,
        *,
        workspace: NexusWorkspaceManager | None = None,
        workspace_scope: str = ".",
        engineering_proposal: CodeProposal | None = None,
        scenarios: NexusScenarioCatalog | None = None,
        proof_graph: NexusProofGraph | None = None,
    ) -> None:
        super().__init__(registry, scenarios=scenarios, proof_graph=proof_graph)
        _reject(workspace is None or type(workspace) is NexusWorkspaceManager,
                "workspace must be a trusted NexusWorkspaceManager")
        _reject(engineering_proposal is None or type(engineering_proposal) is CodeProposal,
                "engineering proposal must be a CodeProposal")
        _reject(type(workspace_scope) is str and
                1 <= len(workspace_scope) <= 256 and
                "\x00" not in workspace_scope and
                (workspace_scope == "." or
                 (not workspace_scope.startswith(("/", "\\")) and
                  ":" not in workspace_scope and
                  all(part not in ("", ".", "..")
                      for part in workspace_scope.replace("\\", "/").split("/")))),
                "invalid approved inventory scope")
        self._workspace = workspace
        self._workspace_scope = workspace_scope
        self._engineering_proposal = engineering_proposal

    @property
    def catalog(self) -> MissionBridgeCatalog:
        return WORKSPACE_ENGINEERING_READ_CATALOG

    def _inventory_data(self) -> tuple[str, dict[str, Any]]:
        _reject(self._workspace is not None, "workspace source missing")
        try:
            inventory = self._workspace.inventory(self._workspace_scope,
                                                  with_hashes=False)
        except (NexusWorkspaceError, OSError, ValueError, RuntimeError) as exc:
            raise NexusMissionReadAdapterError("workspace inventory unavailable") from exc
        files = inventory.get("files")
        _reject(type(files) is list and len(files) <= _MAX_INVENTORY_FILES,
                "invalid or excessive workspace inventory")
        _reject(all(type(row) is dict and type(row.get("size_bytes")) is int and
                    row["size_bytes"] >= 0 and type(row.get("relative_path")) is str
                    for row in files), "invalid workspace entries")
        # Hash the full bounded metadata, including filenames, but do NOT emit
        # file paths, root, or individual entries. No content is read or hashed.
        anchor = {
            "schema": EXTENDED_READ_SCHEMA,
            "scope": self._workspace_scope,
            "root": str(self._workspace.root),
            "files": [(row["relative_path"], row["size_bytes"]) for row in files],
            "truncated": inventory.get("truncated"),
            "warnings": inventory.get("warnings"),
        }
        _reject(len(_encode(anchor)) <= 350_000,
                "inventory metadata exceeds safe budget")
        sha = _digest(anchor)
        summary = {
            "file_count": len(files),
            "total_bytes": sum(row["size_bytes"] for row in files),
            "text_candidate_count": sum(row.get("is_text_candidate") is True for row in files),
            "partial": bool(inventory.get("truncated") or inventory.get("warnings")),
            "metadata_only": True,
        }
        return sha, summary

    def _proposal_data(self, mission_id: str) -> tuple[str, dict[str, Any]]:
        proposal = self._engineering_proposal
        _reject(proposal is not None, "engineering proposal source missing")
        _reject(type(proposal.edits) is tuple and len(proposal.edits) <= 12 and
                all(type(edit) is ProposedEdit for edit in proposal.edits),
                "invalid engineering edits")
        _reject(type(proposal.usage) is dict, "invalid engineering usage")
        _reject(type(proposal.mission_id) is str and proposal.mission_id == mission_id,
                "engineering proposal belongs to another mission")
        _reject(type(proposal.status) is str and proposal.status == "proposed",
                "engineering proposal is not in proposed state")
        _reject(type(proposal.context_sha256) is str and
                len(proposal.context_sha256) == 64 and
                all(c in "0123456789abcdef" for c in proposal.context_sha256),
                "invalid proposal context digest")
        metadata = proposal.to_dict(include_diffs=False)
        _reject(type(metadata) is dict and
                len(_encode(metadata)) <= _MAX_PROPOSAL_METADATA_BYTES and
                metadata.get("schema") == "aegisforge.nexus.engineering.code_proposal.v1" and
                metadata.get("applied") is False and
                metadata.get("needs_review") is bool(proposal.edits),
                "invalid proposal metadata")
        sha = _digest(metadata)
        return sha, {
            "proposal_status": "proposed",
            "edit_count": len(proposal.edits),
            "needs_review": bool(proposal.edits),
            "context_sha256": proposal.context_sha256,
            "proposal_metadata_sha256": sha,
            "changes_applied_by_adapter": False,
        }

    def _source_summary(self, component: str, operation: str,
                        mission_id: str, product: str) -> tuple[str, dict[str, Any]]:
        if (component, operation) == ("workspace", "inventory"):
            return self._inventory_data()
        if (component, operation) == ("engineering", "propose_change"):
            return self._proposal_data(mission_id)
        return super()._source_summary(component, operation, mission_id, product)

    def _current_source_sha(self, component: str, operation: str) -> str:
        if (component, operation) == ("workspace", "inventory"):
            return self._inventory_data()[0]
        if (component, operation) == ("engineering", "propose_change"):
            # No plan mission arg in this private base hook. Preserve the
            # proposal's anchored mission, checked in _source_summary.
            _reject(self._engineering_proposal is not None, "engineering proposal missing")
            return self._proposal_data(self._engineering_proposal.mission_id)[0]
        return super()._current_source_sha(component, operation)

    def inspect(self, plan: Any, *, step_id: str) -> MissionReadReceipt:
        """Read declared metadata; Workspace inventory may STAT real files.

        This method never reads file contents, runs models, applies edits,
        invokes a test runner, writes files, or consumes Trust approvals.
        """
        return super().inspect(plan, step_id=step_id)


__all__ = [
    "EXTENDED_READ_SCHEMA", "WORKSPACE_ENGINEERING_READ_CATALOG",
    "NexusMissionWorkspaceEngineeringReadAdapters",
]
