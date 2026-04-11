from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionRecord:
    session_id: str
    domain_name: str
    domain: Any
    state: dict[str, Any]
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "domain": self.domain_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "step_count": self.state.get("step_count", 0),
            "done": self.state.get("done", False),
            "success": self.state.get("success", False),
            "mission": self.state.get("mission"),
            "task_category": self.state.get("task_category"),
        }


class StateEngine:
    """
    Session/state lifecycle manager for OmniBench Aegis environments.

    Responsibilities:
    - create/reset sessions
    - route actions to the correct domain
    - store current state
    - expose health/session inspection helpers

    This is intentionally domain-agnostic.
    """

    def __init__(
        self,
        *,
        domain_registry: dict[str, Any] | None = None,
        max_sessions: int = 1024,
    ) -> None:
        self.max_sessions = int(max_sessions)
        self._lock = RLock()
        self._sessions: dict[str, SessionRecord] = {}
        self._domain_registry: dict[str, Any] = {}
        if domain_registry:
            self.register_domains(domain_registry)

    # ------------------------------------------------------------------
    # Registry management
    # ------------------------------------------------------------------

    def register_domain(self, name: str, domain_cls: Any) -> None:
        with self._lock:
            self._domain_registry[str(name)] = domain_cls

    def register_domains(self, mapping: dict[str, Any]) -> None:
        with self._lock:
            for name, domain_cls in mapping.items():
                self._domain_registry[str(name)] = domain_cls

    def _lazy_load_registry(self) -> None:
        if self._domain_registry:
            return

        try:
            from .domains.registry import DOMAIN_REGISTRY  # type: ignore
        except Exception:
            DOMAIN_REGISTRY = {}

        if DOMAIN_REGISTRY:
            self.register_domains(DOMAIN_REGISTRY)

    def available_domains(self) -> list[str]:
        self._lazy_load_registry()
        return sorted(self._domain_registry.keys())

    def _build_domain(self, domain_name: str) -> Any:
        self._lazy_load_registry()

        try:
            domain_cls = self._domain_registry[domain_name]
        except KeyError as exc:
            raise ValueError(
                f"Unknown domain '{domain_name}'. "
                f"Available domains: {self.available_domains()}"
            ) from exc

        return domain_cls()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def create_session(
        self,
        *,
        domain_name: str,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        **reset_kwargs: Any,
    ) -> dict[str, Any]:
        with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise RuntimeError(
                    f"Max sessions reached ({self.max_sessions}). "
                    "Delete old sessions before creating new ones."
                )

            sid = session_id or str(uuid4())
            if sid in self._sessions:
                raise ValueError(f"Session '{sid}' already exists.")

            domain = self._build_domain(domain_name)
            payload = domain.reset(**reset_kwargs)

            session = SessionRecord(
                session_id=sid,
                domain_name=domain_name,
                domain=domain,
                state=deepcopy(payload["state"]),
                metadata=deepcopy(metadata or {}),
            )
            self._sessions[sid] = session

            return self._decorate_payload(session, payload)

    def reset_session(
        self,
        session_id: str,
        *,
        metadata: dict[str, Any] | None = None,
        **reset_kwargs: Any,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._get_session(session_id)
            payload = session.domain.reset(**reset_kwargs)
            session.state = deepcopy(payload["state"])
            session.updated_at = utc_now_iso()

            if metadata:
                session.metadata.update(deepcopy(metadata))

            return self._decorate_payload(session, payload)

    def restore_session(
        self,
        *,
        domain_name: str,
        state: dict[str, Any],
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise RuntimeError(
                    f"Max sessions reached ({self.max_sessions}). "
                    "Delete old sessions before creating new ones."
                )

            sid = session_id or str(uuid4())
            if sid in self._sessions:
                raise ValueError(f"Session '{sid}' already exists.")

            domain = self._build_domain(domain_name)
            session = SessionRecord(
                session_id=sid,
                domain_name=domain_name,
                domain=domain,
                state=deepcopy(state),
                metadata=deepcopy(metadata or {}),
            )
            self._sessions[sid] = session

            payload = {
                "observation": domain.get_observation(session.state),
                "state": deepcopy(session.state),
                "info": {
                    "restored": True,
                    "domain": domain_name,
                },
            }
            return self._decorate_payload(session, payload)

    def step_session(self, session_id: str, action: dict[str, Any] | None) -> dict[str, Any]:
        with self._lock:
            session = self._get_session(session_id)
            payload = session.domain.step(session.state, action)
            session.state = deepcopy(payload["state"])
            session.updated_at = utc_now_iso()
            return self._decorate_payload(session, payload)

    def delete_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._get_session(session_id)
            summary = session.summary()
            del self._sessions[session_id]
            return {
                "deleted": True,
                "session": summary,
            }

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_state(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._get_session(session_id)
            return {
                "session_id": session.session_id,
                "domain": session.domain_name,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "metadata": deepcopy(session.metadata),
                "state": deepcopy(session.state),
            }

    def set_state(self, session_id: str, state: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            session = self._get_session(session_id)
            session.state = deepcopy(state)
            session.updated_at = utc_now_iso()
            return self.get_state(session_id)

    def get_observation(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._get_session(session_id)
            return {
                "session_id": session.session_id,
                "domain": session.domain_name,
                "observation": session.domain.get_observation(session.state),
            }

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [session.summary() for session in self._sessions.values()]

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": "ok",
                "active_sessions": len(self._sessions),
                "max_sessions": self.max_sessions,
                "available_domains": self.available_domains(),
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_session(self, session_id: str) -> SessionRecord:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"Unknown session_id '{session_id}'.") from exc

    def _decorate_payload(
        self,
        session: SessionRecord,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = deepcopy(payload)
        info = dict(response.get("info", {}))
        info.update(
            {
                "session_id": session.session_id,
                "domain": session.domain_name,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
            }
        )
        response["info"] = info
        response["session_id"] = session.session_id
        response["domain"] = session.domain_name
        return response
    