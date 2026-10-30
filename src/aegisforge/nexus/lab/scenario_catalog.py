"""Deterministic, inert scenario catalog for AegisForge Nexus — Colossus.

Repository destination: src/aegisforge/nexus/lab/scenario_catalog.py

Defines bounded, data-only evaluation scenarios for Nexus, AegisGrid and
Live Forge. Does not call an LLM, run a test, import workspace content,
execute commands, connect to Twitch/Nebius, or access the filesystem.

A scenario is a specification, NOT an authorization or a test result.
An external Evaluation Runner must independently enforce permissions,
execution isolation, and evidence checks before acting on scenario data.
Hashes provide internal consistency, NOT provenance or authenticity.
Never store real tokens, passwords, personally identifying data, or private
prompt contents in catalogs; snapshots include all scenario inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import hmac
import json
import math
import re
from threading import RLock
from typing import Any, Mapping, Sequence

SCENARIO_SCHEMA = "aegisforge.nexus.lab.scenario.v1"
CATALOG_SCHEMA = "aegisforge.nexus.lab.catalog.v1"
PRODUCTS = frozenset({"nexus", "aegisgrid", "live_forge", "colossus"})
CATEGORIES = frozenset({"functional", "safety", "reliability", "integration", "performance", "regression"})
MODES = frozenset({"offline", "simulated", "external"})
OPERATORS = frozenset({"equals", "not_equals", "exists", "gte", "lte", "contains"})
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_FIELD = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}(\.[a-zA-Z_][a-zA-Z0-9_]{0,63}){0,7}$")
_TAG = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_HEX = re.compile(r"^[a-f0-9]{64}$")
# Defense in depth for accidental secret-shaped fields, NOT a complete DLP system.
_SENSITIVE = re.compile(
    r"(?:^|_)(?:password|passwd|secret|api_key|apikey|access_token|refresh_token|"
    r"private_key|authorization|bearer_token|client_secret)(?:$|_)", re.I
)
_CASE_KEYS = frozenset({"schema", "scenario_id", "revision", "product", "category", "mode",
                        "title", "description", "tags", "inputs", "expectations", "max_seconds",
                        "case_sha256"})
_EXPECT_KEYS = frozenset({"path", "operator", "expected"})
_SNAPSHOT_KEYS = frozenset({"schema", "count", "cases", "catalog_sha256"})
_MISSING = object()


class NexusScenarioCatalogError(ValueError):
    """Invalid or untrusted scenario, catalog, or snapshot."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusScenarioCatalogError("expected bounded JSON-compatible data") from exc


def _digest(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _strict_string(value: Any, *, label: str, limit: int, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or value != value.strip():
        raise NexusScenarioCatalogError(f"invalid {label}")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise NexusScenarioCatalogError(f"invalid control character in {label}")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise NexusScenarioCatalogError(f"invalid {label}")
    return value


def _valid_json(value: Any, *, size_limit: int) -> bytes:
    """Strictly validate JSON, depth and secret-like keys; return immutable copy."""
    count = 0
    def visit(obj: Any, depth: int) -> None:
        nonlocal count
        count += 1
        if count > 2048 or depth > 12:
            raise NexusScenarioCatalogError("scenario JSON is too complex")
        if obj is None or type(obj) in (bool, int):
            if type(obj) is int and abs(obj) > 10**16:
                raise NexusScenarioCatalogError("integer exceeds range")
            return
        if type(obj) is float:
            if not math.isfinite(obj):
                raise NexusScenarioCatalogError("nonfinite JSON number")
            return
        if type(obj) is str:
            if len(obj) > 8192 or any(ord(c) < 32 and c not in "\t\n\r" for c in obj):
                raise NexusScenarioCatalogError("invalid JSON string")
            return
        if type(obj) is list:
            if len(obj) > 512:
                raise NexusScenarioCatalogError("JSON list too long")
            for item in obj:
                visit(item, depth+1)
            return
        if type(obj) is dict:
            if len(obj) > 256:
                raise NexusScenarioCatalogError("JSON object too large")
            for key, item in obj.items():
                if not isinstance(key, str) or not 1 <= len(key) <= 100 or _SENSITIVE.search(key):
                    raise NexusScenarioCatalogError("JSON includes invalid or sensitive field name")
                visit(item, depth+1)
            return
        raise NexusScenarioCatalogError("scenario data must use strict JSON types")

    visit(value, 0)
    encoded = _canonical(value)
    if len(encoded) > size_limit:
        raise NexusScenarioCatalogError("scenario JSON exceeds byte limit")
    return encoded


@dataclass(frozen=True, slots=True, init=False)
class ScenarioExpectation:
    """Declarative assertion consumed by a separately authorized evaluator.

    `expected` is omitted for `exists`; for other operators it is required.
    The expression is a *field path*, never Python, JavaScript, SQL or JMESPath.
    """

    path: str
    operator: str
    _expected_json: bytes | None = field(repr=False)

    def __init__(self, path: str, operator: str, expected: Any = _MISSING) -> None:
        object.__setattr__(self, "path", _strict_string(path, label="expectation path", limit=256, pattern=_FIELD))
        if not isinstance(operator, str) or operator not in OPERATORS:
            raise NexusScenarioCatalogError("unsupported expectation operator")
        object.__setattr__(self, "operator", operator)
        if operator == "exists":
            if expected is not _MISSING:
                raise NexusScenarioCatalogError("exists expectation must omit expected")
            raw = None
        else:
            if expected is _MISSING:
                raise NexusScenarioCatalogError("expected value required")
            raw = _valid_json(expected, size_limit=4096)
            if operator in {"gte", "lte"}:
                if type(expected) not in (int, float):
                    raise NexusScenarioCatalogError("numeric expectation required")
            if operator == "contains" and type(expected) not in (str, int, float, bool):
                raise NexusScenarioCatalogError("contains expects a scalar")
        object.__setattr__(self, "_expected_json", raw)

    @property
    def expected(self) -> Any:
        return None if self._expected_json is None else json.loads(self._expected_json)

    def to_dict(self) -> dict[str, Any]:
        result = {"path": self.path, "operator": self.operator}
        if self._expected_json is not None:
            result["expected"] = self.expected
        return result

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "ScenarioExpectation":
        if not isinstance(row, Mapping) or set(row) - _EXPECT_KEYS:
            raise NexusScenarioCatalogError("invalid expectation fields")
        if "path" not in row or "operator" not in row:
            raise NexusScenarioCatalogError("missing expectation fields")
        if "expected" in row:
            return cls(row["path"], row["operator"], row["expected"])
        return cls(row["path"], row["operator"])


@dataclass(frozen=True, slots=True, init=False)
class ScenarioCase:
    """Immutable JSON-backed scenario specification and stable content digest."""

    scenario_id: str
    revision: int
    product: str
    category: str
    mode: str
    title: str
    description: str
    tags: tuple[str, ...]
    max_seconds: int
    expectations: tuple[ScenarioExpectation, ...]
    _inputs_json: bytes = field(repr=False)
    case_sha256: str

    def __init__(
        self, scenario_id: str, *, product: str, category: str, title: str,
        description: str, inputs: Mapping[str, Any],
        expectations: Sequence[ScenarioExpectation], mode: str = "offline",
        revision: int = 1, tags: Sequence[str] = (), max_seconds: int = 30,
    ) -> None:
        sid = _strict_string(scenario_id, label="scenario ID", limit=96, pattern=_ID)
        if (not isinstance(product, str) or product not in PRODUCTS
                or not isinstance(category, str) or category not in CATEGORIES
                or not isinstance(mode, str) or mode not in MODES):
            raise NexusScenarioCatalogError("invalid scenario product, category, or mode")
        if type(revision) is not int or not 1 <= revision <= 1_000_000:
            raise NexusScenarioCatalogError("invalid scenario revision")
        if type(max_seconds) is not int or not 1 <= max_seconds <= 3600:
            raise NexusScenarioCatalogError("invalid time budget")
        name = _strict_string(title, label="title", limit=120)
        detail = _strict_string(description, label="description", limit=1500)
        if type(inputs) is not dict:
            raise NexusScenarioCatalogError("inputs must be a plain JSON object")
        frozen_input = _valid_json(inputs, size_limit=12_000)
        if not isinstance(expectations, (tuple, list)) or not 1 <= len(expectations) <= 32 or not all(isinstance(x, ScenarioExpectation) for x in expectations):
            raise NexusScenarioCatalogError("at least one valid expectation is required")
        if not isinstance(tags, (list, tuple)) or len(tags) > 16:
            raise NexusScenarioCatalogError("invalid tags")
        if any(not isinstance(x, str) or _TAG.fullmatch(x) is None for x in tags):
            raise NexusScenarioCatalogError("invalid tags")
        normalized_tags = tuple(sorted(set(tags)))
        if len(normalized_tags) != len(tags):
            raise NexusScenarioCatalogError("duplicate tags")
        assertions = tuple(expectations)
        keys = [(x.path, x.operator) for x in assertions]
        if len(set(keys)) != len(keys):
            raise NexusScenarioCatalogError("duplicate expectation")
        for key, val in (
            ("scenario_id", sid), ("revision", revision), ("product", product),
            ("category", category), ("mode", mode), ("title", name),
            ("description", detail), ("tags", normalized_tags),
            ("max_seconds", max_seconds), ("expectations", assertions),
            ("_inputs_json", frozen_input),
        ):
            object.__setattr__(self, key, val)
        payload = self._payload()
        if len(_canonical(payload)) > 24_000:
            raise NexusScenarioCatalogError("scenario exceeds byte limit")
        object.__setattr__(self, "case_sha256", _digest(payload))

    @property
    def inputs(self) -> dict[str, Any]:
        """Defensive copy; mutations cannot alter the registered specification."""
        return json.loads(self._inputs_json)

    @property
    def requires_approval(self) -> bool:
        """Metadata only; an external actor still must check actual authorization."""
        return self.mode == "external"

    def _payload(self) -> dict[str, Any]:
        return {
            "schema": SCENARIO_SCHEMA, "scenario_id": self.scenario_id,
            "revision": self.revision, "product": self.product, "category": self.category,
            "mode": self.mode, "title": self.title, "description": self.description,
            "tags": list(self.tags), "max_seconds": self.max_seconds,
            "inputs": self.inputs, "expectations": [x.to_dict() for x in self.expectations],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "case_sha256": self.case_sha256}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScenarioCase":
        if not isinstance(data, Mapping) or set(data) != _CASE_KEYS:
            raise NexusScenarioCatalogError("unexpected scenario fields")
        digest = data["case_sha256"]
        if not isinstance(digest, str) or _HEX.fullmatch(digest) is None:
            raise NexusScenarioCatalogError("invalid scenario digest")
        if data["schema"] != SCENARIO_SCHEMA:
            raise NexusScenarioCatalogError("unsupported scenario schema")
        if not isinstance(data["expectations"], list):
            raise NexusScenarioCatalogError("invalid expectations")
        case = cls(
            data["scenario_id"], revision=data["revision"], product=data["product"],
            category=data["category"], mode=data["mode"], title=data["title"],
            description=data["description"], tags=data["tags"], max_seconds=data["max_seconds"],
            inputs=data["inputs"],
            expectations=tuple(ScenarioExpectation.from_dict(x) for x in data["expectations"]),
        )
        if not hmac.compare_digest(case.case_sha256, digest):
            raise NexusScenarioCatalogError("scenario has been altered")
        return case


@dataclass(frozen=True, slots=True)
class ScenarioCatalogLimits:
    max_scenarios: int = 256
    max_snapshot_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        if type(self.max_scenarios) is not int or not 1 <= self.max_scenarios <= 2048:
            raise NexusScenarioCatalogError("invalid catalog scenario limit")
        if type(self.max_snapshot_bytes) is not int or not 1_000 <= self.max_snapshot_bytes <= 8_000_000:
            raise NexusScenarioCatalogError("invalid catalog byte limit")


class NexusScenarioCatalog:
    """In-memory, deterministic, mission-neutral library of scenario specs.

    All mutation methods change ONLY this catalog's in-memory registry.
    There is no code execution, subprocess, network request or file writing.
    """

    def __init__(self, *, limits: ScenarioCatalogLimits | None = None) -> None:
        self.limits = limits or ScenarioCatalogLimits()
        if not isinstance(self.limits, ScenarioCatalogLimits):
            raise NexusScenarioCatalogError("invalid catalog limits")
        self._cases: dict[str, ScenarioCase] = {}
        self._lock = RLock()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._cases)

    def get(self, scenario_id: str) -> ScenarioCase:
        with self._lock:
            try:
                return self._cases[scenario_id]
            except (KeyError, TypeError) as exc:
                raise NexusScenarioCatalogError("unknown scenario ID") from exc

    def register(self, case: ScenarioCase) -> str:
        self.register_many((case,))
        return case.case_sha256

    def register_many(self, cases: Sequence[ScenarioCase]) -> None:
        """Atomic batch registration; no partial writes on invalid or duplicate IDs."""
        if not isinstance(cases, (list, tuple)) or not 1 <= len(cases) <= self.limits.max_scenarios:
            raise NexusScenarioCatalogError("invalid scenario batch")
        if not all(isinstance(x, ScenarioCase) for x in cases):
            raise NexusScenarioCatalogError("invalid scenario in batch")
        ids = [x.scenario_id for x in cases]
        if len(set(ids)) != len(ids):
            raise NexusScenarioCatalogError("duplicate scenario IDs in batch")
        with self._lock:
            if len(self._cases) + len(cases) > self.limits.max_scenarios or any(x in self._cases for x in ids):
                raise NexusScenarioCatalogError("catalog is full or scenario ID already exists")
            candidate = dict(self._cases)
            candidate.update((case.scenario_id, case) for case in cases)
            self._bounded_snapshot(candidate)
            self._cases = candidate

    def replace(self, case: ScenarioCase, *, expected_sha256: str) -> None:
        """Compare-and-swap a scenario version, rejecting stale updates."""
        if not isinstance(case, ScenarioCase) or not isinstance(expected_sha256, str):
            raise NexusScenarioCatalogError("invalid scenario replacement")
        with self._lock:
            current = self._cases.get(case.scenario_id)
            if current is None or not hmac.compare_digest(current.case_sha256, expected_sha256):
                raise NexusScenarioCatalogError("scenario replacement is stale")
            if case.revision != current.revision + 1:
                raise NexusScenarioCatalogError("replacement must increment revision")
            candidate = dict(self._cases)
            candidate[case.scenario_id] = case
            self._bounded_snapshot(candidate)
            self._cases = candidate

    def select(self, *, product: str | None = None, category: str | None = None,
               mode: str | None = None, tag: str | None = None) -> tuple[ScenarioCase, ...]:
        if product is not None and (not isinstance(product, str) or product not in PRODUCTS):
            raise NexusScenarioCatalogError("invalid product filter")
        if category is not None and (not isinstance(category, str) or category not in CATEGORIES):
            raise NexusScenarioCatalogError("invalid category filter")
        if mode is not None and (not isinstance(mode, str) or mode not in MODES):
            raise NexusScenarioCatalogError("invalid mode filter")
        if tag is not None and (not isinstance(tag, str) or _TAG.fullmatch(tag) is None):
            raise NexusScenarioCatalogError("invalid tag filter")
        with self._lock:
            return tuple(case for _, case in sorted(self._cases.items())
                         if (product is None or case.product == product)
                         and (category is None or case.category == category)
                         and (mode is None or case.mode == mode)
                         and (tag is None or tag in case.tags))

    def _bounded_snapshot(self, cases: Mapping[str, ScenarioCase]) -> dict[str, Any]:
        rows = [c.to_dict() for _, c in sorted(cases.items())]
        base = {"schema": CATALOG_SCHEMA, "count": len(rows), "cases": rows}
        snapshot = {**base, "catalog_sha256": _digest(base)}
        if len(_canonical(snapshot)) > self.limits.max_snapshot_bytes:
            raise NexusScenarioCatalogError("catalog exceeds snapshot byte limit")
        return snapshot

    def snapshot(self) -> dict[str, Any]:
        """Deterministic JSON-safe snapshot, with no implicit file export."""
        with self._lock:
            return self._bounded_snapshot(self._cases)

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any], *,
                      limits: ScenarioCatalogLimits | None = None) -> "NexusScenarioCatalog":
        catalog = cls(limits=limits)
        if not isinstance(snapshot, Mapping) or set(snapshot) != _SNAPSHOT_KEYS:
            raise NexusScenarioCatalogError("invalid catalog snapshot fields")
        frozen = _canonical(snapshot)
        if len(frozen) > catalog.limits.max_snapshot_bytes:
            raise NexusScenarioCatalogError("snapshot exceeds byte limit")
        data = json.loads(frozen)
        if data["schema"] != CATALOG_SCHEMA or type(data["count"]) is not int:
            raise NexusScenarioCatalogError("unsupported snapshot schema/count")
        if not isinstance(data["cases"], list) or data["count"] != len(data["cases"]):
            raise NexusScenarioCatalogError("mismatched snapshot count")
        if len(data["cases"]) > catalog.limits.max_scenarios:
            raise NexusScenarioCatalogError("snapshot has too many scenarios")
        check = data["catalog_sha256"]
        if not isinstance(check, str) or _HEX.fullmatch(check) is None:
            raise NexusScenarioCatalogError("invalid catalog hash")
        expected = _digest({key: val for key, val in data.items() if key != "catalog_sha256"})
        if not hmac.compare_digest(check, expected):
            raise NexusScenarioCatalogError("snapshot integrity check failed")
        if data["cases"] != sorted(data["cases"], key=lambda x: x["scenario_id"]):
            raise NexusScenarioCatalogError("scenarios must be sorted by ID")
        parsed = [ScenarioCase.from_dict(x) for x in data["cases"]]
        if parsed:
            catalog.register_many(parsed)
        if catalog.snapshot() != data:
            raise NexusScenarioCatalogError("non-canonical catalog snapshot")
        return catalog

    @staticmethod
    def verify_snapshot(snapshot: Mapping[str, Any], *,
                        limits: ScenarioCatalogLimits | None = None) -> bool:
        try:
            NexusScenarioCatalog.from_snapshot(snapshot, limits=limits)
        except (NexusScenarioCatalogError, KeyError, TypeError, ValueError, OverflowError):
            return False
        return True


__all__ = [
    "SCENARIO_SCHEMA", "CATALOG_SCHEMA", "PRODUCTS", "CATEGORIES", "MODES",
    "OPERATORS", "NexusScenarioCatalogError", "ScenarioExpectation", "ScenarioCase",
    "ScenarioCatalogLimits", "NexusScenarioCatalog",
]
