from __future__ import annotations

"""Schemas for the AegisForge OpenEnv adapter.

These models are intentionally lightweight and dependency-free so the adapter
can be used from tests, local scripts, and the core runtime without requiring
Pydantic. They normalize the contract used by the local OmniBench Aegis env and
also tolerate the shorthand action shape used by the OpenEnv evaluator.
"""

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Mapping, MutableMapping, Sequence


JsonDict = dict[str, Any]


class SchemaError(ValueError):
    """Raised when a payload cannot be normalized into an expected schema."""



def _as_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    raise SchemaError(f"{field_name} must be a mapping, got {type(value).__name__}")



def _as_dict(value: Any, *, field_name: str) -> JsonDict:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    raise SchemaError(f"{field_name} must be a mapping, got {type(value).__name__}")



def _as_list_of_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item) for item in value]
    return [str(value)]



def _coerce_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)



def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)



def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default



def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class OpenEnvAction:
    """Canonical action representation for the adapter.

    Accepted inputs:
    - {"name": "advance", "args": {"value": 1}}
    - {"action": "advance", "value": 1}
    - {"action": "advance", "args": {"value": 1}}
    """

    name: str
    args: JsonDict = field(default_factory=dict)

    @classmethod
    def from_any(cls, payload: Mapping[str, Any] | "OpenEnvAction") -> "OpenEnvAction":
        if isinstance(payload, OpenEnvAction):
            return cls(name=payload.name, args=dict(payload.args))

        data = _as_mapping(payload, field_name="action")

        if "name" in data:
            name = _coerce_text(data.get("name")).strip()
            args = _as_dict(data.get("args"), field_name="action.args")
        else:
            name = _coerce_text(data.get("action")).strip()
            if "args" in data and isinstance(data.get("args"), Mapping):
                args = dict(data["args"])
            else:
                args = {}
                if "value" in data:
                    args["value"] = data["value"]
                for key, value in data.items():
                    if key not in {"action", "name", "args"}:
                        args[key] = value

        if not name:
            raise SchemaError("action.name/action must be a non-empty string")
        return cls(name=name, args=args)

    def to_wire(self) -> JsonDict:
        return {"name": self.name, "args": dict(self.args)}

    def to_shorthand(self) -> JsonDict:
        payload: JsonDict = {"action": self.name}
        if "value" in self.args:
            payload["value"] = self.args["value"]
        else:
            payload.update(self.args)
        return payload


@dataclass(slots=True)
class OpenEnvActionSpec:
    name: str
    description: str = ""
    args_schema: JsonDict = field(default_factory=dict)

    @classmethod
    def from_any(cls, payload: Mapping[str, Any] | "OpenEnvActionSpec") -> "OpenEnvActionSpec":
        if isinstance(payload, OpenEnvActionSpec):
            return cls(
                name=payload.name,
                description=payload.description,
                args_schema=dict(payload.args_schema),
            )
        data = _as_mapping(payload, field_name="action_spec")
        name = _coerce_text(data.get("name")).strip()
        if not name:
            raise SchemaError("action_spec.name must be a non-empty string")
        return cls(
            name=name,
            description=_coerce_text(data.get("description")),
            args_schema=_as_dict(data.get("args_schema"), field_name="action_spec.args_schema"),
        )

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "description": self.description,
            "args_schema": dict(self.args_schema),
        }


@dataclass(slots=True)
class ObservationPayload:
    text: str = ""
    data: JsonDict = field(default_factory=dict)

    @classmethod
    def from_any(cls, payload: Any) -> "ObservationPayload":
        if isinstance(payload, ObservationPayload):
            return cls(text=payload.text, data=dict(payload.data))
        if isinstance(payload, str):
            return cls(text=payload)
        if isinstance(payload, Mapping):
            data = dict(payload)
            if "text" in data:
                text = _coerce_text(data.pop("text"))
            elif "message" in data:
                text = _coerce_text(data.pop("message"))
            elif "content" in data:
                text = _coerce_text(data.pop("content"))
            else:
                text = ""
            return cls(text=text, data=data)
        return cls(text=_coerce_text(payload))

    def to_dict(self) -> JsonDict:
        payload = dict(self.data)
        if self.text:
            payload["text"] = self.text
        return payload


@dataclass(slots=True)
class EpisodeState:
    episode_id: str = ""
    score: float = 0.0
    step_count: int = 0
    max_steps: int = 0
    target_score: float = 0.0
    done: bool = False
    success: bool = False
    last_action: str = ""
    history: list[JsonDict] = field(default_factory=list)
    scenario_id: str = ""
    mission_id: str = ""
    domain: str = ""
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_any(cls, payload: Mapping[str, Any] | "EpisodeState") -> "EpisodeState":
        if isinstance(payload, EpisodeState):
            return cls(
                episode_id=payload.episode_id,
                score=payload.score,
                step_count=payload.step_count,
                max_steps=payload.max_steps,
                target_score=payload.target_score,
                done=payload.done,
                success=payload.success,
                last_action=payload.last_action,
                history=[dict(item) for item in payload.history],
                scenario_id=payload.scenario_id,
                mission_id=payload.mission_id,
                domain=payload.domain,
                metadata=dict(payload.metadata),
            )

        data = _as_mapping(payload, field_name="state")
        history_raw = data.get("history")
        history: list[JsonDict] = []
        if isinstance(history_raw, Sequence) and not isinstance(history_raw, (str, bytes, bytearray)):
            for item in history_raw:
                history.append(_as_dict(item, field_name="state.history[]"))

        metadata = _as_dict(data.get("metadata"), field_name="state.metadata")

        return cls(
            episode_id=_coerce_text(data.get("episode_id")),
            score=_coerce_float(data.get("score"), default=0.0),
            step_count=_coerce_int(data.get("step_count"), default=0),
            max_steps=_coerce_int(data.get("max_steps"), default=0),
            target_score=_coerce_float(data.get("target_score"), default=0.0),
            done=_coerce_bool(data.get("done"), default=False),
            success=_coerce_bool(data.get("success"), default=False),
            last_action=_coerce_text(data.get("last_action")),
            history=history,
            scenario_id=_coerce_text(data.get("scenario_id")),
            mission_id=_coerce_text(data.get("mission_id")),
            domain=_coerce_text(data.get("domain")),
            metadata=metadata,
        )

    def to_dict(self) -> JsonDict:
        payload: JsonDict = {
            "episode_id": self.episode_id,
            "score": self.score,
            "step_count": self.step_count,
            "max_steps": self.max_steps,
            "target_score": self.target_score,
            "done": self.done,
            "success": self.success,
            "last_action": self.last_action,
            "history": [dict(item) for item in self.history],
        }
        if self.scenario_id:
            payload["scenario_id"] = self.scenario_id
        if self.mission_id:
            payload["mission_id"] = self.mission_id
        if self.domain:
            payload["domain"] = self.domain
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(slots=True)
class HealthPayload:
    status: str = "ok"
    env: str = ""
    initialized: bool = False
    runtime: str = ""
    env_id: str = ""
    supported_domains: list[str] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_any(cls, payload: Mapping[str, Any] | "HealthPayload") -> "HealthPayload":
        if isinstance(payload, HealthPayload):
            return cls(
                status=payload.status,
                env=payload.env,
                initialized=payload.initialized,
                runtime=payload.runtime,
                env_id=payload.env_id,
                supported_domains=list(payload.supported_domains),
                metadata=dict(payload.metadata),
            )
        data = _as_mapping(payload, field_name="health")
        known = {"status", "env", "initialized", "runtime", "env_id", "supported_domains"}
        metadata = {k: v for k, v in data.items() if k not in known}
        return cls(
            status=_coerce_text(data.get("status"), default="ok"),
            env=_coerce_text(data.get("env")),
            initialized=_coerce_bool(data.get("initialized"), default=False),
            runtime=_coerce_text(data.get("runtime")),
            env_id=_coerce_text(data.get("env_id")),
            supported_domains=_as_list_of_strings(data.get("supported_domains")),
            metadata=metadata,
        )

    def to_dict(self) -> JsonDict:
        payload: JsonDict = {
            "status": self.status,
            "env": self.env,
            "initialized": self.initialized,
        }
        if self.runtime:
            payload["runtime"] = self.runtime
        if self.env_id:
            payload["env_id"] = self.env_id
        if self.supported_domains:
            payload["supported_domains"] = list(self.supported_domains)
        payload.update(self.metadata)
        return payload


@dataclass(slots=True)
class ResetRequest:
    seed: int | None = None
    scenario_id: str = ""
    mission_id: str = ""
    domain: str = ""
    options: JsonDict = field(default_factory=dict)

    @classmethod
    def from_any(cls, payload: Mapping[str, Any] | "ResetRequest" | None) -> "ResetRequest":
        if payload is None:
            return cls()
        if isinstance(payload, ResetRequest):
            return cls(
                seed=payload.seed,
                scenario_id=payload.scenario_id,
                mission_id=payload.mission_id,
                domain=payload.domain,
                options=dict(payload.options),
            )
        data = _as_mapping(payload, field_name="reset")
        seed = data.get("seed")
        return cls(
            seed=_coerce_int(seed) if seed is not None else None,
            scenario_id=_coerce_text(data.get("scenario_id")),
            mission_id=_coerce_text(data.get("mission_id")),
            domain=_coerce_text(data.get("domain")),
            options=_as_dict(data.get("options"), field_name="reset.options"),
        )

    def to_dict(self) -> JsonDict:
        payload: JsonDict = {"options": dict(self.options)}
        if self.seed is not None:
            payload["seed"] = self.seed
        if self.scenario_id:
            payload["scenario_id"] = self.scenario_id
        if self.mission_id:
            payload["mission_id"] = self.mission_id
        if self.domain:
            payload["domain"] = self.domain
        return payload


@dataclass(slots=True)
class StepRequest:
    action: OpenEnvAction

    @classmethod
    def from_any(cls, payload: Mapping[str, Any] | OpenEnvAction | "StepRequest") -> "StepRequest":
        if isinstance(payload, StepRequest):
            return cls(action=OpenEnvAction.from_any(payload.action))
        if isinstance(payload, OpenEnvAction):
            return cls(action=OpenEnvAction.from_any(payload))
        return cls(action=OpenEnvAction.from_any(payload))

    def to_dict(self) -> JsonDict:
        return self.action.to_wire()

    def to_shorthand(self) -> JsonDict:
        return self.action.to_shorthand()


@dataclass(slots=True)
class ResetResponse:
    observation: ObservationPayload
    state: EpisodeState
    info: JsonDict = field(default_factory=dict)

    @classmethod
    def from_any(cls, payload: Mapping[str, Any] | "ResetResponse") -> "ResetResponse":
        if isinstance(payload, ResetResponse):
            return cls(
                observation=ObservationPayload.from_any(payload.observation),
                state=EpisodeState.from_any(payload.state),
                info=dict(payload.info),
            )
        data = _as_mapping(payload, field_name="reset_response")
        return cls(
            observation=ObservationPayload.from_any(data.get("observation", {})),
            state=EpisodeState.from_any(data.get("state", {})),
            info=_as_dict(data.get("info"), field_name="reset_response.info"),
        )

    def to_dict(self) -> JsonDict:
        return {
            "observation": self.observation.to_dict(),
            "state": self.state.to_dict(),
            "info": dict(self.info),
        }


@dataclass(slots=True)
class StepResponse:
    observation: ObservationPayload
    reward: float
    done: bool
    truncated: bool
    info: JsonDict
    state: EpisodeState

    @classmethod
    def from_any(cls, payload: Mapping[str, Any] | "StepResponse") -> "StepResponse":
        if isinstance(payload, StepResponse):
            return cls(
                observation=ObservationPayload.from_any(payload.observation),
                reward=payload.reward,
                done=payload.done,
                truncated=payload.truncated,
                info=dict(payload.info),
                state=EpisodeState.from_any(payload.state),
            )
        data = _as_mapping(payload, field_name="step_response")
        return cls(
            observation=ObservationPayload.from_any(data.get("observation", {})),
            reward=_coerce_float(data.get("reward"), default=0.0),
            done=_coerce_bool(data.get("done"), default=False),
            truncated=_coerce_bool(data.get("truncated"), default=False),
            info=_as_dict(data.get("info"), field_name="step_response.info"),
            state=EpisodeState.from_any(data.get("state", {})),
        )

    def to_dict(self) -> JsonDict:
        return {
            "observation": self.observation.to_dict(),
            "reward": self.reward,
            "done": self.done,
            "truncated": self.truncated,
            "info": dict(self.info),
            "state": self.state.to_dict(),
        }


@dataclass(slots=True)
class OpenEnvContract:
    name: str
    version: str = ""
    description: str = ""
    supported_domains: list[str] = field(default_factory=list)
    actions: list[OpenEnvActionSpec] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_any(cls, payload: Mapping[str, Any] | "OpenEnvContract") -> "OpenEnvContract":
        if isinstance(payload, OpenEnvContract):
            return cls(
                name=payload.name,
                version=payload.version,
                description=payload.description,
                supported_domains=list(payload.supported_domains),
                actions=[OpenEnvActionSpec.from_any(item) for item in payload.actions],
                metadata=dict(payload.metadata),
            )
        data = _as_mapping(payload, field_name="contract")
        actions: list[OpenEnvActionSpec] = []
        raw_actions = data.get("actions")
        if isinstance(raw_actions, Sequence) and not isinstance(raw_actions, (str, bytes, bytearray)):
            actions = [OpenEnvActionSpec.from_any(item) for item in raw_actions]
        known = {"name", "version", "description", "supported_domains", "actions"}
        metadata = {k: v for k, v in data.items() if k not in known}
        name = _coerce_text(data.get("name")).strip()
        if not name:
            raise SchemaError("contract.name must be a non-empty string")
        return cls(
            name=name,
            version=_coerce_text(data.get("version")),
            description=_coerce_text(data.get("description")),
            supported_domains=_as_list_of_strings(data.get("supported_domains")),
            actions=actions,
            metadata=metadata,
        )

    def to_dict(self) -> JsonDict:
        payload: JsonDict = {
            "name": self.name,
            "supported_domains": list(self.supported_domains),
            "actions": [item.to_dict() for item in self.actions],
        }
        if self.version:
            payload["version"] = self.version
        if self.description:
            payload["description"] = self.description
        payload.update(self.metadata)
        return payload


@dataclass(slots=True)
class ClientBundle:
    base_url: str
    timeout: float = 10.0
    reset_payload: ResetRequest = field(default_factory=ResetRequest)
    action_plan: list[OpenEnvAction] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_any(cls, payload: Mapping[str, Any] | "ClientBundle") -> "ClientBundle":
        if isinstance(payload, ClientBundle):
            return cls(
                base_url=payload.base_url,
                timeout=payload.timeout,
                reset_payload=ResetRequest.from_any(payload.reset_payload),
                action_plan=[OpenEnvAction.from_any(item) for item in payload.action_plan],
                metadata=dict(payload.metadata),
            )
        data = _as_mapping(payload, field_name="client_bundle")
        raw_plan = data.get("action_plan") or []
        actions: list[OpenEnvAction] = []
        if isinstance(raw_plan, Sequence) and not isinstance(raw_plan, (str, bytes, bytearray)):
            actions = [OpenEnvAction.from_any(item) for item in raw_plan]
        known = {"base_url", "timeout", "reset_payload", "action_plan"}
        metadata = {k: v for k, v in data.items() if k not in known}
        base_url = _coerce_text(data.get("base_url")).strip()
        if not base_url:
            raise SchemaError("client_bundle.base_url must be a non-empty string")
        return cls(
            base_url=base_url,
            timeout=_coerce_float(data.get("timeout"), default=10.0),
            reset_payload=ResetRequest.from_any(data.get("reset_payload")),
            action_plan=actions,
            metadata=metadata,
        )

    def to_dict(self) -> JsonDict:
        payload: JsonDict = {
            "base_url": self.base_url,
            "timeout": self.timeout,
            "reset_payload": self.reset_payload.to_dict(),
            "action_plan": [item.to_shorthand() for item in self.action_plan],
        }
        payload.update(self.metadata)
        return payload


@dataclass(slots=True)
class EvalPayload:
    adapter: str = "openenv"
    environment_url: str = ""
    timeout: float = 10.0
    live_check: bool = True
    require_success: bool = False
    seed: int | None = None
    env_name: str = ""
    episode_id: str = ""
    action_plan: list[OpenEnvAction] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_any(cls, payload: Mapping[str, Any] | "EvalPayload") -> "EvalPayload":
        if isinstance(payload, EvalPayload):
            return cls(
                adapter=payload.adapter,
                environment_url=payload.environment_url,
                timeout=payload.timeout,
                live_check=payload.live_check,
                require_success=payload.require_success,
                seed=payload.seed,
                env_name=payload.env_name,
                episode_id=payload.episode_id,
                action_plan=[OpenEnvAction.from_any(item) for item in payload.action_plan],
                metadata=dict(payload.metadata),
            )
        data = _as_mapping(payload, field_name="eval_payload")
        raw_plan = data.get("action_plan") or []
        actions: list[OpenEnvAction] = []
        if isinstance(raw_plan, Sequence) and not isinstance(raw_plan, (str, bytes, bytearray)):
            actions = [OpenEnvAction.from_any(item) for item in raw_plan]
        known = {
            "adapter",
            "environment_url",
            "base_url",
            "timeout",
            "live_check",
            "require_success",
            "seed",
            "env_name",
            "episode_id",
            "action_plan",
        }
        metadata = {k: v for k, v in data.items() if k not in known}
        environment_url = _coerce_text(data.get("environment_url") or data.get("base_url")).strip()
        return cls(
            adapter=_coerce_text(data.get("adapter"), default="openenv") or "openenv",
            environment_url=environment_url,
            timeout=_coerce_float(data.get("timeout"), default=10.0),
            live_check=_coerce_bool(data.get("live_check"), default=True),
            require_success=_coerce_bool(data.get("require_success"), default=False),
            seed=_coerce_int(data.get("seed")) if data.get("seed") is not None else None,
            env_name=_coerce_text(data.get("env_name")),
            episode_id=_coerce_text(data.get("episode_id")),
            action_plan=actions,
            metadata=metadata,
        )

    def to_dict(self) -> JsonDict:
        payload: JsonDict = {
            "adapter": self.adapter,
            "environment_url": self.environment_url,
            "timeout": self.timeout,
            "live_check": self.live_check,
            "require_success": self.require_success,
            "action_plan": [item.to_shorthand() for item in self.action_plan],
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        if self.env_name:
            payload["env_name"] = self.env_name
        if self.episode_id:
            payload["episode_id"] = self.episode_id
        payload.update(self.metadata)
        return payload


__all__ = [
    "ClientBundle",
    "EpisodeState",
    "EvalPayload",
    "HealthPayload",
    "ObservationPayload",
    "OpenEnvAction",
    "OpenEnvActionSpec",
    "OpenEnvContract",
    "ResetRequest",
    "ResetResponse",
    "SchemaError",
    "StepRequest",
    "StepResponse",
]
