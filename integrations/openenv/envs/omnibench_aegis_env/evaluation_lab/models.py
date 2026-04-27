from __future__ import annotations

"""Typed models for AegisForge Evaluation Lab.

The lab is intentionally defensive-only: it performs read-only static analysis of
public benchmark repositories and generates safe evaluation artifacts. It does
not execute target code, exploit repositories, extract secrets, or generate real
attack payloads.
"""

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional
import copy

JsonDict = Dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def deep_copy_json(value: Any) -> Any:
    return copy.deepcopy(value)


@dataclass(slots=True)
class SerializableModel:
    def to_dict(self) -> JsonDict:
        return _serialize_value(self)


def _serialize_value(value: Any) -> Any:
    if is_dataclass(value):
        raw = asdict(value)
        return {key: _serialize_value(item) for key, item in raw.items()}
    if isinstance(value, Mapping):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    return deep_copy_json(value)


@dataclass(slots=True)
class EvaluationLabRequest(SerializableModel):
    repo_url: str
    mode: str = "read_only_defensive"
    include_findings: bool = True
    include_controlled_scenarios: bool = True
    include_benign_payloads: bool = True

    @classmethod
    def from_mapping(cls, payload: Optional[Mapping[str, Any]] = None) -> "EvaluationLabRequest":
        payload = dict(payload or {})
        repo_url = str(payload.get("repo_url") or payload.get("repository") or "").strip()
        if not repo_url:
            raise ValueError("repo_url is required")
        return cls(
            repo_url=repo_url,
            mode=str(payload.get("mode") or "read_only_defensive"),
            include_findings=bool(payload.get("include_findings", True)),
            include_controlled_scenarios=bool(payload.get("include_controlled_scenarios", True)),
            include_benign_payloads=bool(payload.get("include_benign_payloads", True)),
        )


@dataclass(slots=True)
class Finding(SerializableModel):
    category: str
    severity: str
    file: str
    line: int
    evidence: str
    recommendation: str


@dataclass(slots=True)
class RepoShape(SerializableModel):
    has_dockerfile: bool = False
    has_github_actions: bool = False
    has_tests: bool = False
    has_pyproject: bool = False
    has_src: bool = False
    has_agent_entrypoint: bool = False
    has_readme: bool = False


@dataclass(slots=True)
class ControlledScenario(SerializableModel):
    id: str
    title: str
    goal: str
    mode: str
    success_criteria: List[str] = field(default_factory=list)


@dataclass(slots=True)
class BenignPayload(SerializableModel):
    id: str
    type: str
    purpose: str
    payload: Any


@dataclass(slots=True)
class EvaluationScan(SerializableModel):
    repo_url: str
    mode: str
    files_analyzed: int
    risk_score: int
    repo_shape: JsonDict
    findings: List[JsonDict] = field(default_factory=list)


@dataclass(slots=True)
class EvaluationLabReport(SerializableModel):
    lab: str
    version: str
    timestamp: str
    mode: str
    repo_url: str
    risk_score: int
    files_analyzed: int
    repo_shape: JsonDict
    findings: List[JsonDict]
    controlled_scenarios: List[JsonDict]
    benign_payloads: List[JsonDict]
    agentbeats_alignment: JsonDict
    safety_contract: JsonDict
    recommended_next_steps: List[str]
