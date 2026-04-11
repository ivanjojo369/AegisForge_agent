from __future__ import annotations

"""Wire models and typed payload helpers for omnibench_aegis_env.

This version keeps the overall structure of the older reference file, but it is
aligned to the current AegisForge/OpenEnv stack based on BaseDomain + plain HTTP
mappings. It intentionally avoids importing the retired BaseOpenEnv /
ResetResult / StepResult contract.
"""

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional
import copy
import uuid

JsonDict = Dict[str, Any]


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()



def deep_copy_json(value: Any) -> Any:
    return copy.deepcopy(value)


@dataclass(slots=True)
class SerializableModel:
    """Small mixin for dataclass-based JSON serialization."""

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


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ResetRequest(SerializableModel):
    seed: Optional[int] = None
    scenario_id: Optional[str] = None
    mission_id: Optional[str] = None
    options: JsonDict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Optional[Mapping[str, Any]] = None) -> "ResetRequest":
        payload = dict(payload or {})
        return cls(
            seed=_coerce_optional_int(payload.get("seed")),
            scenario_id=_coerce_optional_str(payload.get("scenario_id")),
            mission_id=_coerce_optional_str(payload.get("mission_id")),
            options=dict(payload.get("options") or {}),
        )


@dataclass(slots=True)
class StepRequest(SerializableModel):
    """Action input accepted by the environment server.

    Supports two shapes:
    1) canonical/internal: {"name": "inspect_inventory", "args": {}}
    2) shorthand/eval-friendly: {"action": "inspect_inventory"}
    """

    name: str
    args: JsonDict = field(default_factory=dict)

    @property
    def value(self) -> Any:
        return self.args.get("value")

    def to_action_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "args": deep_copy_json(self.args),
        }

    def to_shorthand_dict(self) -> JsonDict:
        payload = {"action": self.name}
        payload.update(deep_copy_json(self.args))
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "StepRequest":
        if not isinstance(payload, Mapping):
            raise TypeError("step payload must be a mapping")

        if isinstance(payload.get("name"), str):
            args = payload.get("args") or {}
            if not isinstance(args, Mapping):
                raise ValueError("step payload field `args` must be a mapping")
            return cls(name=str(payload["name"]), args=dict(args))

        if isinstance(payload.get("action"), str):
            name = str(payload["action"])
            args = {key: value for key, value in payload.items() if key != "action"}
            return cls(name=name, args=args)

        raise ValueError("step payload must include either `name` or `action`")


# ---------------------------------------------------------------------------
# Observation / state models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HistoryEntry(SerializableModel):
    index: int
    timestamp: str
    event: str
    payload: JsonDict = field(default_factory=dict)

    @classmethod
    def make(
        cls,
        *,
        index: int,
        event: str,
        payload: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> "HistoryEntry":
        return cls(
            index=index,
            timestamp=timestamp or utc_now_iso(),
            event=event,
            payload=dict(payload or {}),
        )


@dataclass(slots=True)
class ObservationPayload(SerializableModel):
    text: str = ""
    domain: str = "general"
    mission: str = ""
    scenario_id: str = ""
    step_count: int = 0
    max_steps: int = 0
    progress: int = 0
    target_progress: int = 0
    available_actions: List[str] = field(default_factory=list)
    alerts: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    visible_inventory_summary: List[JsonDict] = field(default_factory=list)
    status_flags: JsonDict = field(default_factory=dict)
    evidence_count: int = 0
    last_event: str = "reset"
    progress_view: JsonDict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Optional[Mapping[str, Any]] = None) -> "ObservationPayload":
        payload = dict(payload or {})
        return cls(
            text=str(payload.get("text") or ""),
            domain=str(payload.get("domain") or "general"),
            mission=str(payload.get("mission") or ""),
            scenario_id=str(payload.get("scenario_id") or ""),
            step_count=int(payload.get("step_count") or 0),
            max_steps=int(payload.get("max_steps") or 0),
            progress=int(payload.get("progress") or 0),
            target_progress=int(payload.get("target_progress") or 0),
            available_actions=[str(item) for item in payload.get("available_actions") or []],
            alerts=[str(item) for item in payload.get("alerts") or []],
            notes=[str(item) for item in payload.get("notes") or []],
            visible_inventory_summary=[dict(item) for item in payload.get("visible_inventory_summary") or []],
            status_flags=dict(payload.get("status_flags") or {}),
            evidence_count=int(payload.get("evidence_count") or 0),
            last_event=str(payload.get("last_event") or "reset"),
            progress_view=dict(payload.get("progress_view") or {}),
        )


@dataclass(slots=True)
class EpisodeState(SerializableModel):
    """Normalized environment state for `/state` and client helpers."""

    episode_id: str
    score: float = 0.0
    step_count: int = 0
    max_steps: int = 0
    target_score: int = 1
    done: bool = False
    success: bool = False
    last_action: str = ""
    history: List[JsonDict] = field(default_factory=list)
    scenario_id: Optional[str] = None
    mission_id: Optional[str] = None
    domain: str = "general"
    env_name: str = "omnibench_aegis_env"
    env_id: Optional[str] = None
    state_data: JsonDict = field(default_factory=dict)

    def append_history(self, event: str, payload: Optional[Mapping[str, Any]] = None) -> None:
        entry = HistoryEntry.make(
            index=len(self.history) + 1,
            event=event,
            payload=payload,
        )
        self.history.append(entry.to_dict())

    @classmethod
    def new(
        cls,
        *,
        env_name: str,
        domain: str,
        scenario_id: Optional[str],
        mission_id: Optional[str] = None,
        max_steps: int = 25,
        target_score: int = 1,
        state_data: Optional[Mapping[str, Any]] = None,
        env_id: Optional[str] = None,
    ) -> "EpisodeState":
        return cls(
            episode_id=str(uuid.uuid4()),
            score=0.0,
            step_count=0,
            max_steps=int(max_steps),
            target_score=int(target_score),
            done=False,
            success=False,
            last_action="",
            history=[],
            scenario_id=scenario_id,
            mission_id=mission_id,
            domain=domain,
            env_name=env_name,
            env_id=env_id,
            state_data=dict(state_data or {}),
        )

    @classmethod
    def from_state_envelope(cls, payload: Mapping[str, Any]) -> "EpisodeState":
        state = dict(payload.get("state") or {})
        history = state.get("history") if isinstance(state, Mapping) else None
        last_action = state.get("last_action") if isinstance(state, Mapping) else None
        if isinstance(last_action, Mapping):
            last_action_name = str(last_action.get("name") or "")
        else:
            last_action_name = str(last_action or "")
        return cls(
            episode_id=str(payload.get("mission_id") or payload.get("scenario_id") or "episode"),
            score=float(state.get("score") or 0.0),
            step_count=int(state.get("step_count") or 0),
            max_steps=int(state.get("max_steps") or 0),
            target_score=int(state.get("target_score") or state.get("target_progress") or 1),
            done=bool(state.get("done") or False),
            success=bool(state.get("success") or False),
            last_action=last_action_name,
            history=list(history) if isinstance(history, list) else [],
            scenario_id=_coerce_optional_str(payload.get("scenario_id")),
            mission_id=_coerce_optional_str(payload.get("mission_id")),
            domain=str(state.get("domain") or "general"),
            env_name=str((payload.get("last_info") or {}).get("env_name") or "omnibench_aegis_env"),
            env_id=_coerce_optional_str(payload.get("env_id")),
            state_data=state,
        )


@dataclass(slots=True)
class HealthPayload(SerializableModel):
    status: str = "ok"
    env: str = "omnibench_aegis_env"
    env_name: str = "omnibench_aegis_env"
    initialized: bool = True
    version: str = "0.1.0"
    domain: str = "general"
    scenarios: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=utc_now_iso)

    @classmethod
    def from_mapping(cls, payload: Optional[Mapping[str, Any]] = None) -> "HealthPayload":
        payload = dict(payload or {})
        scenarios = payload.get("scenarios")
        domain = payload.get("domain") or payload.get("active_domain") or "general"
        return cls(
            status=str(payload.get("status") or "ok"),
            env=str(payload.get("env") or payload.get("env_name") or "omnibench_aegis_env"),
            env_name=str(payload.get("env_name") or payload.get("env") or "omnibench_aegis_env"),
            initialized=bool(payload.get("initialized") if payload.get("initialized") is not None else True),
            version=str(payload.get("version") or "0.1.0"),
            domain=str(domain),
            scenarios=[str(item) for item in scenarios] if isinstance(scenarios, list) else [],
            timestamp=str(payload.get("timestamp") or utc_now_iso()),
        )


@dataclass(slots=True)
class ContractPayload(SerializableModel):
    env_id: str = ""
    name: str = "omnibench_aegis_env"
    version: str = "0.1.0"
    description: str = ""
    primary_scenarios: List[str] = field(default_factory=list)
    supported_domains: List[str] = field(default_factory=list)
    supported_env_ids: List[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: Optional[Mapping[str, Any]] = None) -> "ContractPayload":
        payload = dict(payload or {})
        return cls(
            env_id=str(payload.get("env_id") or ""),
            name=str(payload.get("name") or "omnibench_aegis_env"),
            version=str(payload.get("version") or "0.1.0"),
            description=str(payload.get("description") or ""),
            primary_scenarios=[str(item) for item in payload.get("primary_scenarios") or []],
            supported_domains=[str(item) for item in payload.get("supported_domains") or []],
            supported_env_ids=[str(item) for item in payload.get("supported_env_ids") or []],
        )


# ---------------------------------------------------------------------------
# Endpoint response envelopes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ResetResponse(SerializableModel):
    observation: JsonDict
    state: JsonDict
    info: JsonDict = field(default_factory=dict)
    env_id: Optional[str] = None
    scenario_id: Optional[str] = None
    mission_id: Optional[str] = None
    actions: List[Any] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ResetResponse":
        return cls(
            observation=dict(payload.get("observation") or {}),
            state=dict(payload.get("state") or {}),
            info=dict(payload.get("info") or {}),
            env_id=_coerce_optional_str(payload.get("env_id")),
            scenario_id=_coerce_optional_str(payload.get("scenario_id")),
            mission_id=_coerce_optional_str(payload.get("mission_id")),
            actions=list(payload.get("actions") or []),
        )


@dataclass(slots=True)
class StepResponse(SerializableModel):
    observation: JsonDict
    reward: float
    done: bool
    truncated: bool
    info: JsonDict = field(default_factory=dict)
    state: JsonDict = field(default_factory=dict)
    env_id: Optional[str] = None
    scenario_id: Optional[str] = None
    mission_id: Optional[str] = None
    actions: List[Any] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "StepResponse":
        return cls(
            observation=dict(payload.get("observation") or {}),
            reward=float(payload.get("reward") or 0.0),
            done=bool(payload.get("done") or False),
            truncated=bool(payload.get("truncated") or False),
            info=dict(payload.get("info") or {}),
            state=dict(payload.get("state") or {}),
            env_id=_coerce_optional_str(payload.get("env_id")),
            scenario_id=_coerce_optional_str(payload.get("scenario_id")),
            mission_id=_coerce_optional_str(payload.get("mission_id")),
            actions=list(payload.get("actions") or []),
        )


@dataclass(slots=True)
class StateEnvelope(SerializableModel):
    env_id: Optional[str] = None
    scenario_id: Optional[str] = None
    mission_id: Optional[str] = None
    state: JsonDict = field(default_factory=dict)
    last_observation: JsonDict = field(default_factory=dict)
    last_info: JsonDict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "StateEnvelope":
        return cls(
            env_id=_coerce_optional_str(payload.get("env_id")),
            scenario_id=_coerce_optional_str(payload.get("scenario_id")),
            mission_id=_coerce_optional_str(payload.get("mission_id")),
            state=dict(payload.get("state") or {}),
            last_observation=dict(payload.get("last_observation") or {}),
            last_info=dict(payload.get("last_info") or {}),
        )


@dataclass(slots=True)
class ActionListResponse(SerializableModel):
    env_id: Optional[str] = None
    scenario_id: Optional[str] = None
    actions: List[Any] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ActionListResponse":
        return cls(
            env_id=_coerce_optional_str(payload.get("env_id")),
            scenario_id=_coerce_optional_str(payload.get("scenario_id")),
            actions=list(payload.get("actions") or []),
        )


# ---------------------------------------------------------------------------
# Conversion helpers used by server/client code
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EpisodeTracker:
    """Lightweight tracker compatible with the new state envelope."""

    state: EpisodeState

    def sync_from_state(self, payload: Mapping[str, Any]) -> None:
        refreshed = EpisodeState.from_state_envelope(payload)
        self.state = refreshed

    def record_reset(self, payload: Mapping[str, Any]) -> None:
        self.sync_from_state(
            {
                "env_id": payload.get("env_id"),
                "scenario_id": payload.get("scenario_id"),
                "mission_id": payload.get("mission_id"),
                "state": payload.get("state") or {},
                "last_observation": payload.get("observation") or {},
                "last_info": payload.get("info") or {},
            }
        )

    def record_step(self, payload: Mapping[str, Any]) -> None:
        self.sync_from_state(
            {
                "env_id": payload.get("env_id"),
                "scenario_id": payload.get("scenario_id"),
                "mission_id": payload.get("mission_id"),
                "state": payload.get("state") or {},
                "last_observation": payload.get("observation") or {},
                "last_info": payload.get("info") or {},
            }
        )


@dataclass(slots=True)
class EnvironmentContract(SerializableModel):
    env_id: str
    name: str
    domain: str
    version: str = "0.1.0"
    description: str = ""
    primary_scenarios: List[str] = field(default_factory=list)
    supported_domains: List[str] = field(default_factory=list)
    supported_action_shapes: List[str] = field(
        default_factory=lambda: [
            "canonical:{name,args}",
            "shorthand:{action,value,...}",
        ]
    )
    endpoints: List[str] = field(
        default_factory=lambda: [
            "/health",
            "/contract",
            "/reset",
            "/step",
            "/state",
            "/actions",
        ]
    )


# ---------------------------------------------------------------------------
# Public helper functions
# ---------------------------------------------------------------------------


def normalize_observation(observation: Mapping[str, Any]) -> JsonDict:
    return ObservationPayload.from_mapping(observation).to_dict()



def make_episode_tracker(
    *,
    env_name: str,
    domain: str,
    scenario_id: Optional[str],
    mission_id: Optional[str] = None,
    max_steps: int = 25,
    target_score: int = 1,
    state_data: Optional[Mapping[str, Any]] = None,
    env_id: Optional[str] = None,
) -> EpisodeTracker:
    state = EpisodeState.new(
        env_name=env_name,
        domain=domain,
        scenario_id=scenario_id,
        mission_id=mission_id,
        max_steps=max_steps,
        target_score=target_score,
        state_data=state_data,
        env_id=env_id,
    )
    return EpisodeTracker(state=state)



def build_health_payload(
    *,
    env_name: str,
    domain: str,
    version: str = "0.1.0",
    scenarios: Optional[Iterable[str]] = None,
    initialized: bool = True,
) -> HealthPayload:
    return HealthPayload(
        status="ok",
        env=env_name,
        env_name=env_name,
        initialized=bool(initialized),
        version=version,
        domain=domain,
        scenarios=[str(item) for item in (scenarios or [])],
    )


# ---------------------------------------------------------------------------
# Internal coercion helpers
# ---------------------------------------------------------------------------


def _coerce_optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None



def _coerce_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "ActionListResponse",
    "ContractPayload",
    "EnvironmentContract",
    "EpisodeState",
    "EpisodeTracker",
    "HealthPayload",
    "HistoryEntry",
    "JsonDict",
    "ObservationPayload",
    "ResetRequest",
    "ResetResponse",
    "SerializableModel",
    "StateEnvelope",
    "StepRequest",
    "StepResponse",
    "build_health_payload",
    "make_episode_tracker",
    "normalize_observation",
    "utc_now_iso",
]
