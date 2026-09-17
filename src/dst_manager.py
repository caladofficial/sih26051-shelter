"""Server-side dialogue state for the design assistant (roadmap Step 3.4).

The UI already keeps the live design and sends it back as `context_design` —
that makes every turn resolvable even if this store evaporates (a cold
serverless container, a browser refresh, a guest re-login). What this module
adds on top is the two things a client cannot reconstruct by itself:

  * an UNDO stack — "undo that" after a chain of edits, where each turn's
    exact applied patch is recorded alongside the design it produced;
  * a turn counter — so the answer can honestly say "this is edit 4 of this
    conversation" and tests can assert state actually persisted across turns.

In-memory and bounded (TTL + LRU): it is a convenience over the client state,
never the source of truth, so nothing here needs durability. Design copied in
spirit from the SIH dataset doc's `dst_manager.py` sketch, with the session
dict pruned to what the Shelter-Studio flow actually uses.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict

MAX_SESSIONS = 256
TTL_SECONDS = 2 * 60 * 60          # conversations idle for 2 h fall off
HISTORY_DEPTH = 10                  # roadmap: last 10 turns are undoable

_sessions: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()


def new_session_id() -> str:
    return uuid.uuid4().hex[:16]


def _prune_locked(now: float) -> None:
    dead = [sid for sid, s in _sessions.items() if now - s["seen"] > TTL_SECONDS]
    for sid in dead:
        _sessions.pop(sid, None)
    while len(_sessions) > MAX_SESSIONS:
        _sessions.popitem(last=False)          # oldest touched first


def get_session(session_id: str | None) -> dict | None:
    """Return the session or None; refreshes recency when found."""
    if not session_id:
        return None
    now = time.time()
    with _lock:
        _prune_locked(now)
        s = _sessions.get(session_id)
        if s:
            s["seen"] = now
            _sessions.move_to_end(session_id)
        return s


def open_session(session_id: str | None, seed_design: dict | None) -> str:
    """Create (or reuse) a session; returns the id the client should keep."""
    sid = session_id or new_session_id()
    now = time.time()
    with _lock:
        _prune_locked(now)
        s = _sessions.get(sid)
        if s is None:
            s = {"turn": 0, "history": [], "current_design": None,
                 "seen": now}
            _sessions[sid] = s
        else:
            s["seen"] = now
            _sessions.move_to_end(sid)
        # a client that re-opens its page knows the truth; adopt it once
        if seed_design and s["current_design"] is None:
            s["current_design"] = dict(seed_design)
        return sid


def record_turn(session_id: str | None, text: str, design: dict,
                applied: list[str]) -> int:
    """Push the pre-turn state onto the undo stack; return the turn number."""
    if not session_id:
        return 0
    with _lock:
        s = _sessions.get(session_id)
        if s is None:
            return 0
        if s["current_design"] is not None:
            s["history"].append({"design": dict(s["current_design"]),
                                 "undid_text": text, "applied": list(applied)})
            if len(s["history"]) > HISTORY_DEPTH:
                s["history"].pop(0)
        s["current_design"] = dict(design)
        s["turn"] += 1
        s["seen"] = time.time()
        return s["turn"]


def pop_undo(session_id: str | None) -> dict | None:
    """Return {"design", "undid_text"} to restore, or None if nothing to undo."""
    if not session_id:
        return None
    with _lock:
        s = _sessions.get(session_id)
        if not s or not s["history"]:
            return None
        entry = s["history"].pop()
        s["current_design"] = dict(entry["design"])
        s["turn"] += 1
        s["seen"] = time.time()
        return {"design": entry["design"], "undid_text": entry["undid_text"],
                "applied_was": entry.get("applied", [])}


def state(session_id: str | None) -> dict:
    """Snapshot for the response: turn count + how deep the undo stack is."""
    s = get_session(session_id)
    if not s:
        return {"session": None, "turn": 0, "undo_depth": 0}
    return {"session": session_id, "turn": s["turn"],
            "undo_depth": len(s["history"])}


def reset() -> None:
    """Test helper: forget everything."""
    with _lock:
        _sessions.clear()
