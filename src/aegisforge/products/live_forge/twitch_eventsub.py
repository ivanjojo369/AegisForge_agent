"""Read-only Twitch EventSub WebSocket transport for authorized broadcasters.

This bridge never posts to chat, saves personal identifiers, or accepts votes
from HTTP clients. Twitch connection is only claimed after subscribing.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect

from .engine import LiveVoteEngine
from .eventsub_ingestion import TwitchChatIngestion

_TWITCH_WS = 'wss://eventsub.wss.twitch.tv/ws'
_VALIDATE = 'https://id.twitch.tv/oauth2/validate'
_EVENTSUB = 'https://api.twitch.tv/helix/eventsub/subscriptions'


@dataclass(frozen=True, slots=True)
class TwitchConfig:
    client_id: str
    user_token: str
    broadcaster_id: str
    user_id: str

    @classmethod
    def from_env(cls) -> 'TwitchConfig | None':
        keys = ('TWITCH_CLIENT_ID', 'TWITCH_USER_ACCESS_TOKEN',
                'TWITCH_BROADCASTER_ID', 'TWITCH_USER_ID')
        values = [os.getenv(k, '').strip() for k in keys]
        if not any(values):
            return None
        if not all(values):
            raise ValueError('incomplete_twitch_configuration')
        client_id, token, broadcaster_id, user_id = values
        if not (client_id.isalnum() and broadcaster_id.isdigit() and
                user_id.isdigit() and len(token) >= 12):
            raise ValueError('invalid_twitch_configuration')
        return cls(client_id, token, broadcaster_id, user_id)


class TwitchEventSubBridge:
    def __init__(self, engine: LiveVoteEngine, config: TwitchConfig):
        self.engine = engine
        self.config = config
        self.ingestion = TwitchChatIngestion(engine, config.broadcaster_id, config.user_id)
        self.connected = False
        self.status = 'starting'
        self.last_issue: str | None = None
        self.reconnections = 0

    def public_diagnostics(self) -> dict[str, Any]:
        return {**self.ingestion.summary(), 'reconnections': self.reconnections,
                'source': 'authenticated_twitch_websocket_only',
                'connected': self.connected}

    async def _validate_token(self, http: httpx.AsyncClient) -> None:
        response = await http.get(_VALIDATE, headers={'Authorization': 'OAuth ' + self.config.user_token})
        if response.status_code != 200:
            raise RuntimeError('invalid_or_expired_twitch_token')
        data: dict[str, Any] = response.json()
        if (data.get('client_id') != self.config.client_id or
                data.get('user_id') != self.config.user_id or
                'user:read:chat' not in data.get('scopes', [])):
            raise RuntimeError('twitch_token_identity_or_scope_mismatch')

    async def _subscribe(self, http: httpx.AsyncClient, session_id: str) -> None:
        body = {
            'type': 'channel.chat.message', 'version': '1',
            'condition': {'broadcaster_user_id': self.config.broadcaster_id,
                          'user_id': self.config.user_id},
            'transport': {'method': 'websocket', 'session_id': session_id},
        }
        response = await http.post(_EVENTSUB, json=body, headers={
            'Client-ID': self.config.client_id,
            'Authorization': 'Bearer ' + self.config.user_token,
        })
        if response.status_code != 202:
            raise RuntimeError(f'eventsub_subscription_http_{response.status_code}')

    @staticmethod
    def _reconnect_url(url: object) -> str:
        if type(url) is not str or len(url) > 2048:
            raise RuntimeError('invalid_reconnect_url')
        parts = urlsplit(url)
        if (parts.scheme != 'wss' or parts.hostname != 'eventsub.wss.twitch.tv' or
                parts.username is not None or parts.password is not None or
                parts.port not in (None, 443)):
            raise RuntimeError('invalid_reconnect_url')
        return url  # Required by Twitch: preserve the server-provided URL.

    @staticmethod
    async def _welcome(ws, *, previous_keepalive: int | None = None) -> tuple[str, int]:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=9)
            frame = json.loads(raw)
            if type(frame) is not dict or frame.get('metadata', {}).get('message_type') != 'session_welcome':
                raise ValueError('unexpected')
            session = frame.get('payload', {}).get('session', {})
            session_id = session.get('id')
            keepalive = session.get('keepalive_timeout_seconds')
            if keepalive is None:
                keepalive = previous_keepalive
            if (type(session_id) is not str or not 1 <= len(session_id) <= 128 or
                    type(keepalive) is not int or not 10 <= keepalive <= 600):
                raise ValueError('invalid')
            return session_id, keepalive
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise RuntimeError('invalid_eventsub_welcome') from exc

    async def _connection(self, http: httpx.AsyncClient) -> None:
        ws = await connect(_TWITCH_WS, open_timeout=12, max_size=256_000, ping_interval=20)
        try:
            session_id, keepalive = await self._welcome(ws)
            await self._subscribe(http, session_id)
            self.status = 'connected_to_twitch'
            self.connected = True
            self.last_issue = None
            while True:
                # Twitch keepalive messages protect against silent connections.
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=keepalive + 10)
                except asyncio.TimeoutError as exc:
                    raise RuntimeError('eventsub_keepalive_timeout') from exc
                try:
                    frame = json.loads(raw)
                except (ValueError, TypeError):
                    self.ingestion.invalid += 1
                    continue
                if type(frame) is not dict:
                    self.ingestion.invalid += 1
                    continue
                meta = frame.get('metadata')
                kind = meta.get('message_type') if type(meta) is dict else None
                if kind == 'notification':
                    self.ingestion.process(frame)
                elif kind == 'session_keepalive':
                    continue
                elif kind == 'revocation':
                    raise RuntimeError('twitch_subscription_revoked')
                elif kind == 'session_reconnect':
                    payload = frame.get('payload')
                    session = payload.get('session') if type(payload) is dict else None
                    if type(session) is not dict:
                        raise RuntimeError('invalid_reconnect_payload')
                    url = self._reconnect_url(session.get('reconnect_url'))
                    new_ws = await connect(url, open_timeout=12, max_size=256_000, ping_interval=20)
                    try:
                        _, new_keepalive = await self._welcome(new_ws, previous_keepalive=keepalive)
                    except BaseException:
                        await new_ws.close()
                        raise
                    # Reconnect handoff: Twitch transfers subscriptions, so do NOT resubscribe.
                    old_ws, ws = ws, new_ws
                    keepalive = new_keepalive
                    self.reconnections += 1
                    await old_ws.close()
                # Other message types do not result in votes.
        finally:
            self.connected = False
            await ws.close()

    async def run(self) -> None:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as http:
            try:
                await self._validate_token(http)
            except Exception as exc:
                self.connected = False
                self.status = 'authentication_failed'
                self.last_issue = (str(exc) if isinstance(exc, RuntimeError)
                                   else 'twitch_authentication_unavailable')[:100]
                return
            while True:
                self.status = 'connecting'
                self.connected = False
                try:
                    await self._connection(http)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Fixed error codes only: never remote response bodies or tokens.
                    code = str(exc) if isinstance(exc, RuntimeError) else 'twitch_connection_error'
                    self.last_issue = code[:100]
                    if code == 'twitch_subscription_revoked':
                        self.status = 'subscription_revoked'
                        return
                self.connected = False
                self.status = 'retrying'
                self.reconnections += 1
                await asyncio.sleep(5)
