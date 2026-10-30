"""Build canonical, review-only patch bundles from Nexus CodeProposer proposals.

Repository target: src/aegisforge/nexus/engineering/diff_builder.py

The upstream code_proposer.py produces one exact text replacement per existing
file, recorded as a unified diff with base/target SHA-256 digests. This module
*validates* those diffs against the captured sources and groups them into an
inspectable, deterministic patch bundle. No patch is applied, no file is
written, no code is executed, and no model or network call is made.

Trust boundary:
- Accept contexts/proposals only through a trusted NexusCodeProposer instance.
- Optionally re-read mission-authorized source files to reject stale proposals.
- Never treat a valid patch, content digest, or review artifact as approval.
- Even after external human review, a future patch applicator must acquire
  fresh host authorization, recheck file identity, and use an isolated runner.
- Unified diffs may contain secrets or sensitive source. They are hidden from
  repr and excluded from summary export by default.

The parser deliberately accepts only difflib.unified_diff()'s canonical,
non-binary, path-bound text format. If a malformed or nonstandard diff cannot
be reconstituted exactly, it is rejected rather than heuristically applied.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from difflib import unified_diff
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
from typing import Any

from .code_proposer import (
    CodeProposal,
    NexusCodeProposer,
    ProposalContext,
    ProposedEdit,
)


PATCH_SCHEMA = "aegisforge.nexus.engineering.patch_bundle.v1"
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@\n$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
# The proposer still limits model targets to code unless the local operator
# expressly enables review of other inventory-approved UTF-8 text formats.
from aegisforge.nexus.workspace.manager import _TEXT_SUFFIXES
_ALLOWED_SUFFIXES = _TEXT_SUFFIXES


class NexusDiffBuilderError(ValueError):
    """A proposed patch is stale, ambiguous, malformed, or out of scope."""


@dataclass(frozen=True, slots=True)
class DiffBuilderLimits:
    max_files: int = 12
    max_diff_chars_per_file: int = 45_000
    max_total_diff_chars: int = 180_000
    max_hunks_per_file: int = 80
    max_changed_lines: int = 3_000

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise NexusDiffBuilderError(f"{name} must be a positive integer")
        if self.max_files > 12 or self.max_total_diff_chars > 1_000_000:
            raise NexusDiffBuilderError("diff builder limits exceed supported bounds")


@dataclass(frozen=True, slots=True)
class PatchHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    additions: int
    deletions: int

    def to_dict(self) -> dict[str, int]:
        return {
            "old_start": self.old_start,
            "old_count": self.old_count,
            "new_start": self.new_start,
            "new_count": self.new_count,
            "additions": self.additions,
            "deletions": self.deletions,
        }


@dataclass(frozen=True, slots=True)
class PatchFile:
    relative_path: str
    base_text_sha256: str
    proposed_text_sha256: str
    additions: int
    deletions: int
    hunks: tuple[PatchHunk, ...]
    rationale: str
    unified_diff: str = field(repr=False)

    def to_dict(self, *, include_diff: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "relative_path": self.relative_path,
            "base_text_sha256": self.base_text_sha256,
            "proposed_text_sha256": self.proposed_text_sha256,
            "additions": self.additions,
            "deletions": self.deletions,
            "hunks": [hunk.to_dict() for hunk in self.hunks],
            "rationale": self.rationale,
        }
        if include_diff:
            data["unified_diff"] = self.unified_diff
        return data


@dataclass(frozen=True, slots=True)
class PatchBundle:
    patchset_sha256: str
    proposal_id: str
    mission_id: str
    context_sha256: str
    files: tuple[PatchFile, ...]
    additions: int
    deletions: int
    status: str = "review_required"

    def to_dict(self, *, include_diffs: bool = False) -> dict[str, Any]:
        """Metadata-only by default; diffs explicitly require opt-in."""
        return {
            "schema": PATCH_SCHEMA,
            "patchset_sha256": self.patchset_sha256,
            "proposal_id": self.proposal_id,
            "mission_id": self.mission_id,
            "context_sha256": self.context_sha256,
            "status": self.status,
            "applied": False,
            "needs_review": True,
            "file_count": len(self.files),
            "additions": self.additions,
            "deletions": self.deletions,
            "files": [f.to_dict(include_diff=include_diffs) for f in self.files],
        }


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _check_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise NexusDiffBuilderError(f"invalid {label}")


def _offset(start: int, count: int) -> int:
    """Convert GNU unified hunk position to zero-based index.

    A zero-line range starts *after* the named line, while a nonempty
    range's first line has the supplied 1-based number.
    """
    return start if count == 0 else start - 1


def _reconstruct(source: str, edit: ProposedEdit, limits: DiffBuilderLimits) -> tuple[str, tuple[PatchHunk, ...]]:
    """Parse a constrained diff and reconstruct a new string in memory.

    Every context/removal line must match the original exactly. The canonical
    digest and regenerated difflib diff are checked by the caller.
    """
    diff = edit.unified_diff
    path = edit.relative_path
    if (not isinstance(diff, str) or not diff or len(diff) > limits.max_diff_chars_per_file
            or "\x00" in diff):
        raise NexusDiffBuilderError("diff is empty, invalid or too large")
    lines = diff.splitlines(keepends=True)
    if len(lines) < 4 or lines[0] != f"--- a/{path}\n" or lines[1] != f"+++ b/{path}\n":
        raise NexusDiffBuilderError("diff headers do not identify the authorized file")
    source_lines = source.splitlines(keepends=True)
    output: list[str] = []
    cursor = 0
    i = 2
    hunks: list[PatchHunk] = []
    changed_lines = 0
    while i < len(lines):
        header = lines[i]
        match = _HUNK_RE.fullmatch(header)
        if match is None:
            raise NexusDiffBuilderError("unexpected text outside a canonical hunk")
        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) is not None else 1
        new_start = int(match.group(3))
        new_count = int(match.group(4)) if match.group(4) is not None else 1
        if old_start < 0 or new_start < 0 or old_count < 0 or new_count < 0:
            raise NexusDiffBuilderError("negative hunk positions are prohibited")
        old_offset = _offset(old_start, old_count)
        new_offset = _offset(new_start, new_count)
        if old_offset < cursor or old_offset > len(source_lines):
            raise NexusDiffBuilderError("hunk is overlapping or out of source bounds")
        output.extend(source_lines[cursor:old_offset])
        cursor = old_offset
        if new_offset != len(output):
            raise NexusDiffBuilderError("hunk's destination position is inconsistent")
        i += 1
        consumed = produced = additions = deletions = 0
        while i < len(lines) and not lines[i].startswith("@@ "):
            row = lines[i]
            # difflib emits no "\\ No newline at end of file" markers;
            # reject missing line terminators in hunks rather than guessing.
            if not row.endswith("\n") or len(row) < 2:
                raise NexusDiffBuilderError("ambiguous or unterminated diff line")
            flag, content = row[0], row[1:]
            if flag not in (" ", "+", "-"):
                raise NexusDiffBuilderError("unknown diff line prefix")
            if flag in (" ", "-"):
                if cursor >= len(source_lines) or source_lines[cursor] != content:
                    raise NexusDiffBuilderError("diff content disagrees with captured source")
                cursor += 1
                consumed += 1
            if flag in (" ", "+"):
                output.append(content)
                produced += 1
            if flag == "+":
                additions += 1
            elif flag == "-":
                deletions += 1
            i += 1
        if consumed != old_count or produced != new_count:
            raise NexusDiffBuilderError("hunk line totals do not match the header")
        if additions == 0 and deletions == 0:
            raise NexusDiffBuilderError("hunk does not change any lines")
        hunks.append(PatchHunk(old_start, old_count, new_start, new_count,
                               additions, deletions))
        changed_lines += additions + deletions
        if len(hunks) > limits.max_hunks_per_file or changed_lines > limits.max_changed_lines:
            raise NexusDiffBuilderError("patch exceeds hunk or changed-line limits")
    if not hunks:
        raise NexusDiffBuilderError("diff contains no hunks")
    output.extend(source_lines[cursor:])
    updated = "".join(output)
    if updated == source:
        raise NexusDiffBuilderError("patch does not alter the file")
    return updated, tuple(hunks)


class NexusDiffBuilder:
    """Assemble REVIEW-ONLY patch bundles from a trusted proposer session.

    Reusing the proposer object binds a ProposalContext to its original
    proposer owner. Call build(..., require_fresh=True) to read current source
    again under the same mission policy, rejecting known stale content.

    A successful result does not provide permission to write, run tests or
    deploy; it is merely an inspectable, reproducible patch description.
    """

    def __init__(self, proposer: NexusCodeProposer, *, limits: DiffBuilderLimits | None = None) -> None:
        if not isinstance(proposer, NexusCodeProposer):
            raise NexusDiffBuilderError("proposer must be NexusCodeProposer")
        self.proposer = proposer
        self.limits = limits if limits is not None else DiffBuilderLimits()
        if not isinstance(self.limits, DiffBuilderLimits):
            raise NexusDiffBuilderError("limits must be DiffBuilderLimits")

    def build(
        self,
        context: ProposalContext,
        proposal: CodeProposal,
        *,
        require_fresh: bool = True,
    ) -> PatchBundle:
        """Verify every provided diff and return a deterministic in-memory bundle.

        The caller supplies proposal, not raw arbitrary patch text. No changes
        are applied. With require_fresh=True, the source is re-read using
        workspace_policy's mission-scoped read_text permission.
        """
        if type(require_fresh) is not bool:
            raise NexusDiffBuilderError("require_fresh must be boolean")
        self.proposer._require_context(context)
        if not isinstance(proposal, CodeProposal) or proposal.status != "proposed":
            raise NexusDiffBuilderError("expected a nonempty, reviewable CodeProposal")
        if (proposal.mission_id != context.mission_id
                or proposal.context_sha256 != context.context_sha256):
            raise NexusDiffBuilderError("proposal does not match its context")
        if not isinstance(proposal.edits, tuple) or not 1 <= len(proposal.edits) <= self.limits.max_files:
            raise NexusDiffBuilderError("proposal file count is outside supported bounds")
        if len(proposal.edits) > len(context.sources):
            raise NexusDiffBuilderError("proposal has more edits than authorized sources")
        references = [s.to_dict() for s in context.sources]
        expected_context_hash = _digest(_canonical({
            "mission_id": context.mission_id,
            "request": context.request,
            "files": references,
        }))
        if expected_context_hash != context.context_sha256:
            raise NexusDiffBuilderError("context digest is inconsistent")
        if require_fresh and not self.proposer.revalidate(context)["fresh"]:
            raise NexusDiffBuilderError("source files changed; prepare a new proposal")
        sources = {src.relative_path: src for src in context.sources}
        if len(sources) != len(context.sources):
            raise NexusDiffBuilderError("context contains duplicate source paths")
        used: set[str] = set()
        files: list[PatchFile] = []
        total_chars = 0
        for edit in proposal.edits:
            if not isinstance(edit, ProposedEdit):
                raise NexusDiffBuilderError("malformed proposed edit")
            path = edit.relative_path
            if not isinstance(path, str) or path not in sources or path in used:
                raise NexusDiffBuilderError("duplicate or out-of-scope patch target")
            if PurePosixPath(path).suffix.casefold() not in _ALLOWED_SUFFIXES:
                raise NexusDiffBuilderError("unsupported patch target extension")
            used.add(path)
            src = sources[path]
            _check_hash(src.source_text_sha256, "source digest")
            _check_hash(edit.base_text_sha256, "base digest")
            _check_hash(edit.proposed_text_sha256, "new digest")
            if (_digest(src._source) != src.source_text_sha256
                    or len(src._source) != src.length_chars
                    or src.source_text_sha256 != edit.base_text_sha256):
                raise NexusDiffBuilderError("captured source and proposed base disagree")
            if not isinstance(edit.rationale, str) or not 1 <= len(edit.rationale.strip()) <= 1000:
                raise NexusDiffBuilderError("invalid edit rationale")
            total_chars += len(edit.unified_diff) if isinstance(edit.unified_diff, str) else self.limits.max_total_diff_chars + 1
            if total_chars > self.limits.max_total_diff_chars:
                raise NexusDiffBuilderError("aggregate patch content exceeds limit")
            proposed_text, hunks = _reconstruct(src._source, edit, self.limits)
            if _digest(proposed_text) != edit.proposed_text_sha256:
                raise NexusDiffBuilderError("reconstructed file hash does not match proposal")
            canonical_diff = "".join(unified_diff(
                src._source.splitlines(keepends=True),
                proposed_text.splitlines(keepends=True),
                fromfile="a/" + path, tofile="b/" + path, n=3,
            ))
            if canonical_diff != edit.unified_diff:
                raise NexusDiffBuilderError("noncanonical diff; refuse ambiguous patch")
            if PurePosixPath(path).suffix.casefold() in (".py", ".pyi"):
                try:
                    ast.parse(proposed_text, filename=path)
                except (SyntaxError, ValueError) as exc:
                    raise NexusDiffBuilderError("patched Python file has invalid syntax") from exc
            files.append(PatchFile(
                relative_path=path,
                base_text_sha256=edit.base_text_sha256,
                proposed_text_sha256=edit.proposed_text_sha256,
                additions=sum(h.additions for h in hunks),
                deletions=sum(h.deletions for h in hunks),
                hunks=hunks, rationale=edit.rationale,
                unified_diff=edit.unified_diff,
            ))
        # Verify the CodeProposal ID using the same canonical identity contract
        # as code_proposer.py, not merely the provided proposal_id string.
        expected_proposal_id = _digest(_canonical({
            "mission_id": context.mission_id,
            "context_sha256": context.context_sha256,
            "summary": proposal.summary,
            "edits": [
                (edit.relative_path, edit.base_text_sha256,
                 edit.proposed_text_sha256, edit.unified_diff)
                for edit in proposal.edits
            ],
        }))
        if expected_proposal_id != proposal.proposal_id:
            raise NexusDiffBuilderError("proposal ID does not match its contents")
        patch_identity = {
            "schema": PATCH_SCHEMA,
            "proposal_id": proposal.proposal_id,
            "mission_id": context.mission_id,
            "context_sha256": context.context_sha256,
            "files": [
                {
                    "relative_path": f.relative_path,
                    "base_text_sha256": f.base_text_sha256,
                    "proposed_text_sha256": f.proposed_text_sha256,
                    "diff_sha256": _digest(f.unified_diff),
                } for f in files
            ],
        }
        return PatchBundle(
            patchset_sha256=_digest(_canonical(patch_identity)),
            proposal_id=proposal.proposal_id,
            mission_id=context.mission_id,
            context_sha256=context.context_sha256,
            files=tuple(files),
            additions=sum(f.additions for f in files),
            deletions=sum(f.deletions for f in files),
        )

    def verify(
        self,
        bundle: PatchBundle,
        context: ProposalContext,
        proposal: CodeProposal,
        *,
        require_fresh: bool = True,
    ) -> bool:
        """Check that a bundle exactly equals fresh, reconstructed proposal data.

        Raises NexusDiffBuilderError when invalid; returns True when consistent.
        This is not a signature, user approval, or permission to apply changes.
        """
        if not isinstance(bundle, PatchBundle):
            raise NexusDiffBuilderError("expected a PatchBundle")
        rebuilt = self.build(context, proposal, require_fresh=require_fresh)
        if rebuilt != bundle:
            raise NexusDiffBuilderError("patch bundle has changed since construction")
        return True


    def render_reviewed_text(
        self,
        bundle: PatchBundle,
        context: ProposalContext,
        proposal: CodeProposal,
        relative_path: str,
        *,
        require_fresh: bool = True,
    ) -> str:
        """Reconstruct one reviewed file in memory; no writes or authorization.

        Always verify the entire patch bundle first. This is intended for a
        trusted host that separately authorizes and confines candidate writes.
        Never interpret this return value as permission to modify a checkout.
        """
        self.verify(bundle, context, proposal, require_fresh=require_fresh)
        sources = {item.relative_path: item for item in context.sources}
        edits = {item.relative_path: item for item in proposal.edits}
        expected = {item.relative_path: item for item in bundle.files}
        if (type(relative_path) is not str or relative_path not in sources
                or relative_path not in edits or relative_path not in expected):
            raise NexusDiffBuilderError("file is not in the reviewed patch bundle")
        updated, _ = _reconstruct(
            sources[relative_path]._source, edits[relative_path], self.limits
        )
        if _digest(updated) != expected[relative_path].proposed_text_sha256:
            raise NexusDiffBuilderError("reviewed text digest changed")
        return updated


__all__ = [
    "PATCH_SCHEMA", "NexusDiffBuilderError", "DiffBuilderLimits", "PatchHunk",
    "PatchFile", "PatchBundle", "NexusDiffBuilder",
]
