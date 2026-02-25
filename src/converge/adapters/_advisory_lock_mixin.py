"""PostgreSQL advisory lock mixin for distributed queue coordination.

Uses pg_try_advisory_lock / pg_advisory_unlock for lightweight,
session-scoped locking that doesn't require a dedicated table.
"""

from __future__ import annotations

import hashlib
import logging
import struct

log = logging.getLogger("converge.adapters.advisory_lock")


def _lock_id(lock_name: str) -> int:
    """Convert lock name to bigint for pg_advisory_lock."""
    h = hashlib.md5(lock_name.encode()).digest()
    return struct.unpack(">q", h[:8])[0]


class AdvisoryLockMixin:
    """PostgreSQL advisory lock implementation.

    Mixed into PostgresStore when the ``advisory_locks`` feature flag
    is set to ``enforce``.  Methods mirror the table-based LockMixin API.
    """

    def acquire_queue_lock_advisory(
        self, lock_name: str = "queue", holder_pid: int | None = None, ttl_seconds: int = 300,
    ) -> bool:
        lid = _lock_id(lock_name)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT pg_try_advisory_lock(%s)", (lid,),
            ).fetchone()
            return row[0] if row else False

    def release_queue_lock_advisory(
        self, lock_name: str = "queue", holder_pid: int | None = None,
    ) -> bool:
        lid = _lock_id(lock_name)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT pg_advisory_unlock(%s)", (lid,),
            ).fetchone()
            return row[0] if row else False

    def force_release_queue_lock_advisory(self, lock_name: str = "queue") -> bool:
        with self._connection() as conn:
            conn.execute("SELECT pg_advisory_unlock_all()")
            return True

    def get_queue_lock_info_advisory(self, lock_name: str = "queue") -> dict | None:
        lid = _lock_id(lock_name)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT pid, granted FROM pg_locks WHERE locktype='advisory' AND objid=%s",
                (lid & 0xFFFFFFFF,),
            ).fetchone()
        if row is None:
            return None
        return {
            "lock_name": lock_name,
            "holder_pid": row["pid"],
            "granted": row["granted"],
        }
