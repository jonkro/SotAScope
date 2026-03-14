"""Tests for the GROBID enrichment endpoint (POST /api/enrich/works/{id}/grobid)
and the health-check endpoint (GET /api/grobid/status).

Mock strategy
-------------
* ``litexplorer.external.grobid.GrobidClient`` is patched per-test with a
  mock class whose constructor returns a mock instance.  Because the service
  imports the class with ``from litexplorer.external.grobid import GrobidClient``
  *inside* the function, patching the attribute on the source module is the
  correct approach.
* ``pathlib.Path.exists`` and ``pathlib.Path.read_bytes`` are patched to avoid
  needing real PDF files on disk.
* OpenAlex and Crossref clients are injected via the existing ``_get_client``
  and ``_get_crossref_client`` factory-function patches (same pattern as
  ``test_enrichment_api.py`` and ``test_semantic_scholar_enrichment.py``).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from litexplorer.api.deps import get_db
from litexplorer.external.grobid import GrobidReference
from litexplorer.models.base import Base
from litexplorer.models.library import Citation, Work, WorkPDF
from litexplorer.models.settings import Setting
from litexplorer.schemas.enrichment import SearchImportCandidate
from tests.fixtures.openalex_responses import SAMPLE_WORK_RAW


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def mock_oa_client():
    """Mock OpenAlex client; returns nothing by default."""
    mock = MagicMock()
    mock.get_work_by_doi_raw.return_value = None
    mock.get_work_by_id_raw.return_value = None
    return mock


@pytest.fixture()
def mock_cr_client():
    """Mock Crossref client; returns nothing by default."""
    mock = MagicMock()
    mock.get_work_by_doi_raw.return_value = None
    return mock


@pytest.fixture()
def client(db_session, mock_oa_client, mock_cr_client):
    """FastAPI TestClient with in-memory DB + mocked OA/Crossref clients."""
    from litexplorer.app import app

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db

    with (
        patch("litexplorer.api.enrichment._get_client") as mock_get_oa,
        patch("litexplorer.api.enrichment._get_crossref_client") as mock_get_cr,
    ):
        mock_get_oa.return_value = mock_oa_client
        mock_get_cr.return_value = mock_cr_client
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_work(db_session, **kwargs) -> Work:
    """Create and persist a minimal Work record."""
    defaults = {"title": "Test Paper", "citation_count": 0}
    defaults.update(kwargs)
    work = Work(**defaults)
    db_session.add(work)
    db_session.commit()
    db_session.refresh(work)
    return work


def _make_work_pdf(
    db_session,
    work_id: int,
    filename: str = "paper.pdf",
    is_primary: bool = True,
) -> WorkPDF:
    """Attach a WorkPDF record to *work_id*."""
    pdf = WorkPDF(work_id=work_id, filename=filename, is_primary=is_primary)
    db_session.add(pdf)
    db_session.commit()
    db_session.refresh(pdf)
    return pdf


def _seed_setting(db_session, key: str, value: str) -> None:
    """Insert or update a Setting row."""
    existing = db_session.execute(
        select(Setting).where(Setting.key == key)
    ).scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db_session.add(Setting(key=key, value=value))
    db_session.commit()


def _make_grobid_ref(**kwargs) -> GrobidReference:
    """Build a GrobidReference with sensible defaults for unspecified fields."""
    defaults = dict(
        title=None,
        authors=[],
        doi=None,
        arxiv_id=None,
        journal=None,
        volume=None,
        pages=None,
        year=None,
        raw_string=None,
    )
    defaults.update(kwargs)
    return GrobidReference(**defaults)


def _mock_grobid_cls(references: list) -> MagicMock:
    """Return a mock GrobidClient *class* whose instances return *references*."""
    instance = MagicMock()
    instance.extract_references.return_value = references
    return MagicMock(return_value=instance)


# ---------------------------------------------------------------------------
# POST /api/enrich/works/{id}/grobid — happy paths
# ---------------------------------------------------------------------------


def test_grobid_enrich_with_doi_reference(db_session, client, mock_oa_client):
    """GROBID enrichment is accepted and scheduled (returns 202 immediately).

    The actual reference extraction and import happens in the background task.
    """
    work = _make_work(db_session, title="Seed Paper")
    _make_work_pdf(db_session, work.id)
    _seed_setting(db_session, "grobid_url", "http://localhost:8070")

    refs = [_make_grobid_ref(doi="10.1145/3230543.3230563", title="Referenced Work")]
    mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW

    with (
        patch("litexplorer.external.grobid.GrobidClient", _mock_grobid_cls(refs)),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_bytes", return_value=b"fake pdf bytes"),
    ):
        resp = client.post(f"/api/enrich/works/{work.id}/grobid")

    assert resp.status_code == 202, resp.text
    assert resp.json()["work_id"] == work.id


def test_grobid_enrich_with_arxiv_reference(db_session, client):
    """GROBID enrichment with an arXiv reference is accepted and returns 202."""
    seed_work = _make_work(db_session, title="Seed Paper")
    _make_work_pdf(db_session, seed_work.id)
    _seed_setting(db_session, "grobid_url", "http://localhost:8070")

    _make_work(db_session, title="ArXiv Preprint", arxiv_id="1234.5678")
    refs = [_make_grobid_ref(arxiv_id="1234.5678")]

    with (
        patch("litexplorer.external.grobid.GrobidClient", _mock_grobid_cls(refs)),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_bytes", return_value=b"fake pdf bytes"),
    ):
        resp = client.post(f"/api/enrich/works/{seed_work.id}/grobid")

    assert resp.status_code == 202, resp.text
    assert resp.json()["work_id"] == seed_work.id


def test_grobid_enrich_title_only(db_session, client, mock_oa_client):
    """Title-only GROBID reference: endpoint accepts the request and returns 202."""
    work = _make_work(db_session, title="Seed Paper")
    _make_work_pdf(db_session, work.id)
    _seed_setting(db_session, "grobid_url", "http://localhost:8070")

    ref_title = "Deep Residual Learning for Image Recognition"
    refs = [_make_grobid_ref(title=ref_title, authors=["He Kaiming"], year="2016")]

    candidate = SearchImportCandidate(
        title=ref_title,
        authors=["He Kaiming"],
        year=2016,
        doi="10.1109/cvpr.2016.90",
        venue="CVPR",
        semantic_scholar_id=None,
        source="crossref",
        score=95.0,
    )
    mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW

    with (
        patch("litexplorer.external.grobid.GrobidClient", _mock_grobid_cls(refs)),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_bytes", return_value=b"fake pdf bytes"),
        patch(
            "litexplorer.services.enrichment.EnrichmentService.search_import_candidates",
            return_value=[candidate],
        ),
    ):
        resp = client.post(f"/api/enrich/works/{work.id}/grobid")

    assert resp.status_code == 202, resp.text
    assert resp.json()["work_id"] == work.id


def test_grobid_enrich_dedup(db_session, client):
    """GROBID enrichment with an already-known DOI: accepted and returns 202."""
    work = _make_work(db_session, title="Seed Paper")
    _make_work_pdf(db_session, work.id)
    _seed_setting(db_session, "grobid_url", "http://localhost:8070")

    _make_work(db_session, title="Already In Library", doi="10.1234/existing")
    refs = [_make_grobid_ref(doi="10.1234/existing", title="Already In Library")]

    with (
        patch("litexplorer.external.grobid.GrobidClient", _mock_grobid_cls(refs)),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_bytes", return_value=b"fake pdf bytes"),
    ):
        resp = client.post(f"/api/enrich/works/{work.id}/grobid")

    assert resp.status_code == 202, resp.text
    assert resp.json()["work_id"] == work.id


# ---------------------------------------------------------------------------
# POST /api/enrich/works/{id}/grobid — error paths
# ---------------------------------------------------------------------------


def test_grobid_enrich_no_pdf(db_session, client):
    """A work with no attached PDF returns 404."""
    work = _make_work(db_session, title="No PDF Paper")
    # Deliberately no WorkPDF record

    resp = client.post(f"/api/enrich/works/{work.id}/grobid")

    assert resp.status_code == 404
    assert "pdf" in resp.json()["detail"].lower()


def test_grobid_enrich_not_configured(db_session, client):
    """When grobid_url is not set in Settings, the endpoint returns 400."""
    work = _make_work(db_session, title="Seed Paper")
    _make_work_pdf(db_session, work.id)
    # No grobid_url setting → service raises ValueError("...not configured...")

    with (
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_bytes", return_value=b"fake pdf bytes"),
    ):
        resp = client.post(f"/api/enrich/works/{work.id}/grobid")

    assert resp.status_code == 400
    assert "grobid" in resp.json()["detail"].lower()


def test_grobid_enrich_failed_resolution(db_session, client):
    """Unresolvable reference: endpoint accepts the request and returns 202.

    Resolution failure is handled inside the background task and logged.
    """
    work = _make_work(db_session, title="Seed Paper")
    _make_work_pdf(db_session, work.id)
    _seed_setting(db_session, "grobid_url", "http://localhost:8070")

    refs = [_make_grobid_ref(title="xkcd qwerty zork nonsense unresolvable 2099")]

    with (
        patch("litexplorer.external.grobid.GrobidClient", _mock_grobid_cls(refs)),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_bytes", return_value=b"fake pdf bytes"),
        patch(
            "litexplorer.services.enrichment.EnrichmentService.search_import_candidates",
            return_value=[],
        ),
    ):
        resp = client.post(f"/api/enrich/works/{work.id}/grobid")

    assert resp.status_code == 202, resp.text
    assert resp.json()["work_id"] == work.id


# ---------------------------------------------------------------------------
# GET /api/grobid/status
# ---------------------------------------------------------------------------


def test_grobid_status_available(db_session, client):
    """When GROBID is reachable, available=True and the configured url is echoed."""
    _seed_setting(db_session, "grobid_url", "http://localhost:8070")

    mock_instance = MagicMock()
    mock_instance.check_health.return_value = True

    with patch("litexplorer.api.grobid.GrobidClient", MagicMock(return_value=mock_instance)):
        resp = client.get("/api/grobid/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["url"] == "http://localhost:8070"


def test_grobid_status_not_available(db_session, client):
    """When the GROBID service is unreachable, available=False."""
    _seed_setting(db_session, "grobid_url", "http://localhost:8070")

    mock_instance = MagicMock()
    mock_instance.check_health.return_value = False

    with patch("litexplorer.api.grobid.GrobidClient", MagicMock(return_value=mock_instance)):
        resp = client.get("/api/grobid/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert data["url"] == "http://localhost:8070"


def test_grobid_status_not_configured(client):
    """When grobid_url is absent from Settings, available=False and url=''."""
    # No grobid_url setting seeded — the endpoint should short-circuit immediately
    resp = client.get("/api/grobid/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert data["url"] == ""
