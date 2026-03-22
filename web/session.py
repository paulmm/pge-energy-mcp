"""Simple in-memory session store for uploaded interval data.

Sessions are keyed by UUID, stored in a cookie, and auto-expire after 1 hour.
No database needed -- data lives only in server memory.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

_SESSION_TTL = 3600  # 1 hour

_store: dict[str, dict[str, Any]] = {}


def create_session() -> str:
    """Create a new session and return its ID."""
    sid = str(uuid.uuid4())
    _store[sid] = {"created_at": time.time(), "data": {}}
    return sid


def get_session(sid: str) -> dict[str, Any] | None:
    """Return session data dict, or None if expired/missing."""
    entry = _store.get(sid)
    if entry is None:
        return None
    if time.time() - entry["created_at"] > _SESSION_TTL:
        _store.pop(sid, None)
        return None
    return entry["data"]


def set_session_data(sid: str, key: str, value: Any) -> None:
    """Store a value in the session."""
    entry = _store.get(sid)
    if entry is None:
        return
    entry["data"][key] = value


def cleanup_expired() -> int:
    """Remove expired sessions. Returns count removed."""
    now = time.time()
    expired = [k for k, v in _store.items() if now - v["created_at"] > _SESSION_TTL]
    for k in expired:
        del _store[k]
    return len(expired)
