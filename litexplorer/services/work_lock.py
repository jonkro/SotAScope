"""In-memory registry tracking work IDs with in-flight background operations.

Thread-safe using a simple dict protected by a threading.Lock.  Suitable for
single-worker deployments (uvicorn --workers 1, the standard for SQLite).

Stale locks (held for more than _STALE_SECONDS) are auto-released on any
access so that a background task crash cannot permanently block a work ID.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_STALE_SECONDS = 600  # 10 minutes


class _WorkLockRegistry:
    """Registry mapping work IDs to in-progress task descriptions."""

    def __init__(self) -> None:
        self._mu = threading.Lock()
        # work_id -> (task_description, acquired_monotonic_time)
        self._locks: dict[int, tuple[str, float]] = {}

    def _is_stale(self, acquired_at: float) -> bool:
        return time.monotonic() - acquired_at > _STALE_SECONDS

    def acquire(self, work_id: int, task_description: str) -> bool:
        """Try to acquire the lock for *work_id*.

        Returns True on success.  Returns False if the work already has a
        non-stale lock (the caller should respond with HTTP 409).  Stale locks
        are silently released and the acquisition succeeds.
        """
        with self._mu:
            if work_id in self._locks:
                _, acquired_at = self._locks[work_id]
                if self._is_stale(acquired_at):
                    logger.warning("Releasing stale lock for work %d", work_id)
                    del self._locks[work_id]
                else:
                    return False
            self._locks[work_id] = (task_description, time.monotonic())
            return True

    def release(self, work_id: int) -> None:
        """Release the lock for *work_id* (no-op if not held)."""
        with self._mu:
            self._locks.pop(work_id, None)

    def is_locked(self, work_id: int) -> bool:
        """Return True if *work_id* has a non-stale lock."""
        with self._mu:
            if work_id not in self._locks:
                return False
            _, acquired_at = self._locks[work_id]
            if self._is_stale(acquired_at):
                logger.warning("Releasing stale lock for work %d (is_locked)", work_id)
                del self._locks[work_id]
                return False
            return True

    def get_status(self, work_id: int) -> dict | None:
        """Return {"locked": True, "task": "..."} or None if not locked."""
        with self._mu:
            if work_id not in self._locks:
                return None
            task, acquired_at = self._locks[work_id]
            if self._is_stale(acquired_at):
                logger.warning("Releasing stale lock for work %d (get_status)", work_id)
                del self._locks[work_id]
                return None
            return {"locked": True, "task": task}

    def get_all_locked(self) -> dict[int, str]:
        """Return {work_id: task_description} for all currently locked works."""
        with self._mu:
            stale = [
                wid
                for wid, (_, at) in self._locks.items()
                if self._is_stale(at)
            ]
            for wid in stale:
                logger.warning("Releasing stale lock for work %d (get_all_locked)", wid)
                del self._locks[wid]
            return {wid: task for wid, (task, _) in self._locks.items()}


# Module-level singleton — shared across all requests in the same process.
work_lock = _WorkLockRegistry()
