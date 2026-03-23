from __future__ import annotations

from typing import Any

import httpx


class AegisArenaEnvClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8012", timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return response.json()

    def reset(
        self,
        seed: int | None = None,
        mission_type: str | None = None,
        heldout_mode: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "heldout_mode": heldout_mode,
        }
        if seed is not None:
            payload["seed"] = seed
        if mission_type is not None:
            payload["mission_type"] = mission_type

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/reset", json=payload)
            response.raise_for_status()
            return response.json()

    def step(
        self,
        action: str,
        target: str | None = None,
        tool_name: str | None = None,
        answer: str | None = None,
        plan_text: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "action": action,
            "payload": payload or {},
        }
        if target is not None:
            body["target"] = target
        if tool_name is not None:
            body["tool_name"] = tool_name
        if answer is not None:
            body["answer"] = answer
        if plan_text is not None:
            body["plan_text"] = plan_text

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/step", json=body)
            response.raise_for_status()
            return response.json()

    def state(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/state")
            response.raise_for_status()
            return response.json()


class AsyncAegisArenaEnvClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8012", timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return response.json()

    async def reset(
        self,
        seed: int | None = None,
        mission_type: str | None = None,
        heldout_mode: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "heldout_mode": heldout_mode,
        }
        if seed is not None:
            payload["seed"] = seed
        if mission_type is not None:
            payload["mission_type"] = mission_type

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/reset", json=payload)
            response.raise_for_status()
            return response.json()

    async def step(
        self,
        action: str,
        target: str | None = None,
        tool_name: str | None = None,
        answer: str | None = None,
        plan_text: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "action": action,
            "payload": payload or {},
        }
        if target is not None:
            body["target"] = target
        if tool_name is not None:
            body["tool_name"] = tool_name
        if answer is not None:
            body["answer"] = answer
        if plan_text is not None:
            body["plan_text"] = plan_text

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/step", json=body)
            response.raise_for_status()
            return response.json()

    async def state(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/state")
            response.raise_for_status()
            return response.json()
