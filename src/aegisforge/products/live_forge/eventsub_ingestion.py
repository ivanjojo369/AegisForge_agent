"""Validate Twitch channel.chat.message notifications before forwarding A/B votes.

This module has no network access and does not expose HTTP vote injection endpoints.
It does not persist Twitch user identifiers, message bodies or credentials.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .engine import LiveVoteEngine

_VOTE = re.compile(r"^!vote\s+[ab]$", re.IGNORECASE)


class TwitchChatIngestion:
    """Single authorized broadcaster+reader boundary, no synthetic live source."""

    def __init__(self, engine: LiveVoteEngine, broadcaster_id: str, reader_id: str):
        self.engine = engine
        self.broadcaster_id = broadcaster_id
        self.reader_id = reader_id
        self.notifications_seen = 0
        self.votes_accepted = 0
        self.ignored = 0
        self.invalid = 0

    def summary(self) -> dict[str, int]:
        return {
            'notifications_seen': self.notifications_seen,
            'votes_accepted': self.votes_accepted,
            'ignored_or_duplicate': self.ignored,
            'invalid_notifications': self.invalid,
        }

    def process(self, frame: object) -> str:
        """Process a notification from the established Twitch WebSocket only.

        An offline caller may use this parser for a fixture, but cannot mark the
        application connected or cause an HTTP vote. Status is owned by the socket.
        """
        self.notifications_seen += 1
        if type(frame) is not dict:
            return self._invalid()
        metadata = frame.get('metadata')
        payload = frame.get('payload')
        if (type(metadata) is not dict or type(payload) is not dict or
                metadata.get('message_type') != 'notification'):
            return self._invalid()
        if (metadata.get('subscription_type') != 'channel.chat.message' or
                metadata.get('subscription_version') != '1'):
            return self._invalid()
        try:
            sent_at = datetime.fromisoformat(metadata['message_timestamp'].replace('Z', '+00:00'))
            if sent_at.tzinfo is None or not -60 <= (datetime.now(timezone.utc) - sent_at).total_seconds() <= 600:
                return self._invalid()
        except (KeyError, TypeError, ValueError, AttributeError):
            return self._invalid()
        message_id = metadata.get('message_id')
        subscription = payload.get('subscription')
        event = payload.get('event')
        if (type(message_id) is not str or not 3 <= len(message_id) <= 128 or
                type(subscription) is not dict or type(event) is not dict or
                subscription.get('type') != 'channel.chat.message' or
                subscription.get('version') != '1' or
                subscription.get('status') != 'enabled'):
            return self._invalid()
        condition = subscription.get('condition')
        if (type(condition) is not dict or
                condition.get('broadcaster_user_id') != self.broadcaster_id or
                condition.get('user_id') != self.reader_id or
                event.get('broadcaster_user_id') != self.broadcaster_id):
            return self._invalid()
        chatter_id = event.get('chatter_user_id')
        chat_message_id = event.get('message_id')
        message = event.get('message')
        if (type(chatter_id) is not str or not chatter_id.isascii() or
                not chatter_id.isdigit() or not 1 <= len(chatter_id) <= 40 or
                type(chat_message_id) is not str or not 3 <= len(chat_message_id) <= 128 or
                type(message) is not dict or type(message.get('text')) is not str):
            return self._invalid()
        text = message['text']
        if len(text) > 256 or not _VOTE.fullmatch(text.strip()):
            self.ignored += 1
            return 'ignored'
        if self.engine.receive(message_id, chatter_id, text, sent_at=sent_at,
                               chat_message_id=chat_message_id):
            self.votes_accepted += 1
            return 'accepted'
        self.ignored += 1
        return 'ignored'

    def _invalid(self) -> str:
        self.invalid += 1
        return 'invalid'
