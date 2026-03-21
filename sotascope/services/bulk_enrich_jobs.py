"""In-memory tracker for bulk enrichment jobs (S2 batch fetch, GROBID batch extract).

Jobs are kept for at most _STALE_SECONDS (1 hour) and pruned on each
:func:`create_job` call.  Thread-safe via a single ``threading.Lock``.
Suitable for single-worker uvicorn deployments.

Job lifecycle:
  running → completed   (all works processed)
  running → cancelled   (cancel_job() called while running)
  running → rate_limited (Semantic Scholar returned HTTP 429)
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_STALE_SECONDS = 3600  # 1 hour


@dataclass
class BulkEnrichJob:
    job_id: str
    source: str          # "semantic_scholar" | "grobid"
    work_ids: list[int]
    done: int = 0        # seeds fully processed (skipped or enriched)
    total: int = 0       # total seeds submitted
    status: str = "running"           # running | completed | cancelled | rate_limited
    rate_limited_at: int | None = None  # done count when 429 was hit
    cancel_requested: bool = False
    errors: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "source": self.source,
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "rate_limited_at": self.rate_limited_at,
            "errors": list(self.errors),
        }


_jobs: dict[str, BulkEnrichJob] = {}
_lock = threading.Lock()


def create_job(source: str, work_ids: list[int]) -> BulkEnrichJob:
    """Create a new bulk enrichment job and return it."""
    _prune_stale()
    job_id = str(uuid.uuid4())
    job = BulkEnrichJob(
        job_id=job_id,
        source=source,
        work_ids=list(work_ids),
        total=len(work_ids),
    )
    with _lock:
        _jobs[job_id] = job
    return job


def get_job(job_id: str) -> BulkEnrichJob | None:
    """Return the job or None if not found."""
    with _lock:
        return _jobs.get(job_id)


def cancel_job(job_id: str) -> bool:
    """Request cancellation of a running job. Returns True if the request was accepted."""
    with _lock:
        job = _jobs.get(job_id)
        if job and job.status == "running":
            job.cancel_requested = True
            return True
        return False


def _prune_stale() -> None:
    now = time.monotonic()
    with _lock:
        stale = [
            jid for jid, j in _jobs.items()
            if now - j.created_at > _STALE_SECONDS
        ]
        for jid in stale:
            logger.debug("Pruning stale bulk enrich job %s", jid)
            del _jobs[jid]
