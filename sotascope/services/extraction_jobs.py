"""In-memory tracker for LLM extraction batch jobs.

Each job covers one batch extraction request (one or many works for one schema).
Jobs are kept for at most _STALE_SECONDS (1 hour); older entries are pruned on
each :func:`create_job` call.

Thread-safe via a single ``threading.Lock``.  Suitable for single-worker
uvicorn deployments (the standard setup for this application).
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# Jobs older than this are pruned from memory on the next create_job call.
_STALE_SECONDS = 3600  # 1 hour


class _ExtractionJobRegistry:
    """In-memory registry tracking per-work progress for extraction jobs."""

    def __init__(self) -> None:
        self._mu = threading.Lock()
        # job_id -> job dict (status, works, schema_id, created_at)
        self._jobs: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_job(self, schema_id: int, work_ids: list[int]) -> str:
        """Create a new extraction job and return its ``job_id`` (uuid4 string).

        All work IDs start with status ``"pending"``.  Stale jobs older than
        :data:`_STALE_SECONDS` are pruned before the new job is registered.
        """
        self._prune_stale()
        job_id = str(uuid.uuid4())
        with self._mu:
            self._jobs[job_id] = {
                "job_id": job_id,
                "schema_id": schema_id,
                "status": "running",
                "works": {str(wid): {"status": "pending"} for wid in work_ids},
                "created_at": time.monotonic(),
            }
        return job_id

    def update_work_status(
        self,
        job_id: str,
        work_id: int,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        """Update the per-work status within a job.

        ``status`` should be one of ``"pending"``, ``"running"``, ``"done"``,
        or ``"failed"``.  If ``error`` is provided it is included in the work
        entry (only meaningful for ``"failed"`` status).

        A no-op if ``job_id`` is not found.
        """
        with self._mu:
            if job_id not in self._jobs:
                return
            entry: dict[str, str] = {"status": status}
            if error is not None:
                entry["error"] = error
            self._jobs[job_id]["works"][str(work_id)] = entry

    def get_job(self, job_id: str) -> Optional[dict]:
        """Return a shallow copy of the job state dict, or ``None`` if not found."""
        with self._mu:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            # Return a copy so callers can't accidentally mutate registry state.
            copy = dict(job)
            copy["works"] = {k: dict(v) for k, v in job["works"].items()}
            return copy

    def mark_completed(self, job_id: str) -> None:
        """Mark the overall job status as ``"completed"``."""
        with self._mu:
            if job_id in self._jobs:
                self._jobs[job_id]["status"] = "completed"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prune_stale(self) -> None:
        """Remove jobs whose ``created_at`` is older than :data:`_STALE_SECONDS`."""
        now = time.monotonic()
        with self._mu:
            stale = [
                jid
                for jid, job in self._jobs.items()
                if now - job["created_at"] > _STALE_SECONDS
            ]
            for jid in stale:
                logger.debug("Pruning stale extraction job %s", jid)
                del self._jobs[jid]


# Module-level singleton — shared across all requests in the same process.
extraction_jobs = _ExtractionJobRegistry()
