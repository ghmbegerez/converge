"""Event chain tamper-evidence (AR-44).

Provides cryptographic integrity for the event log via a hash chain.
Each event's hash includes the previous event's hash, creating a tamper-evident
chain. Verification detects any gap, mutation, or insertion in the chain.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from converge import event_log
from converge.defaults import QUERY_LIMIT_UNBOUNDED
from converge.event_types import EventType
from converge.models import Event

_GENESIS_HASH = "0" * 64  # SHA-256 of empty chain


def compute_event_hash(event_dict: dict[str, Any], prev_hash: str) -> str:
    """Compute SHA-256 hash of an event chained to the previous hash.

    The hash covers: prev_hash + event_id + timestamp + event_type + payload.
    """
    canonical = (
        f"{prev_hash}|{event_dict['id']}|{event_dict['timestamp']}"
        f"|{event_dict['event_type']}|{json.dumps(event_dict['payload'], sort_keys=True)}"
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def initialize_chain() -> dict[str, Any]:
    """Initialize or re-initialize the hash chain from existing events.

    Walks all events in chronological order, computes the chain hash for each,
    and stores the final chain state. Safe to call on an existing chain.
    """
    events = _get_all_events_chronological()

    prev_hash = _GENESIS_HASH
    for evt in events:
        prev_hash = compute_event_hash(evt, prev_hash)

    # Persist chain state
    _save_chain_state(prev_hash, len(events))

    event_log.append(Event(
        event_type=EventType.CHAIN_INITIALIZED,
        payload={
            "event_count": len(events),
            "chain_hash": prev_hash,
        },
    ))

    return {
        "initialized": True,
        "event_count": len(events),
        "chain_hash": prev_hash,
    }


def verify_chain() -> dict[str, Any]:
    """Verify the integrity of the event chain.

    Walks all events in chronological order, recomputes hashes, and compares
    against the stored chain state. Returns verification result.
    """
    events = _get_all_events_chronological()
    stored_state = _get_chain_state()

    prev_hash = _GENESIS_HASH
    for i, evt in enumerate(events):
        prev_hash = compute_event_hash(evt, prev_hash)

    if stored_state is None:
        # No chain state exists — chain has never been initialized
        result = {
            "valid": False,
            "reason": "chain not initialized",
            "event_count": len(events),
            "computed_hash": prev_hash,
        }
    elif stored_state["event_count"] != len(events):
        result = {
            "valid": False,
            "reason": f"event count mismatch: stored={stored_state['event_count']}, actual={len(events)}",
            "event_count": len(events),
            "stored_count": stored_state["event_count"],
            "computed_hash": prev_hash,
            "stored_hash": stored_state["last_hash"],
        }
    elif stored_state["last_hash"] != prev_hash:
        result = {
            "valid": False,
            "reason": "hash mismatch — chain tampered",
            "event_count": len(events),
            "computed_hash": prev_hash,
            "stored_hash": stored_state["last_hash"],
        }
    else:
        result = {
            "valid": True,
            "event_count": len(events),
            "chain_hash": prev_hash,
        }

    # Emit verification event
    event_type = EventType.CHAIN_VERIFIED if result["valid"] else EventType.CHAIN_TAMPER_DETECTED
    event_log.append(Event(
        event_type=event_type,
        payload=result,
    ))

    return result


def get_chain_state() -> dict[str, Any] | None:
    """Get the current chain state (public API)."""
    return _get_chain_state()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_all_events_chronological() -> list[dict[str, Any]]:
    """Get all events ordered by timestamp ASC (oldest first)."""
    # Use a large limit and reverse since query returns DESC
    events = event_log.query(limit=QUERY_LIMIT_UNBOUNDED)
    events.reverse()
    return events


def _get_chain_state() -> dict[str, Any] | None:
    """Read chain state from the database."""
    return event_log.get_chain_state()


def _save_chain_state(last_hash: str, event_count: int) -> None:
    """Write chain state to the database."""
    event_log.save_chain_state("main", last_hash, event_count)
