"""
In-memory session store.

A prototype-appropriate store: sessions live in memory only (no PII ever touches
disk), keyed by a random id held in a cookie. Each session owns its event log
(the source of truth), the OpenAI message history, and any generated PDFs (held
transiently behind a one-time token). Old sessions expire.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from .events import EventLog, SESSION_STARTED
from .observability import log_event
from .schemas import ReturnState

SESSION_TTL_SECONDS = 2 * 60 * 60  # 2 hours
MAX_SESSIONS = 500


@dataclass
class GeneratedPdf:
    filename: str
    content: bytes
    created_at: float


@dataclass
class Session:
    id: str
    log: EventLog = field(default_factory=EventLog)
    messages: list[dict] = field(default_factory=list)  # OpenAI chat history
    pdfs: dict[str, GeneratedPdf] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.last_active = time.time()

    def record(self, kind: str, summary: str, **data):
        ev = self.log.append(kind, summary, **data)
        log_event(self.id, ev)
        self.touch()
        return ev

    def state(self) -> ReturnState:
        return self.log.state()

    def add_pdf(self, content: bytes, filename: str) -> str:
        token = secrets.token_urlsafe(16)
        self.pdfs[token] = GeneratedPdf(filename=filename, content=content,
                                        created_at=time.time())
        return token

    def get_pdf(self, token: str) -> GeneratedPdf | None:
        return self.pdfs.get(token)


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def _evict_if_needed(self) -> None:
        now = time.time()
        expired = [sid for sid, s in self._sessions.items()
                   if now - s.last_active > SESSION_TTL_SECONDS]
        for sid in expired:
            del self._sessions[sid]
        if len(self._sessions) > MAX_SESSIONS:
            oldest = sorted(self._sessions.values(), key=lambda s: s.last_active)
            for s in oldest[: len(self._sessions) - MAX_SESSIONS]:
                self._sessions.pop(s.id, None)

    def create(self) -> Session:
        self._evict_if_needed()
        sid = secrets.token_urlsafe(18)
        session = Session(id=sid)
        session.record(SESSION_STARTED, "New filing session started.")
        self._sessions[sid] = session
        return session

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        return self._sessions.get(sid)

    def get_or_create(self, sid: str | None) -> Session:
        session = self.get(sid)
        if session is None:
            session = self.create()
        return session


# Module-level singleton store.
STORE = SessionStore()
