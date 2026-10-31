"""Authorized, ephemeral Twitch device sign-in for the local Live Forge server.

Launch with: python -m aegisforge.products.live_forge.twitch_login
Requires TWITCH_CLIENT_ID for a registered Twitch PUBLIC application.
Requests only user:read:chat. No token is logged or persisted.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

_DEVICE_URL = "https://id.twitch.tv/oauth2/device"
_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
_SCOPE = "user:read:chat"


def _require_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("unexpected_twitch_response")
    return value


def _check_activation_url(value: Any) -> str:
    if not isinstance(value, str):
        raise RuntimeError("invalid_twitch_activation_url")
    url = urlparse(value)
    if url.scheme != "https" or url.hostname not in {"www.twitch.tv", "twitch.tv"} or url.path != "/activate":
        raise RuntimeError("invalid_twitch_activation_url")
    return value


async def authorize(client_id: str) -> tuple[str, str]:
    """Return (user_access_token, user_id) exclusively through official Twitch OAuth."""
    if not re.fullmatch(r"[a-zA-Z0-9]{8,80}", client_id):
        raise ValueError("invalid_twitch_client_id")

    async with httpx.AsyncClient(timeout=12, follow_redirects=False) as http:
        response = await http.post(_DEVICE_URL, data={
            "client_id": client_id, "scopes": _SCOPE,
        })
        if response.status_code != 200:
            raise RuntimeError(f"twitch_device_authorization_http_{response.status_code}")
        device = _require_mapping(response.json())
        activation = _check_activation_url(device.get("verification_uri"))
        user_code = device.get("user_code")
        device_code = device.get("device_code")
        expires = device.get("expires_in")
        interval = device.get("interval")
        if (not isinstance(user_code, str) or not (4 <= len(user_code) <= 30)
                or not isinstance(device_code, str) or not (12 <= len(device_code) <= 256)
                or type(expires) is not int or not (60 <= expires <= 3600)
                or type(interval) is not int or not (1 <= interval <= 60)):
            raise RuntimeError("invalid_twitch_device_response")

        print("\nAbre la autorización OFICIAL de Twitch en tu navegador:")
        print(activation)
        print("Código temporal:", user_code)
        print("Permiso solicitado: lectura de chat (user:read:chat).")
        print("Autoriza solo si eres la persona titular de la cuenta y aceptas ese permiso.")
        print("Esperando autorización; no compartas tokens de acceso.\n", flush=True)

        deadline = time.monotonic() + expires
        delay = max(5, interval)
        while time.monotonic() + delay < deadline:
            await asyncio.sleep(delay)
            response = await http.post(_TOKEN_URL, data={
                "client_id": client_id,
                "scopes": _SCOPE,
                "device_code": device_code,
                "grant_type": _GRANT_TYPE,
            })
            if response.status_code == 200:
                granted = _require_mapping(response.json())
                token = granted.get("access_token")
                if not isinstance(token, str) or len(token) < 12:
                    raise RuntimeError("missing_twitch_access_token")

                verification = await http.get(_VALIDATE_URL, headers={
                    "Authorization": "OAuth " + token,
                })
                if verification.status_code != 200:
                    raise RuntimeError("twitch_token_validation_failed")
                identity = _require_mapping(verification.json())
                user_id = identity.get("user_id")
                scopes = identity.get("scopes")
                if (identity.get("client_id") != client_id or
                        not isinstance(user_id, str) or not user_id.isdigit() or
                        not isinstance(scopes, list) or _SCOPE not in scopes):
                    raise RuntimeError("twitch_identity_or_scope_mismatch")
                return token, user_id

            if response.status_code == 429:
                delay = min(60, max(delay + 5, delay * 2))
                continue
            if response.status_code != 400:
                raise RuntimeError(f"twitch_device_token_http_{response.status_code}")
            body = _require_mapping(response.json())
            reason = str(body.get("message", "")).lower()
            if reason == "authorization_pending":
                continue
            if reason == "slow_down":
                delay = min(60, delay + 5)
                continue
            if reason in {"access_denied", "authorization_denied"}:
                raise RuntimeError("twitch_authorization_declined")
            if reason in {"expired_token", "invalid device code", "invalid_grant"}:
                raise RuntimeError("twitch_authorization_expired")
            raise RuntimeError("twitch_authorization_rejected")
        raise RuntimeError("twitch_authorization_timed_out")


def main() -> None:
    import uvicorn

    client_id = os.getenv("TWITCH_CLIENT_ID", "").strip()
    if not client_id:
        raise SystemExit("Define TWITCH_CLIENT_ID en esta terminal con el ID público de tu aplicación Twitch.")
    if os.getenv("TWITCH_USER_ACCESS_TOKEN") or os.getenv("TWITCH_BROADCASTER_ID") or os.getenv("TWITCH_USER_ID"):
        raise SystemExit("El inicio por dispositivo requiere un entorno limpio: elimina las variables TWITCH_USER_* y TWITCH_BROADCASTER_ID de esta terminal.")

    try:
        token, user_id = asyncio.run(authorize(client_id))
    except KeyboardInterrupt:
        raise SystemExit("Autorización cancelada.") from None
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        # Never reveal response bodies, tokens, or device codes in errors.
        safe = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else "twitch_network_unavailable"
        raise SystemExit(f"No se inició Live Forge: {safe}") from None

    os.environ["TWITCH_USER_ACCESS_TOKEN"] = token
    os.environ["TWITCH_USER_ID"] = user_id
    os.environ["TWITCH_BROADCASTER_ID"] = user_id  # authorized account's own channel only
    from . import server
    print("Autorización aceptada para el canal de la cuenta autenticada.")
    print("LIVE FORGE — token de operador local (no compartir):", server.operator_token, flush=True)
    print("Abre http://127.0.0.1:8013/ y verifica el estado 'connected_to_twitch'.", flush=True)
    try:
        uvicorn.run(server.app, host="127.0.0.1", port=8013, log_level="warning")
    finally:
        for key in ("TWITCH_USER_ACCESS_TOKEN", "TWITCH_USER_ID", "TWITCH_BROADCASTER_ID"):
            os.environ.pop(key, None)


if __name__ == "__main__":
    main()
