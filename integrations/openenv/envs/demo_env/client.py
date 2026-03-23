from __future__ import annotations

from typing import Any

import httpx


class DemoEnvClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8011", timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return response.json()

    def reset(self, seed: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if seed is not None:
            payload["seed"] = seed

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/reset", json=payload)
            response.raise_for_status()
            return response.json()

    def step(self, action: str = "advance", value: int = 1) -> dict[str, Any]:
        payload = {
            "action": action,
            "value": value,
        }

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/step", json=payload)
            response.raise_for_status()
            return response.json()

    def state(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/state")
            response.raise_for_status()
            return response.json()


class AsyncDemoEnvClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8011", timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return response.json()

    async def reset(self, seed: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if seed is not None:
            payload["seed"] = seed

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/reset", json=payload)
            response.raise_for_status()
            return response.json()

    async def step(self, action: str = "advance", value: int = 1) -> dict[str, Any]:
        payload = {
            "action": action,
            "value": value,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/step", json=payload)
            response.raise_for_status()
            return response.json()

    async def state(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/state")
            response.raise_for_status()
            return response.json()
        