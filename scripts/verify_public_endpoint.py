#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class FetchResult:
    url: str
    status: int
    body: bytes


def fetch(url: str, timeout: float) -> FetchResult:
    request = Request(url, headers={"User-Agent": "aegisforge-verify-public-endpoint/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return FetchResult(url=url, status=response.status, body=response.read())


def fetch_with_retries(url: str, timeout: float, retries: int, sleep_seconds: float) -> FetchResult:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return fetch(url, timeout=timeout)
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < retries:
                print(f"[verify_public_endpoint] waiting for {url} ({attempt}/{retries})...", file=sys.stderr)
                time.sleep(sleep_seconds)
    assert last_error is not None
    raise last_error


def parse_json_bytes(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"{label} did not return valid JSON: {exc}") from exc


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def build_urls(base_url: str) -> tuple[str, str]:
    normalized = normalize_base_url(base_url)
    return f"{normalized}/health", f"{normalized}/.well-known/agent-card.json"


def validate_health(data: Any) -> list[str]:
    warnings: list[str] = []
    if not isinstance(data, dict):
        raise SystemExit("/health must return a JSON object.")
    if "status" not in data:
        warnings.append("Health JSON does not include 'status'.")
    elif str(data["status"]).lower() not in {"ok", "healthy", "up"}:
        warnings.append(f"Health status is unusual: {data['status']!r}")
    return warnings


def validate_agent_card(data: Any, base_url: str, health_url: str) -> list[str]:
    warnings: list[str] = []

    if not isinstance(data, dict):
        raise SystemExit("Agent Card must return a JSON object.")

    expected_any = {"id", "name", "version", "url", "capabilities", "description"}
    if not any(key in data for key in expected_any):
        raise SystemExit(
            f"Agent Card is valid JSON but missing expected keys. Found keys: {sorted(data.keys())}"
        )

    advertised_url = data.get("url")
    if isinstance(advertised_url, str) and advertised_url.rstrip("/") != base_url.rstrip("/"):
        warnings.append(
            f"Agent Card 'url' does not match the base URL. "
            f"card={advertised_url!r} expected={base_url!r}"
        )

    advertised_health = data.get("health_url")
    if isinstance(advertised_health, str) and advertised_health != health_url:
        warnings.append(
            f"Agent Card 'health_url' does not match the health URL. "
            f"card={advertised_health!r} expected={health_url!r}"
        )

    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the public AegisForge endpoint.")
    parser.add_argument("--base-url", required=True, help="Base public URL, for example https://example.com")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout in seconds")
    parser.add_argument("--retries", type=int, default=5, help="Number of retries per endpoint")
    parser.add_argument("--sleep", type=float, default=2.0, help="Sleep time between retries")
    args = parser.parse_args()

    base_url = normalize_base_url(args.base_url)
    health_url, card_url = build_urls(base_url)

    print(f"[verify_public_endpoint] health_url={health_url}")
    print(f"[verify_public_endpoint] card_url={card_url}")

    health_result = fetch_with_retries(health_url, timeout=args.timeout, retries=args.retries, sleep_seconds=args.sleep)
    card_result = fetch_with_retries(card_url, timeout=args.timeout, retries=args.retries, sleep_seconds=args.sleep)

    if health_result.status != 200:
        raise SystemExit(f"/health returned unexpected status: {health_result.status}")
    if card_result.status != 200:
        raise SystemExit(f"Agent Card returned unexpected status: {card_result.status}")

    health_json = parse_json_bytes(health_result.body, "/health")
    card_json = parse_json_bytes(card_result.body, "Agent Card")

    warnings: list[str] = []
    warnings.extend(validate_health(health_json))
    warnings.extend(validate_agent_card(card_json, base_url=base_url, health_url=health_url))

    print("[verify_public_endpoint] /health JSON OK")
    print("[verify_public_endpoint] Agent Card JSON OK")

    if warnings:
        print("[verify_public_endpoint] warnings:")
        for item in warnings:
            print(f"  - {item}")

    print("[verify_public_endpoint] verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
