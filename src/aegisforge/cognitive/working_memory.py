from __future__ import annotations

"""Working-memory manager for AegisForge NCP.

This module owns the active-memory layer between ``attention.py`` and the future
``controller.py``.  It is deliberately small in dependencies and strong in
contracts: it stores only compact, redacted, ranked working-memory items and can
export those items back into ``CognitiveState`` or prompt/controller context.

The implementation is deterministic.  It does not learn task answers and it does
not create benchmark-specific lookup tables.  It keeps strategy-level signals:
objectives, constraints, policy boundaries, evidence gaps, tool affordances,
risks, and recent decisions.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from hashlib import sha256
import json
import math
import re
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .state import CognitiveState, WorkingMemoryItem


class SupportsToDict(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


NCP_WORKING_MEMORY_VERSION = "0.1.0"

DEFAULT_CAPACITY = 36
DEFAULT_PROMPT_BUDGET_CHARS = 5_500
DEFAULT_ITEM_CHAR_LIMIT = 620
DEFAULT_MIN_SALIENCE = 0.05
DEFAULT_RECENCY_HALF_LIFE_TURNS = 6.0

CRITICAL_TAGS = {
    "policy_boundary",
    "hardcoding_guard",
    "secret_boundary",
    "needs_evidence",
    "ambiguous_context",
}

PINNED_SOURCES = {
    "policy",
    "metadata",
    "task",
}

SOURCE_QUOTAS: dict[str, int] = {
    "policy": 8,
    "task": 8,
    "metadata": 7,
    "classification": 5,
    "route": 5,
    "plan": 7,
    "budget": 4,
    "prompt_context": 6,
    "memory": 7,
    "evidence": 8,
    "decision": 5,
    "observation": 8,
}

_SECTION_ORDER = (
    "policy",
    "task",
    "metadata",
    "classification",
    "route",
    "plan",
    "evidence",
    "decision",
    "budget",
    "prompt_context",
    "memory",
    "observation",
)

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|credential|private[_-]?key|session|cookie|auth)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{16,}|ghp_[a-z0-9_]{16,}|xox[baprs]-[a-z0-9-]{10,}|"
    r"bearer\s+[a-z0-9._-]{16,}|api[_-]?key\s*[:=]\s*[a-z0-9._-]{12,})"
)
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{2,}")


@dataclass(frozen=True, slots=True)
class WorkingMemoryConfig:
    """Runtime configuration for the NCP working-memory store."""

    capacity: int = DEFAULT_CAPACITY
    prompt_budget_chars: int = DEFAULT_PROMPT_BUDGET_CHARS
    item_char_limit: int = DEFAULT_ITEM_CHAR_LIMIT
    min_salience: float = DEFAULT_MIN_SALIENCE
    recency_half_life_turns: float = DEFAULT_RECENCY_HALF_LIFE_TURNS
    source_quotas: Mapping[str, int] = field(default_factory=lambda: dict(SOURCE_QUOTAS))
    preserve_locked: bool = True
    redact_sensitive: bool = True
    merge_duplicate_threshold: float = 0.92
    enable_compression: bool = True
    compression_target_ratio: float = 0.58

    def normalized(self) -> "WorkingMemoryConfig":
        return WorkingMemoryConfig(
            capacity=max(4, int(self.capacity)),
            prompt_budget_chars=max(800, int(self.prompt_budget_chars)),
            item_char_limit=max(120, int(self.item_char_limit)),
            min_salience=min(max(float(self.min_salience), 0.0), 1.0),
            recency_half_life_turns=max(1.0, float(self.recency_half_life_turns)),
            source_quotas={str(k): max(1, int(v)) for k, v in dict(self.source_quotas).items()},
            preserve_locked=bool(self.preserve_locked),
            redact_sensitive=bool(self.redact_sensitive),
            merge_duplicate_threshold=min(max(float(self.merge_duplicate_threshold), 0.55), 1.0),
            enable_compression=bool(self.enable_compression),
            compression_target_ratio=min(max(float(self.compression_target_ratio), 0.25), 0.95),
        )


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    """Selection request used by the controller or prompt builder."""

    text: str = ""
    tags: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    max_items: int | None = None
    max_chars: int | None = None
    min_score: float = 0.0
    include_locked: bool = True
    prefer_recent: bool = True
    prefer_policy: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_task(
        cls,
        *,
        task_text: str,
        tags: Iterable[str] = (),
        sources: Iterable[str] = (),
        max_items: int | None = None,
        max_chars: int | None = None,
    ) -> "MemoryQuery":
        return cls(
            text=_sanitize(task_text),
            tags=tuple(_unique(str(tag) for tag in tags)),
            sources=tuple(_unique(str(source) for source in sources)),
            max_items=max_items,
            max_chars=max_chars,
        )


@dataclass(frozen=True, slots=True)
class MemoryScore:
    """Scored memory item plus explanations."""

    item: WorkingMemoryItem
    score: float
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item.to_dict(),
            "score": round(float(self.score), 6),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class MemorySelection:
    """Result of a working-memory query."""

    query_digest: str
    selected: tuple[MemoryScore, ...]
    omitted_count: int
    total_chars: int
    compressed: bool = False
    summary: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def items(self) -> tuple[WorkingMemoryItem, ...]:
        return tuple(score.item for score in self.selected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_digest": self.query_digest,
            "selected": [score.to_dict() for score in self.selected],
            "omitted_count": self.omitted_count,
            "total_chars": self.total_chars,
            "compressed": self.compressed,
            "summary": self.summary,
            "metadata": _json_safe(dict(self.metadata)),
        }

    def compact_context(self, *, include_header: bool = True) -> str:
        lines: list[str] = []
        if include_header:
            lines.append(
                f"NCP working memory: selected={len(self.selected)} "
                f"omitted={self.omitted_count} chars={self.total_chars}"
            )
            if self.summary:
                lines.append(f"Summary: {self.summary}")
        for score in self.selected:
            lines.append(f"- ({score.score:.2f}) {score.item.compact()}")
        return "\n".join(lines).strip()


@dataclass(frozen=True, slots=True)
class MemoryCompressionResult:
    """Report emitted when memory pressure forces compression."""

    before_count: int
    after_count: int
    compressed_count: int
    dropped_count: int
    summary_item: WorkingMemoryItem | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "before_count": self.before_count,
            "after_count": self.after_count,
            "compressed_count": self.compressed_count,
            "dropped_count": self.dropped_count,
            "summary_item": self.summary_item.to_dict() if self.summary_item else None,
            "metadata": _json_safe(dict(self.metadata)),
        }


@dataclass(frozen=True, slots=True)
class WorkingMemoryStats:
    """Inspectable memory-health snapshot."""

    version: str
    count: int
    capacity: int
    locked_count: int
    source_counts: Mapping[str, int]
    tag_counts: Mapping[str, int]
    avg_salience: float
    max_salience: float
    min_salience: float
    total_chars: int
    memory_pressure: float
    turn_index: int

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


class WorkingMemoryStore:
    """Mutable active-memory store for one NCP episode.

    The store is intentionally separated from ``CognitiveState``.  The state is
    an immutable trace contract, while this store is a controller utility that
    supports fast add/query/prune operations before exporting back to state.
    """

    def __init__(
        self,
        *,
        config: WorkingMemoryConfig | None = None,
        items: Iterable[WorkingMemoryItem] = (),
        turn_index: int = 0,
    ) -> None:
        self.config = (config or WorkingMemoryConfig()).normalized()
        self.turn_index = int(turn_index)
        self._items: dict[str, WorkingMemoryItem] = {}
        self._insertion_order: list[str] = []
        self._last_compression: MemoryCompressionResult | None = None
        self.add_many(items)

    @classmethod
    def from_state(
        cls,
        state: CognitiveState,
        *,
        config: WorkingMemoryConfig | None = None,
    ) -> "WorkingMemoryStore":
        return cls(config=config, items=state.working_memory, turn_index=state.turn_index)

    @classmethod
    def from_attention_frame(
        cls,
        attention_frame: Any,
        *,
        config: WorkingMemoryConfig | None = None,
        turn_index: int = 0,
    ) -> "WorkingMemoryStore":
        store = cls(config=config, turn_index=turn_index)
        store.add_attention_frame(attention_frame)
        return store

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self.items())

    def items(self) -> tuple[WorkingMemoryItem, ...]:
        return tuple(self._rank_for_retention(self._items.values()))

    def get(self, key: str) -> WorkingMemoryItem | None:
        return self._items.get(str(key))

    def contains(self, key: str) -> bool:
        return str(key) in self._items

    def add(self, item: WorkingMemoryItem) -> WorkingMemoryItem:
        clean = self._normalize_item(item)
        if clean.salience < self.config.min_salience and not clean.locked:
            return clean

        duplicate_key = self._find_duplicate_key(clean)
        if duplicate_key:
            merged = self._merge_items(self._items[duplicate_key], clean)
            self._items[duplicate_key] = merged
            return merged

        existing = self._items.get(clean.key)
        if existing is None:
            self._items[clean.key] = clean
            self._insertion_order.append(clean.key)
        else:
            self._items[clean.key] = self._merge_items(existing, clean)

        self.enforce_capacity()
        return self._items.get(clean.key, clean)

    def add_many(self, items: Iterable[WorkingMemoryItem]) -> tuple[WorkingMemoryItem, ...]:
        added: list[WorkingMemoryItem] = []
        for item in items:
            added.append(self.add(item))
        return tuple(added)

    def add_attention_frame(self, attention_frame: Any, *, max_items: int | None = None) -> tuple[WorkingMemoryItem, ...]:
        frame = _as_mapping(attention_frame)
        if hasattr(attention_frame, "to_dict") and callable(attention_frame.to_dict):
            frame = _as_mapping(attention_frame.to_dict())
        signals = list(frame.get("signals") or [])
        if max_items is not None:
            signals = signals[: max(1, int(max_items))]
        items = [WorkingMemoryItem.from_signal(signal, turn_index=self.turn_index) for signal in signals]
        return self.add_many(items)

    def add_mapping(
        self,
        mapping: Mapping[str, Any],
        *,
        source: str,
        tags: Iterable[str] = (),
        locked_keys: Iterable[str] = (),
        salience: float = 0.52,
    ) -> tuple[WorkingMemoryItem, ...]:
        locked_key_set = {str(key) for key in locked_keys}
        items: list[WorkingMemoryItem] = []
        for key, value in mapping.items():
            if value in (None, ""):
                continue
            item = self.build_item(
                key=str(key),
                content=f"{key}={_format_value(value)}",
                source=source,
                salience=salience + (0.12 if str(key) in locked_key_set else 0.0),
                tags=tuple(tags),
                locked=str(key) in locked_key_set or source in PINNED_SOURCES,
            )
            items.append(item)
        return self.add_many(items)

    def build_item(
        self,
        *,
        key: str,
        content: str,
        source: str = "memory",
        salience: float = 0.5,
        tags: Iterable[str] = (),
        evidence_refs: Iterable[str] = (),
        ttl_turns: int | None = None,
        locked: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> WorkingMemoryItem:
        source = _normalize_identifier(source)
        safe_content = _clip(_sanitize(content), self.config.item_char_limit)
        item_key = _normalize_identifier(key)
        if not item_key or item_key == "unknown":
            item_key = _stable_key("wm", source, safe_content)[:20]
        return WorkingMemoryItem(
            key=item_key,
            content=safe_content,
            source=source,
            salience=_bounded_float(salience, default=0.5),
            turn_index=self.turn_index,
            tags=tuple(_unique(str(tag) for tag in tags)),
            evidence_refs=tuple(_unique(str(ref) for ref in evidence_refs)),
            ttl_turns=ttl_turns,
            locked=bool(locked),
            metadata=_redact_mapping(metadata or {}),
        )

    def query(self, query: MemoryQuery | None = None) -> MemorySelection:
        query = query or MemoryQuery(max_chars=self.config.prompt_budget_chars)
        max_items = query.max_items if query.max_items is not None else self.config.capacity
        max_chars = query.max_chars if query.max_chars is not None else self.config.prompt_budget_chars
        max_items = max(1, int(max_items))
        max_chars = max(200, int(max_chars))

        scored = [self.score_item(item, query) for item in self._items.values()]
        scored = [score for score in scored if score.score >= query.min_score]
        scored.sort(key=lambda score: (-score.score, -score.item.locked, score.item.source, score.item.key))

        selected: list[MemoryScore] = []
        source_counts: Counter[str] = Counter()
        total_chars = 0

        for score in scored:
            item = score.item
            quota = self.config.source_quotas.get(item.source)
            if quota is not None and source_counts[item.source] >= quota and not item.locked:
                continue
            item_chars = len(item.compact())
            if selected and total_chars + item_chars > max_chars:
                continue
            selected.append(score)
            source_counts[item.source] += 1
            total_chars += item_chars
            if len(selected) >= max_items:
                break

        omitted_count = max(0, len(scored) - len(selected))
        summary = self._summarize_selection(selected, omitted_count=omitted_count)

        return MemorySelection(
            query_digest=_query_digest(query),
            selected=tuple(selected),
            omitted_count=omitted_count,
            total_chars=total_chars,
            compressed=False,
            summary=summary,
            metadata={
                "store_count": len(self._items),
                "max_items": max_items,
                "max_chars": max_chars,
                "turn_index": self.turn_index,
            },
        )

    def score_item(self, item: WorkingMemoryItem, query: MemoryQuery) -> MemoryScore:
        score = float(item.salience)
        reasons: list[str] = [f"salience={item.salience:.2f}"]

        age = max(0, self.turn_index - item.turn_index)
        recency = _recency_score(age, self.config.recency_half_life_turns)
        if query.prefer_recent:
            score += 0.16 * recency
            reasons.append(f"recency={recency:.2f}")

        if item.locked and query.include_locked:
            score += 0.18
            reasons.append("locked")
        if query.prefer_policy and item.source == "policy":
            score += 0.12
            reasons.append("policy_source")

        query_tags = {str(tag) for tag in query.tags}
        item_tags = set(item.tags)
        tag_overlap = len(query_tags & item_tags)
        if tag_overlap:
            score += min(0.18, 0.045 * tag_overlap)
            reasons.append(f"tag_overlap={tag_overlap}")

        if query.sources and item.source in set(query.sources):
            score += 0.12
            reasons.append("source_match")
        elif query.sources and item.source not in set(query.sources) and not item.locked:
            score -= 0.10
            reasons.append("source_mismatch")

        evidence_overlap = len(set(query.evidence_refs) & set(item.evidence_refs))
        if evidence_overlap:
            score += min(0.14, 0.05 * evidence_overlap)
            reasons.append(f"evidence_overlap={evidence_overlap}")

        if query.text:
            similarity = _text_similarity(query.text, item.content)
            if similarity:
                score += 0.24 * similarity
                reasons.append(f"text_similarity={similarity:.2f}")

        if any(tag in CRITICAL_TAGS or str(tag).startswith("risk:") for tag in item.tags):
            score += 0.10
            reasons.append("critical_tag")

        if len(item.content) > self.config.item_char_limit * 0.90:
            score -= 0.03
            reasons.append("long_item_penalty")

        return MemoryScore(item=item, score=_bounded_float(score, default=0.0), reasons=tuple(reasons))

    def select_for_prompt(
        self,
        *,
        task_text: str = "",
        max_chars: int | None = None,
        max_items: int | None = None,
        include_sources: Iterable[str] = (),
        tags: Iterable[str] = (),
    ) -> str:
        selection = self.query(
            MemoryQuery.from_task(
                task_text=task_text,
                tags=tags,
                sources=include_sources,
                max_items=max_items,
                max_chars=max_chars or self.config.prompt_budget_chars,
            )
        )
        return format_memory_prompt(selection)

    def enforce_capacity(self) -> MemoryCompressionResult | None:
        self.prune_expired()
        if len(self._items) <= self.config.capacity:
            return None

        before_count = len(self._items)
        if self.config.enable_compression and len(self._items) > int(self.config.capacity * 1.25):
            result = self.compress()
            if len(self._items) <= self.config.capacity:
                self._last_compression = result
                return result

        ranked = self._rank_for_retention(self._items.values())
        keep = ranked[: self.config.capacity]
        if self.config.preserve_locked:
            locked = [item for item in ranked if item.locked and item not in keep]
            if locked:
                keep = self._merge_keep_lists(keep, locked)

        keep_keys = {item.key for item in keep[: self.config.capacity]}
        dropped_count = len(self._items) - len(keep_keys)
        self._items = {key: item for key, item in self._items.items() if key in keep_keys}
        self._insertion_order = [key for key in self._insertion_order if key in self._items]

        result = MemoryCompressionResult(
            before_count=before_count,
            after_count=len(self._items),
            compressed_count=0,
            dropped_count=max(0, dropped_count),
            metadata={"strategy": "capacity_prune"},
        )
        self._last_compression = result
        return result

    def prune_expired(self, *, current_turn: int | None = None) -> int:
        if current_turn is not None:
            self.turn_index = int(current_turn)
        before = len(self._items)
        self._items = {
            key: item for key, item in self._items.items()
            if not item.is_expired(self.turn_index) and (item.salience >= self.config.min_salience or item.locked)
        }
        self._insertion_order = [key for key in self._insertion_order if key in self._items]
        return before - len(self._items)

    def decay(self, *, turns: int = 1, factor: float = 0.96) -> None:
        turns = max(0, int(turns))
        factor = min(max(float(factor), 0.0), 1.0)
        if turns <= 0 or factor >= 1.0:
            return
        decay_factor = factor ** turns
        updated: dict[str, WorkingMemoryItem] = {}
        for key, item in self._items.items():
            if item.locked:
                updated[key] = item
                continue
            updated[key] = replace(item, salience=_bounded_float(item.salience * decay_factor, default=item.salience))
        self._items = updated
        self.prune_expired()

    def advance_turn(self, *, turns: int = 1) -> None:
        self.turn_index += max(1, int(turns))
        self.decay(turns=turns)
        self.prune_expired()

    def pin(self, key: str, *, salience_boost: float = 0.10) -> bool:
        item = self._items.get(str(key))
        if item is None:
            return False
        self._items[item.key] = replace(
            item,
            locked=True,
            salience=_bounded_float(item.salience + salience_boost, default=item.salience),
        )
        return True

    def unpin(self, key: str) -> bool:
        item = self._items.get(str(key))
        if item is None:
            return False
        self._items[item.key] = replace(item, locked=False)
        return True

    def remove(self, key: str) -> bool:
        key = str(key)
        if key not in self._items:
            return False
        del self._items[key]
        self._insertion_order = [item_key for item_key in self._insertion_order if item_key != key]
        return True

    def clear(self, *, preserve_locked: bool = True) -> int:
        before = len(self._items)
        if preserve_locked:
            self._items = {key: item for key, item in self._items.items() if item.locked}
        else:
            self._items.clear()
        self._insertion_order = [key for key in self._insertion_order if key in self._items]
        return before - len(self._items)

    def compress(self, *, target_count: int | None = None) -> MemoryCompressionResult:
        before_count = len(self._items)
        if before_count <= 2:
            return MemoryCompressionResult(before_count, before_count, 0, 0, metadata={"strategy": "noop"})

        target_count = target_count or max(4, int(self.config.capacity * self.config.compression_target_ratio))
        target_count = min(max(2, int(target_count)), self.config.capacity)

        ranked = self._rank_for_retention(self._items.values())
        keep: list[WorkingMemoryItem] = []
        compressible: list[WorkingMemoryItem] = []

        for item in ranked:
            if item.locked or item.source in {"policy", "metadata"}:
                keep.append(item)
            elif len(keep) < target_count:
                keep.append(item)
            else:
                compressible.append(item)

        if not compressible:
            keep = ranked[:target_count]
            compressible = ranked[target_count:]

        summary_item = None
        if compressible:
            summary_item = self._build_summary_item(compressible)
            keep.append(summary_item)

        keep = self._rank_for_retention(keep)[: self.config.capacity]
        keep_keys = {item.key for item in keep}
        self._items = {item.key: item for item in keep}
        self._insertion_order = [key for key in self._insertion_order if key in keep_keys]
        for item in keep:
            if item.key not in self._insertion_order:
                self._insertion_order.append(item.key)

        result = MemoryCompressionResult(
            before_count=before_count,
            after_count=len(self._items),
            compressed_count=len(compressible),
            dropped_count=max(0, before_count - len(self._items) - len(compressible)),
            summary_item=summary_item,
            metadata={"strategy": "semantic_summary", "target_count": target_count},
        )
        self._last_compression = result
        return result

    def update_state(self, state: CognitiveState, *, append_trace: bool = True) -> CognitiveState:
        next_state = state.remember_many(self.items()).reestimate_uncertainty().refresh_scorecard()
        if append_trace:
            stats = self.stats()
            next_state = next_state.append_trace(
                phase="working_memory",
                message=(
                    f"Working memory synchronized: count={stats.count}, "
                    f"pressure={stats.memory_pressure:.2f}, chars={stats.total_chars}."
                ),
                metadata={
                    "source_counts": dict(stats.source_counts),
                    "tag_counts": dict(list(stats.tag_counts.items())[:12]),
                    "last_compression": self._last_compression.to_dict() if self._last_compression else None,
                },
            ).refresh_scorecard()
        return next_state

    def stats(self) -> WorkingMemoryStats:
        items = list(self._items.values())
        source_counts = Counter(item.source for item in items)
        tag_counts = Counter(tag for item in items for tag in item.tags)
        saliences = [float(item.salience) for item in items]
        total_chars = sum(len(item.content) for item in items)
        return WorkingMemoryStats(
            version=NCP_WORKING_MEMORY_VERSION,
            count=len(items),
            capacity=self.config.capacity,
            locked_count=sum(1 for item in items if item.locked),
            source_counts=dict(sorted(source_counts.items())),
            tag_counts=dict(sorted(tag_counts.items())),
            avg_salience=round(sum(saliences) / len(saliences), 6) if saliences else 0.0,
            max_salience=round(max(saliences), 6) if saliences else 0.0,
            min_salience=round(min(saliences), 6) if saliences else 0.0,
            total_chars=total_chars,
            memory_pressure=round(len(items) / self.config.capacity, 6),
            turn_index=self.turn_index,
        )

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "version": NCP_WORKING_MEMORY_VERSION,
            "config": _json_safe(asdict(self.config)),
            "turn_index": self.turn_index,
            "items": [item.to_dict() for item in self.items()],
            "stats": self.stats().to_dict(),
            "last_compression": self._last_compression.to_dict() if self._last_compression else None,
        }

    def compact_context(self, *, max_items: int = 16, max_chars: int | None = None) -> str:
        query = MemoryQuery(max_items=max_items, max_chars=max_chars or self.config.prompt_budget_chars)
        return self.query(query).compact_context()

    def _normalize_item(self, item: WorkingMemoryItem) -> WorkingMemoryItem:
        content = _clip(_sanitize(item.content), self.config.item_char_limit)
        source = _normalize_identifier(item.source)
        tags = tuple(_unique(_normalize_tag(tag) for tag in item.tags))
        evidence_refs = tuple(_unique(str(ref) for ref in item.evidence_refs))
        locked = bool(item.locked or source in PINNED_SOURCES or any(tag in CRITICAL_TAGS for tag in tags))
        metadata = _redact_mapping(dict(item.metadata))
        key = str(item.key or _stable_key("wm", source, content)[:20])
        return replace(
            item,
            key=key,
            content=content,
            source=source,
            salience=_bounded_float(item.salience, default=0.5),
            turn_index=int(item.turn_index),
            tags=tags,
            evidence_refs=evidence_refs,
            ttl_turns=item.ttl_turns,
            locked=locked,
            metadata=metadata,
        )

    def _find_duplicate_key(self, item: WorkingMemoryItem) -> str | None:
        item_norm = _content_key(item.content)
        for key, existing in self._items.items():
            if key == item.key:
                return key
            if existing.source != item.source and not (existing.locked and item.locked):
                continue
            if _content_key(existing.content) == item_norm:
                return key
            similarity = _text_similarity(existing.content, item.content)
            if similarity >= self.config.merge_duplicate_threshold:
                return key
        return None

    @staticmethod
    def _merge_items(old: WorkingMemoryItem, new: WorkingMemoryItem) -> WorkingMemoryItem:
        chosen_content = new.content if len(new.content) >= len(old.content) else old.content
        return replace(
            old,
            content=chosen_content,
            salience=max(old.salience, new.salience),
            turn_index=max(old.turn_index, new.turn_index),
            tags=tuple(_unique((*old.tags, *new.tags))),
            evidence_refs=tuple(_unique((*old.evidence_refs, *new.evidence_refs))),
            ttl_turns=old.ttl_turns if old.locked else new.ttl_turns,
            locked=old.locked or new.locked,
            metadata=_redact_mapping({**dict(old.metadata), **dict(new.metadata)}),
        )

    def _rank_for_retention(self, items: Iterable[WorkingMemoryItem]) -> list[WorkingMemoryItem]:
        def retention_score(item: WorkingMemoryItem) -> tuple[float, int, float, str, str]:
            age = max(0, self.turn_index - item.turn_index)
            recency = _recency_score(age, self.config.recency_half_life_turns)
            critical = any(tag in CRITICAL_TAGS or str(tag).startswith("risk:") for tag in item.tags)
            score = item.salience + 0.14 * recency + (0.22 if item.locked else 0.0) + (0.12 if critical else 0.0)
            return (score, int(item.locked), recency, item.source, item.key)

        return sorted(items, key=retention_score, reverse=True)

    def _merge_keep_lists(self, primary: Sequence[WorkingMemoryItem], locked: Sequence[WorkingMemoryItem]) -> list[WorkingMemoryItem]:
        by_key = {item.key: item for item in primary}
        for item in locked:
            by_key[item.key] = item
        ranked = self._rank_for_retention(by_key.values())
        return ranked[: self.config.capacity]

    def _build_summary_item(self, items: Sequence[WorkingMemoryItem]) -> WorkingMemoryItem:
        by_source: dict[str, list[WorkingMemoryItem]] = defaultdict(list)
        for item in items:
            by_source[item.source].append(item)

        fragments: list[str] = []
        for source in _SECTION_ORDER:
            source_items = by_source.get(source, [])
            if not source_items:
                continue
            top = sorted(source_items, key=lambda item: item.salience, reverse=True)[:3]
            joined = "; ".join(_clip(item.content, 140) for item in top)
            fragments.append(f"{source}: {joined}")
        for source, source_items in sorted(by_source.items()):
            if source in _SECTION_ORDER:
                continue
            top = sorted(source_items, key=lambda item: item.salience, reverse=True)[:2]
            joined = "; ".join(_clip(item.content, 120) for item in top)
            fragments.append(f"{source}: {joined}")

        content = _clip(" | ".join(fragments), self.config.item_char_limit)
        all_tags = tuple(_unique(tag for item in items for tag in item.tags))
        all_refs = tuple(_unique(ref for item in items for ref in item.evidence_refs))
        return self.build_item(
            key=_stable_key("wm_summary", self.turn_index, content)[:20],
            content=content or "Compressed low-priority working-memory details.",
            source="memory",
            salience=max(0.42, max((item.salience for item in items), default=0.42) - 0.08),
            tags=("compressed_summary", *all_tags[:8]),
            evidence_refs=all_refs[:12],
            ttl_turns=8,
            locked=False,
            metadata={"compressed_count": len(items), "compression": "semantic_summary"},
        )

    @staticmethod
    def _summarize_selection(selected: Sequence[MemoryScore], *, omitted_count: int) -> str:
        if not selected:
            return "No working-memory items selected."
        source_counts = Counter(score.item.source for score in selected)
        top_sources = ", ".join(f"{source}={count}" for source, count in source_counts.most_common(5))
        risk_count = sum(
            1
            for score in selected
            for tag in score.item.tags
            if tag.startswith("risk:") or tag in CRITICAL_TAGS
        )
        parts = [f"sources({top_sources})"]
        if risk_count:
            parts.append(f"critical_tags={risk_count}")
        if omitted_count:
            parts.append(f"omitted={omitted_count}")
        return "; ".join(parts)


def build_working_memory(
    *,
    state: CognitiveState | None = None,
    attention_frame: Any | None = None,
    config: WorkingMemoryConfig | None = None,
    turn_index: int | None = None,
) -> WorkingMemoryStore:
    """Build a store from an optional state and attention frame."""

    if state is not None:
        store = WorkingMemoryStore.from_state(state, config=config)
    else:
        store = WorkingMemoryStore(config=config, turn_index=turn_index or 0)
    if turn_index is not None:
        store.turn_index = int(turn_index)
    if attention_frame is not None:
        store.add_attention_frame(attention_frame)
    return store


def update_state_with_working_memory(
    state: CognitiveState,
    *,
    attention_frame: Any | None = None,
    config: WorkingMemoryConfig | None = None,
) -> CognitiveState:
    """Convenience function for controller integration."""

    store = build_working_memory(state=state, attention_frame=attention_frame, config=config)
    return store.update_state(state)


def format_memory_prompt(selection: MemorySelection, *, title: str = "NCP Working Memory") -> str:
    """Render selected memory items as a compact controller/prompt block."""

    if not selection.selected:
        return f"{title}:\n- No active working-memory items selected."

    grouped: dict[str, list[MemoryScore]] = defaultdict(list)
    for score in selection.selected:
        grouped[score.item.source].append(score)

    lines = [
        f"{title}:",
        f"- summary: {selection.summary or 'selected active memory signals'}",
    ]
    for source in _SECTION_ORDER:
        scores = grouped.pop(source, [])
        if not scores:
            continue
        lines.append(f"- {source}:")
        for score in scores:
            lines.append(f"  - [{score.score:.2f}] {_clip(score.item.content, 260)}")
    for source in sorted(grouped):
        lines.append(f"- {source}:")
        for score in grouped[source]:
            lines.append(f"  - [{score.score:.2f}] {_clip(score.item.content, 260)}")
    if selection.omitted_count:
        lines.append(f"- omitted_low_priority_items: {selection.omitted_count}")
    return "\n".join(lines).strip()


def memory_items_from_attention(attention_frame: Any, *, turn_index: int = 0, max_items: int | None = None) -> tuple[WorkingMemoryItem, ...]:
    """Convert an attention frame into state-compatible working-memory items."""

    frame = _as_mapping(attention_frame)
    if hasattr(attention_frame, "to_dict") and callable(attention_frame.to_dict):
        frame = _as_mapping(attention_frame.to_dict())
    signals = list(frame.get("signals") or [])
    if max_items is not None:
        signals = signals[: max(1, int(max_items))]
    return tuple(WorkingMemoryItem.from_signal(signal, turn_index=turn_index) for signal in signals)


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            result = value.to_dict()
            return dict(result) if isinstance(result, Mapping) else {}
        except Exception:
            return {}
    if hasattr(value, "as_dict") and callable(value.as_dict):
        try:
            result = value.as_dict()
            return dict(result) if isinstance(result, Mapping) else {}
        except Exception:
            return {}
    if is_dataclass(value):
        try:
            return dict(asdict(value))
        except Exception:
            return {}
    if hasattr(value, "__dict__"):
        return {key: val for key, val in vars(value).items() if not key.startswith("_")}
    return {}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_safe(item) for item in value)
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            return repr(value)
    if hasattr(value, "as_dict") and callable(value.as_dict):
        try:
            return _json_safe(value.as_dict())
        except Exception:
            return repr(value)
    return repr(value)


def _redact_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        key_text = str(key)
        if _SECRET_KEY_RE.search(key_text):
            out[key_text] = "<redacted>"
        elif isinstance(value, Mapping):
            out[key_text] = _redact_mapping(value)
        elif isinstance(value, list):
            out[key_text] = [_redact_value(item) for item in value]
        elif isinstance(value, tuple):
            out[key_text] = tuple(_redact_value(item) for item in value)
        else:
            out[key_text] = _redact_value(value)
    return out


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize(value)
    if isinstance(value, Mapping):
        return _redact_mapping(value)
    return _json_safe(value)


def _sanitize(text: Any) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    compact = _SECRET_VALUE_RE.sub("<redacted>", compact)
    return compact


def _format_value(value: Any) -> str:
    safe = _json_safe(value)
    try:
        return _sanitize(json.dumps(safe, ensure_ascii=False, sort_keys=True))
    except TypeError:
        return _sanitize(repr(safe))


def _clip(text: Any, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _bounded_float(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return min(1.0, max(0.0, number))


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return tuple(out)


def _normalize_identifier(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("τ²", "tau2").replace("t²", "tau2")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "unknown"


def _normalize_tag(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("risk:") or text.startswith("uncertainty:"):
        prefix, rest = text.split(":", 1)
        return f"{prefix}:{_normalize_identifier(rest)}"
    return _normalize_identifier(text)


def _stable_key(prefix: str, *parts: Any) -> str:
    blob = json.dumps([_json_safe(part) for part in parts], ensure_ascii=False, sort_keys=True)
    return f"{prefix}_{sha256(blob.encode('utf-8')).hexdigest()}"


def _query_digest(query: MemoryQuery) -> str:
    blob = json.dumps(_json_safe(asdict(query)), ensure_ascii=False, sort_keys=True)
    return sha256(blob.encode("utf-8")).hexdigest()[:16]


def _content_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())[:220]


def _tokens(text: str) -> set[str]:
    return {tok.lower() for tok in _TOKEN_RE.findall(str(text or "")) if len(tok) > 1}


def _text_similarity(a: str, b: str) -> float:
    left = _tokens(a)
    right = _tokens(b)
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    return intersection / union if union else 0.0


def _recency_score(age_turns: int, half_life: float) -> float:
    age = max(0.0, float(age_turns))
    half_life = max(1.0, float(half_life))
    return math.exp(-math.log(2) * age / half_life)


__all__ = [
    "NCP_WORKING_MEMORY_VERSION",
    "WorkingMemoryConfig",
    "WorkingMemoryStore",
    "WorkingMemoryStats",
    "MemoryCompressionResult",
    "MemoryQuery",
    "MemoryScore",
    "MemorySelection",
    "build_working_memory",
    "format_memory_prompt",
    "memory_items_from_attention",
    "update_state_with_working_memory",
]
