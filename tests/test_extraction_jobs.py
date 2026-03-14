"""Tests for the in-memory extraction job tracker and the job-status API endpoint."""

from __future__ import annotations

import time

import pytest

from litexplorer.models.extraction import ExtractionSchema
from litexplorer.models.project import Project
from litexplorer.services.extraction_jobs import _ExtractionJobRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry():
    """Return a fresh (empty) _ExtractionJobRegistry for each test."""
    return _ExtractionJobRegistry()


@pytest.fixture()
def project(db_session):
    p = Project(name="Test Project")
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture()
def schema(db_session, project):
    s = ExtractionSchema(title="Test Schema", project_id=project.id)
    db_session.add(s)
    db_session.commit()
    return s


# ---------------------------------------------------------------------------
# _ExtractionJobRegistry unit tests
# ---------------------------------------------------------------------------


def test_create_job_returns_string(registry):
    job_id = registry.create_job(schema_id=1, work_ids=[10, 20])
    assert isinstance(job_id, str)
    assert len(job_id) > 0


def test_create_job_uuid4_format(registry):
    import uuid
    job_id = registry.create_job(schema_id=1, work_ids=[1])
    # Should be a valid UUID4.
    parsed = uuid.UUID(job_id, version=4)
    assert str(parsed) == job_id


def test_create_job_initial_status_running(registry):
    job_id = registry.create_job(schema_id=5, work_ids=[1, 2, 3])
    job = registry.get_job(job_id)
    assert job["status"] == "running"


def test_create_job_work_statuses_start_pending(registry):
    job_id = registry.create_job(schema_id=1, work_ids=[10, 20, 30])
    job = registry.get_job(job_id)
    assert job["works"] == {"10": {"status": "pending"}, "20": {"status": "pending"}, "30": {"status": "pending"}}


def test_create_job_stores_schema_id(registry):
    job_id = registry.create_job(schema_id=42, work_ids=[1])
    assert registry.get_job(job_id)["schema_id"] == 42


def test_create_job_empty_work_ids(registry):
    job_id = registry.create_job(schema_id=1, work_ids=[])
    job = registry.get_job(job_id)
    assert job["works"] == {}


def test_update_work_status_running(registry):
    job_id = registry.create_job(schema_id=1, work_ids=[5])
    registry.update_work_status(job_id, 5, "running")
    assert registry.get_job(job_id)["works"]["5"]["status"] == "running"


def test_update_work_status_done(registry):
    job_id = registry.create_job(schema_id=1, work_ids=[5])
    registry.update_work_status(job_id, 5, "done")
    assert registry.get_job(job_id)["works"]["5"] == {"status": "done"}


def test_update_work_status_failed_includes_error(registry):
    job_id = registry.create_job(schema_id=1, work_ids=[7])
    registry.update_work_status(job_id, 7, "failed", error="Parse error: bad JSON")
    entry = registry.get_job(job_id)["works"]["7"]
    assert entry["status"] == "failed"
    assert entry["error"] == "Parse error: bad JSON"


def test_update_work_status_done_no_error_key(registry):
    job_id = registry.create_job(schema_id=1, work_ids=[7])
    registry.update_work_status(job_id, 7, "done")
    entry = registry.get_job(job_id)["works"]["7"]
    assert "error" not in entry


def test_update_work_status_unknown_job_is_noop(registry):
    # Should not raise.
    registry.update_work_status("nonexistent-job-id", 1, "done")


def test_get_job_returns_none_for_unknown(registry):
    assert registry.get_job("does-not-exist") is None


def test_get_job_returns_copy(registry):
    """Mutating the returned dict must not affect registry state."""
    job_id = registry.create_job(schema_id=1, work_ids=[3])
    copy = registry.get_job(job_id)
    copy["status"] = "tampered"
    copy["works"]["3"]["status"] = "tampered"
    # Registry state is unchanged.
    fresh = registry.get_job(job_id)
    assert fresh["status"] == "running"
    assert fresh["works"]["3"]["status"] == "pending"


def test_mark_completed(registry):
    job_id = registry.create_job(schema_id=1, work_ids=[1, 2])
    registry.mark_completed(job_id)
    assert registry.get_job(job_id)["status"] == "completed"


def test_mark_completed_unknown_job_is_noop(registry):
    registry.mark_completed("nonexistent-job-id")  # Should not raise.


def test_multiple_independent_jobs(registry):
    id1 = registry.create_job(schema_id=1, work_ids=[1])
    id2 = registry.create_job(schema_id=2, work_ids=[2, 3])
    registry.update_work_status(id1, 1, "done")
    registry.mark_completed(id1)
    # id2 should be unaffected.
    assert registry.get_job(id2)["status"] == "running"
    assert registry.get_job(id2)["works"]["2"]["status"] == "pending"


def test_stale_jobs_pruned_on_create(registry):
    """Jobs older than _STALE_SECONDS are removed when a new job is created."""
    # Inject a job with an artificially old created_at.
    job_id = registry.create_job(schema_id=1, work_ids=[1])
    with registry._mu:
        # Move created_at far into the past (2 hours ago).
        registry._jobs[job_id]["created_at"] = time.monotonic() - 7200

    # Creating a new job triggers pruning.
    registry.create_job(schema_id=2, work_ids=[2])
    assert registry.get_job(job_id) is None


def test_recent_jobs_not_pruned(registry):
    """Jobs within the stale window are NOT removed by pruning."""
    job_id = registry.create_job(schema_id=1, work_ids=[1])
    # Create another job to trigger pruning pass.
    registry.create_job(schema_id=2, work_ids=[2])
    assert registry.get_job(job_id) is not None


# ---------------------------------------------------------------------------
# Job-status API endpoint tests
# ---------------------------------------------------------------------------


def test_job_status_endpoint_returns_correct_shape(client, db_session, schema):
    """GET /api/extraction/jobs/{job_id} returns expected response shape."""
    from litexplorer.services.extraction_jobs import extraction_jobs

    job_id = extraction_jobs.create_job(schema.id, [1, 2, 3])
    extraction_jobs.update_work_status(job_id, 1, "done")
    extraction_jobs.update_work_status(job_id, 2, "failed", "some error")
    extraction_jobs.mark_completed(job_id)

    resp = client.get(f"/api/extraction/jobs/{job_id}")
    assert resp.status_code == 200
    data = resp.json()

    assert data["job_id"] == job_id
    assert data["schema_id"] == schema.id
    assert data["status"] == "completed"

    progress = data["progress"]
    assert progress["total"] == 3
    assert progress["completed"] == 1
    assert progress["failed"] == 1

    works = data["works"]
    assert works["1"]["status"] == "done"
    assert works["2"]["status"] == "failed"
    assert works["2"]["error"] == "some error"
    assert works["3"]["status"] == "pending"


def test_job_status_endpoint_404_unknown(client):
    """GET /api/extraction/jobs/{job_id} returns 404 for unknown job IDs."""
    resp = client.get("/api/extraction/jobs/does-not-exist")
    assert resp.status_code == 404


def test_job_status_progress_counts(client, db_session, schema):
    """Progress counts (completed/failed) computed correctly from work statuses."""
    from litexplorer.services.extraction_jobs import extraction_jobs

    job_id = extraction_jobs.create_job(schema.id, [10, 11, 12, 13, 14])
    extraction_jobs.update_work_status(job_id, 10, "done")
    extraction_jobs.update_work_status(job_id, 11, "done")
    extraction_jobs.update_work_status(job_id, 12, "failed", "err")
    extraction_jobs.update_work_status(job_id, 13, "running")
    # work 14 stays pending

    resp = client.get(f"/api/extraction/jobs/{job_id}")
    assert resp.status_code == 200
    progress = resp.json()["progress"]
    assert progress["total"] == 5
    assert progress["completed"] == 2
    assert progress["failed"] == 1


def test_job_status_running_job(client, db_session, schema):
    """A job that has not yet been mark_completed shows status 'running'."""
    from litexplorer.services.extraction_jobs import extraction_jobs

    job_id = extraction_jobs.create_job(schema.id, [1])
    resp = client.get(f"/api/extraction/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
