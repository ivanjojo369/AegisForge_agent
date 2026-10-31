"""Bounded A/B audience vote aggregator, gated by streamer-controlled rounds."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from threading import Lock
from typing import Any

_VOTE = re.compile(r"^!vote\s+([ab])\s*$", re.IGNORECASE)


class LiveVoteEngine:
    """One vote per chatter per active round; never persists personal IDs."""

    def __init__(self, *, simulated: bool = False) -> None:
        self._lock = Lock()
        self._by_chatter: dict[str, str] = {}
        self._seen: set[str] = set()
        self.simulated = simulated
        self._accepted = 0
        self._round = 0
        self._open = False
        self._started_at: datetime | None = None
        self._winner: str | None = None
        self._resolution = "idle"

    def start_round(self) -> dict[str, Any]:
        with self._lock:
            if self._open:
                raise ValueError("round_already_open")
            if self._resolution == "pending_approval":
                raise ValueError("resolve_pending_round_first")
            self._round += 1
            self._by_chatter.clear()
            self._accepted = 0
            self._open = True
            self._started_at = datetime.now(timezone.utc)
            self._winner = None
            self._resolution = "open"
            return self._snapshot()

    def close_round(self, *, commit: bool = True) -> dict[str, Any]:
        with self._lock:
            if not self._open:
                raise ValueError("round_not_open")
            a = sum(choice == "A" for choice in self._by_chatter.values())
            b = len(self._by_chatter) - a
            winner = "A" if a > b else "B" if b > a else None
            resolution = "pending_approval" if winner else "no_winner"
            if commit:
                self._open, self._winner, self._resolution = False, winner, resolution
            return {**self._snapshot(), "round_open": False,
                    "winner": winner, "resolution": resolution}

    def resolve_round(self, round_id: int, *, cancelled: bool = False) -> None:
        with self._lock:
            if type(round_id) is not int or round_id != self._round:
                raise ValueError("stale_round")
            if self._open or self._resolution != "pending_approval":
                raise ValueError("round_not_pending")
            self._resolution = "cancelled" if cancelled else "applied"

    def receive(self, message_id: str, chatter_id: str, text: str, *,
                sent_at: datetime | None = None, chat_message_id: str | None = None) -> bool:
        if any(type(v) is not str for v in (message_id, chatter_id, text)):
            return False
        if not (3 <= len(message_id) <= 128 and chatter_id.isascii() and chatter_id.isdigit() and
                1 <= len(chatter_id) <= 40 and len(text) <= 256):
            return False
        vote = _VOTE.fullmatch(text.strip())
        if vote is None:
            return False
        with self._lock:
            keys = {"event:" + message_id}
            if chat_message_id is not None:
                keys.add("chat:" + chat_message_id)
            if (not self._open or keys & self._seen or
                    (sent_at is not None and sent_at < self._started_at)):
                return False
            # Never evict an ID and silently count its replay. Fail closed at
            # this process-local memory limit; a new server session is required.
            if len(self._seen) + len(keys - self._seen) > 50000:
                return False
            self._seen.update(keys)
            choice = vote.group(1).upper()
            if self._by_chatter.get(chatter_id) == choice:
                return False
            if chatter_id not in self._by_chatter and len(self._by_chatter) >= 5000:
                return False
            self._by_chatter[chatter_id] = choice
            self._accepted += 1
            return True

    def _snapshot(self) -> dict[str, Any]:
        count_a = sum(choice == "A" for choice in self._by_chatter.values())
        count_b = len(self._by_chatter) - count_a
        return {"choice_a": count_a, "choice_b": count_b,
                "unique_voters": len(self._by_chatter),
                "accepted_vote_events": self._accepted,
                "method": "one_current_vote_per_chatter", "simulated": self.simulated,
                "round": self._round, "round_open": self._open,
                "winner": self._winner, "resolution": self._resolution,
                "dedup_capacity_reached": len(self._seen) >= 50000}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot()
