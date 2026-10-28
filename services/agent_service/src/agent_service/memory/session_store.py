"""Session memory: the user's questions and the query plan each was answered with.

By design it never holds query results, tool results or assistant replies. `SessionEntry`
accepts exactly a question and a plan, and a plan's typed steps hold only tool names and
filters, so there is nowhere for result data to go.
"""

import asyncio
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import Field

from shared.clock import Clock
from shared.contracts import QueryPlan
from shared.contracts.contract_model import ContractModel

MAX_QUESTION_LENGTH = 2000
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class SessionEntry(ContractModel):
    user_question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    query_plan: QueryPlan | None  # None when no search ran (e.g. the question was unclear)


class InvalidSessionIdError(ValueError):
    pass


def ensure_valid_session_id(session_id: str) -> None:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise InvalidSessionIdError(
            "session_id must be 1-128 characters of letters, digits, '-' or '_'"
        )


class SessionStore(Protocol):
    async def load_recent_entries(self, session_id: str) -> list[SessionEntry]:
        """Oldest first, at most the store's limit; empty for an unknown or expired session."""
        ...

    async def append_entry(self, session_id: str, entry: SessionEntry) -> None: ...

    async def delete_session(self, session_id: str) -> None: ...


@dataclass
class _Session:
    entries: deque[SessionEntry]
    last_used_at: datetime


class InMemorySessionStore:
    """Single-process session memory. Idle sessions expire; each keeps its last N entries."""

    def __init__(self, max_entries_per_session: int, idle_timeout: timedelta, clock: Clock) -> None:
        if max_entries_per_session < 1:
            raise ValueError("max_entries_per_session must be at least 1")
        self._max_entries_per_session = max_entries_per_session
        self._idle_timeout = idle_timeout
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self._lock = asyncio.Lock()

    async def load_recent_entries(self, session_id: str) -> list[SessionEntry]:
        ensure_valid_session_id(session_id)
        async with self._lock:
            self._remove_expired_sessions()
            session = self._sessions.get(session_id)
            if session is None:
                return []
            session.last_used_at = self._clock.now()
            return list(session.entries)

    async def append_entry(self, session_id: str, entry: SessionEntry) -> None:
        ensure_valid_session_id(session_id)
        if not isinstance(entry, SessionEntry):
            raise TypeError("only SessionEntry objects can be stored")
        async with self._lock:
            self._remove_expired_sessions()
            session = self._sessions.get(session_id)
            if session is None:
                session = _Session(
                    entries=deque(maxlen=self._max_entries_per_session),
                    last_used_at=self._clock.now(),
                )
                self._sessions[session_id] = session
            session.entries.append(entry)
            session.last_used_at = self._clock.now()

    async def delete_session(self, session_id: str) -> None:
        ensure_valid_session_id(session_id)
        async with self._lock:
            self._sessions.pop(session_id, None)

    def _remove_expired_sessions(self) -> None:
        expires_before = self._clock.now() - self._idle_timeout
        for session_id in [
            session_id
            for session_id, session in self._sessions.items()
            if session.last_used_at < expires_before
        ]:
            del self._sessions[session_id]
