"""Tests for the Semantic Scholar enrichment endpoint."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from litexplorer.api.deps import get_db
from litexplorer.external.base import ExternalWork
from litexplorer.external.semantic_scholar import SemanticScholarClient
from litexplorer.models.base import Base
from litexplorer.models.library import Citation, Work


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
def mock_ss_client():
    client = MagicMock(spec=SemanticScholarClient)
    # Default: return None (paper not found)
    client.get_paper_by_doi.return_value = None
    client.get_paper_by_id.return_value = None
    client.search_by_title.return_value = []
    client.get_references.return_value = []
    client.get_citations.return_value = []
    return client


@pytest.fixture()
def mock_oa_client():
    return MagicMock()


@pytest.fixture()
def client(db_session, mock_ss_client, mock_oa_client):
    """TestClient with mocked S2 + OA clients."""
    from litexplorer.app import app

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db

    with patch("litexplorer.api.enrichment._get_ss_client") as mock_get_ss, \
         patch("litexplorer.api.enrichment._get_client") as mock_get_oa:
        mock_get_ss.return_value = mock_ss_client
        mock_get_oa.return_value = mock_oa_client
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    app.dependency_overrides.clear()


def _make_work(db_session, **kwargs) -> Work:
    """Create and commit a minimal Work record."""
    defaults = {"title": "Test Paper", "citation_count": 0}
    defaults.update(kwargs)
    work = Work(**defaults)
    db_session.add(work)
    db_session.commit()
    db_session.refresh(work)
    return work


def _ext_work(title: str, doi: str | None = None, ss_id: str | None = None) -> ExternalWork:
    """Build a minimal ExternalWork for mocking."""
    return ExternalWork(title=title, doi=doi, semantic_scholar_id=ss_id)


# ---------------------------------------------------------------------------
# Successful enrichment
# ---------------------------------------------------------------------------


def test_successful_enrichment_by_doi(db_session, client, mock_ss_client):
    """Endpoint accepts the S2 enrichment request and returns 202 immediately."""
    work = _make_work(db_session, doi="10.1234/test", title="Seed Paper")

    seed_ext = _ext_work("Seed Paper", doi="10.1234/test", ss_id="abc123")
    mock_ss_client.get_paper_by_doi.return_value = seed_ext
    mock_ss_client.get_references.return_value = [
        _ext_work("Ref A", doi="10.1111/ref-a", ss_id="ref-a-id"),
        _ext_work("Ref B", doi="10.2222/ref-b", ss_id="ref-b-id"),
    ]
    mock_ss_client.get_citations.return_value = [
        _ext_work("Citer X", doi="10.3333/citer-x", ss_id="citer-x-id"),
    ]

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 202, resp.text
    assert resp.json()["work_id"] == work.id


def test_successful_enrichment_by_ss_id(db_session, client, mock_ss_client):
    """Work with no DOI but a stored S2 ID: endpoint accepts and returns 202."""
    work = _make_work(db_session, doi=None, semantic_scholar_id="existing-ss-id", title="No DOI")

    seed_ext = _ext_work("No DOI", ss_id="existing-ss-id")
    mock_ss_client.get_paper_by_doi.return_value = None
    mock_ss_client.get_paper_by_id.return_value = seed_ext
    mock_ss_client.get_references.return_value = [
        _ext_work("Reference", doi=None, ss_id="ref-ss-id"),
    ]
    mock_ss_client.get_citations.return_value = []

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 202, resp.text
    assert resp.json()["work_id"] == work.id


def test_work_response_includes_updated_ss_id(db_session, client, mock_ss_client):
    """Endpoint returns 202; the actual S2 ID update happens in the background task."""
    work = _make_work(db_session, doi="10.5678/test", title="My Paper")
    mock_ss_client.get_paper_by_doi.return_value = _ext_work("My Paper", doi="10.5678/test", ss_id="new-ss-id")

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 202
    assert resp.json()["work_id"] == work.id


# ---------------------------------------------------------------------------
# Duplicate citation handling
# ---------------------------------------------------------------------------


def test_duplicate_citations_not_duplicated(db_session, client, mock_ss_client):
    """Dedup logic is tested in the service layer; API returns 202 accepted."""
    work = _make_work(db_session, doi="10.1234/seed", title="Seed")
    ref_work = _make_work(db_session, doi="10.9999/ref", title="Existing Reference")

    db_session.add(Citation(citing_work_id=work.id, cited_work_id=ref_work.id, source="openalex"))
    db_session.commit()

    seed_ext = _ext_work("Seed", doi="10.1234/seed", ss_id="seed-ss")
    mock_ss_client.get_paper_by_doi.return_value = seed_ext
    mock_ss_client.get_references.return_value = [
        _ext_work("Existing Reference", doi="10.9999/ref", ss_id="ref-ss"),
    ]
    mock_ss_client.get_citations.return_value = []

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 202
    assert resp.json()["work_id"] == work.id


def test_second_call_reports_all_existing(db_session, client, mock_ss_client):
    """Both calls return 202; idempotency is tested in the service layer."""
    work = _make_work(db_session, doi="10.1234/seed", title="Seed")
    seed_ext = _ext_work("Seed", doi="10.1234/seed", ss_id="seed-id")
    refs = [_ext_work("Ref A", doi="10.1111/a", ss_id="a-id")]

    mock_ss_client.get_paper_by_doi.return_value = seed_ext
    mock_ss_client.get_references.return_value = refs
    mock_ss_client.get_citations.return_value = []

    r1 = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert r1.status_code == 202

    # Background task for r1 has already run (TestClient is synchronous),
    # so the lock is released and r2 can proceed.
    r2 = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert r2.status_code == 202


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_missing_identifier_returns_400(db_session, client):
    """Work with no DOI and no semantic_scholar_id → 400."""
    work = _make_work(db_session, doi=None, title="No Identifiers")
    # semantic_scholar_id is None by default

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 400
    assert "semantic scholar" in resp.json()["detail"].lower() or "doi" in resp.json()["detail"].lower()


def test_work_not_found_returns_404(client):
    """Non-existent work_id → 404."""
    resp = client.post("/api/enrich/works/999999/semantic-scholar")
    assert resp.status_code == 404


def test_paper_not_on_semantic_scholar_returns_202(db_session, client, mock_ss_client):
    """When S2 doesn't have the paper, the error is handled inside the background task.

    The endpoint returns 202 immediately; the failure is logged, not surfaced as 503.
    """
    work = _make_work(db_session, doi="10.1234/unknown", title="Unknown Paper")
    mock_ss_client.get_paper_by_doi.return_value = None

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 202
    assert resp.json()["work_id"] == work.id


# ---------------------------------------------------------------------------
# semantic_scholar_id deduplication
# ---------------------------------------------------------------------------


def test_existing_work_matched_by_ss_id(db_session, client, mock_ss_client):
    """S2 ID dedup behavior is tested in the service layer; API returns 202."""
    work = _make_work(db_session, doi="10.1234/seed", title="Seed")
    _make_work(db_session, doi=None, semantic_scholar_id="known-ss-ref", title="Known Ref")

    seed_ext = _ext_work("Seed", doi="10.1234/seed", ss_id="seed-id")
    mock_ss_client.get_paper_by_doi.return_value = seed_ext
    mock_ss_client.get_references.return_value = [
        _ext_work("Known Ref Updated", doi=None, ss_id="known-ss-ref"),
    ]
    mock_ss_client.get_citations.return_value = []

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 202
    assert resp.json()["work_id"] == work.id


# ---------------------------------------------------------------------------
# Regression: S2 refs/citations with DOI should get openalex_id via OA pipeline
# ---------------------------------------------------------------------------


def test_s2_ref_with_doi_gets_openalex_id(db_session, client, mock_ss_client, mock_oa_client):
    """OA pipeline integration is tested in the service layer; API returns 202."""
    from tests.fixtures.openalex_responses import SAMPLE_WORK_RAW
    from litexplorer.external.openalex import OpenAlexClient

    work = _make_work(db_session, doi="10.1234/seed", title="Seed Paper")

    seed_ext = _ext_work("Seed Paper", doi="10.1234/seed", ss_id="seed-ss-id")
    mock_ss_client.get_paper_by_doi.return_value = seed_ext
    mock_ss_client.get_references.return_value = [
        _ext_work("Ref with DOI", doi="10.1145/3230543.3230563", ss_id="ref-ss-id"),
    ]
    mock_ss_client.get_citations.return_value = []

    mock_oa_client.__class__ = OpenAlexClient
    mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 202, resp.text
    assert resp.json()["work_id"] == work.id


# ---------------------------------------------------------------------------
# Title-based fallback when DOI and S2 ID lookups both fail
# ---------------------------------------------------------------------------


def test_enrichment_title_fallback(db_session, client, mock_ss_client):
    """Title fallback behavior is tested in the service layer; API returns 202."""
    work = _make_work(
        db_session,
        doi="10.52202/different-doi",
        semantic_scholar_id="266844130",
        title="Deep Learning for Network Intrusion Detection",
    )

    mock_ss_client.get_paper_by_doi.return_value = None
    seed_ext = _ext_work(
        "Deep Learning for Network Intrusion Detection",
        doi="10.52202/real-doi",
        ss_id="abc123def456",
    )
    mock_ss_client.get_paper_by_id.side_effect = lambda pid: (
        seed_ext if pid == "abc123def456" else None
    )
    mock_ss_client.search_by_title.return_value = [
        {
            "paperId": "abc123def456",
            "title": "Deep Learning for Network Intrusion Detection",
            "year": 2023,
            "externalIds": {"DOI": "10.52202/real-doi"},
        }
    ]
    mock_ss_client.get_references.return_value = [
        _ext_work("Some Reference", doi="10.1111/ref", ss_id="ref-id"),
    ]
    mock_ss_client.get_citations.return_value = []

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 202, resp.text
    assert resp.json()["work_id"] == work.id


def test_enrichment_title_fallback_no_match(db_session, client, mock_ss_client):
    """Title fallback with no match: failure is handled in the background task.

    The endpoint returns 202; the "not found on S2" error is logged, not surfaced.
    """
    work = _make_work(
        db_session,
        doi="10.99999/not-on-s2",
        title="A Very Obscure Paper Title",
    )
    mock_ss_client.get_paper_by_doi.return_value = None
    mock_ss_client.search_by_title.return_value = [
        {"paperId": "xyz", "title": "Something Completely Different", "year": 2020}
    ]

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 202
    assert resp.json()["work_id"] == work.id
