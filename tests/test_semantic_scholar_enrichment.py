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
    """Endpoint fetches refs+citations and returns a summary for a work with a DOI."""
    work = _make_work(db_session, doi="10.1234/test", title="Seed Paper")

    # S2 lookup returns the seed paper itself
    seed_ext = _ext_work("Seed Paper", doi="10.1234/test", ss_id="abc123")
    mock_ss_client.get_paper_by_doi.return_value = seed_ext

    # 2 references, 1 citing paper
    mock_ss_client.get_references.return_value = [
        _ext_work("Ref A", doi="10.1111/ref-a", ss_id="ref-a-id"),
        _ext_work("Ref B", doi="10.2222/ref-b", ss_id="ref-b-id"),
    ]
    mock_ss_client.get_citations.return_value = [
        _ext_work("Citer X", doi="10.3333/citer-x", ss_id="citer-x-id"),
    ]

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["new_references"] == 2
    assert data["existing_references"] == 0
    assert data["new_citing"] == 1
    assert data["existing_citing"] == 0

    # semantic_scholar_id should now be set on the work
    db_session.refresh(work)
    assert work.semantic_scholar_id == "abc123"

    # Citation edges should exist
    backward_citations = db_session.execute(
        select(Citation).where(Citation.citing_work_id == work.id)
    ).scalars().all()
    assert len(backward_citations) == 2

    forward_citations = db_session.execute(
        select(Citation).where(Citation.cited_work_id == work.id)
    ).scalars().all()
    assert len(forward_citations) == 1

    # All edges should be sourced from semantic_scholar
    for c in backward_citations + forward_citations:
        assert c.source == "semantic_scholar"


def test_successful_enrichment_by_ss_id(db_session, client, mock_ss_client):
    """Endpoint falls back to semantic_scholar_id lookup when work has no DOI."""
    work = _make_work(db_session, doi=None, semantic_scholar_id="existing-ss-id", title="No DOI")

    seed_ext = _ext_work("No DOI", ss_id="existing-ss-id")
    mock_ss_client.get_paper_by_doi.return_value = None  # no DOI
    mock_ss_client.get_paper_by_id.return_value = seed_ext

    mock_ss_client.get_references.return_value = [
        _ext_work("Reference", doi=None, ss_id="ref-ss-id"),
    ]
    mock_ss_client.get_citations.return_value = []

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["new_references"] == 1
    assert data["existing_references"] == 0
    assert data["new_citing"] == 0

    # S2 lookup should use the stored ID (no DOI to try)
    mock_ss_client.get_paper_by_doi.assert_not_called()
    mock_ss_client.get_paper_by_id.assert_called_once_with("existing-ss-id")


def test_work_response_includes_updated_ss_id(db_session, client, mock_ss_client):
    """Response body contains the updated work with semantic_scholar_id set."""
    work = _make_work(db_session, doi="10.5678/test", title="My Paper")
    mock_ss_client.get_paper_by_doi.return_value = _ext_work("My Paper", doi="10.5678/test", ss_id="new-ss-id")

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 200

    assert resp.json()["work"]["semantic_scholar_id"] == "new-ss-id"


# ---------------------------------------------------------------------------
# Duplicate citation handling
# ---------------------------------------------------------------------------


def test_duplicate_citations_not_duplicated(db_session, client, mock_ss_client):
    """Citations that already exist in the DB are counted as 'existing', not inserted twice."""
    work = _make_work(db_session, doi="10.1234/seed", title="Seed")
    ref_work = _make_work(db_session, doi="10.9999/ref", title="Existing Reference")

    # Pre-insert the citation edge
    db_session.add(Citation(citing_work_id=work.id, cited_work_id=ref_work.id, source="openalex"))
    db_session.commit()

    seed_ext = _ext_work("Seed", doi="10.1234/seed", ss_id="seed-ss")
    mock_ss_client.get_paper_by_doi.return_value = seed_ext
    # S2 returns the same ref
    mock_ss_client.get_references.return_value = [
        _ext_work("Existing Reference", doi="10.9999/ref", ss_id="ref-ss"),
    ]
    mock_ss_client.get_citations.return_value = []

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 200

    data = resp.json()
    assert data["new_references"] == 0
    assert data["existing_references"] == 1

    # Still only one citation edge
    citations = db_session.execute(
        select(Citation).where(
            Citation.citing_work_id == work.id,
            Citation.cited_work_id == ref_work.id,
        )
    ).scalars().all()
    assert len(citations) == 1


def test_second_call_reports_all_existing(db_session, client, mock_ss_client):
    """Calling the endpoint twice: first call adds, second call finds all existing."""
    work = _make_work(db_session, doi="10.1234/seed", title="Seed")
    seed_ext = _ext_work("Seed", doi="10.1234/seed", ss_id="seed-id")
    refs = [_ext_work("Ref A", doi="10.1111/a", ss_id="a-id")]

    mock_ss_client.get_paper_by_doi.return_value = seed_ext
    mock_ss_client.get_references.return_value = refs
    mock_ss_client.get_citations.return_value = []

    r1 = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert r1.status_code == 200
    assert r1.json()["new_references"] == 1

    r2 = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert r2.status_code == 200
    assert r2.json()["new_references"] == 0
    assert r2.json()["existing_references"] == 1


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


def test_paper_not_on_semantic_scholar_returns_503(db_session, client, mock_ss_client):
    """When S2 returns nothing for the DOI, the endpoint returns 503."""
    work = _make_work(db_session, doi="10.1234/unknown", title="Unknown Paper")
    mock_ss_client.get_paper_by_doi.return_value = None

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 503
    assert "semantic scholar" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# semantic_scholar_id deduplication
# ---------------------------------------------------------------------------


def test_existing_work_matched_by_ss_id(db_session, client, mock_ss_client):
    """Works returned by S2 are matched against existing DB entries by S2 ID."""
    work = _make_work(db_session, doi="10.1234/seed", title="Seed")
    # Pre-existing work with a known S2 ID but no DOI
    existing_ref = _make_work(
        db_session, doi=None, semantic_scholar_id="known-ss-ref", title="Known Ref"
    )

    seed_ext = _ext_work("Seed", doi="10.1234/seed", ss_id="seed-id")
    mock_ss_client.get_paper_by_doi.return_value = seed_ext
    # S2 returns the known ref with a different title (update-without-overwrite) but same S2 ID
    mock_ss_client.get_references.return_value = [
        _ext_work("Known Ref Updated", doi=None, ss_id="known-ss-ref"),
    ]
    mock_ss_client.get_citations.return_value = []

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 200

    # Should have created one edge pointing to the EXISTING ref work (no duplicate created)
    all_refs = db_session.execute(select(Citation).where(Citation.citing_work_id == work.id)).scalars().all()
    assert len(all_refs) == 1
    assert all_refs[0].cited_work_id == existing_ref.id

    # No new works created — total works in DB should still be 2 (seed + existing_ref)
    total = db_session.execute(select(Work)).scalars().all()
    assert len(total) == 2


# ---------------------------------------------------------------------------
# Regression: S2 refs/citations with DOI should get openalex_id via OA pipeline
# ---------------------------------------------------------------------------


def test_s2_ref_with_doi_gets_openalex_id(db_session, client, mock_ss_client, mock_oa_client):
    """Regression: when S2 returns a reference paper with a known DOI, the resulting
    DB work should have openalex_id populated (via the OA import pipeline), not left
    as None.  A work with openalex_id=None cannot use 'Fetch references (OA)'."""
    from tests.fixtures.openalex_responses import SAMPLE_WORK_RAW
    from litexplorer.external.openalex import OpenAlexClient

    work = _make_work(db_session, doi="10.1234/seed", title="Seed Paper")

    seed_ext = _ext_work("Seed Paper", doi="10.1234/seed", ss_id="seed-ss-id")
    mock_ss_client.get_paper_by_doi.return_value = seed_ext

    # S2 returns one reference with a DOI that OA knows about
    ref_doi = "10.1145/3230543.3230563"
    mock_ss_client.get_references.return_value = [
        _ext_work("Ref with DOI", doi=ref_doi, ss_id="ref-ss-id"),
    ]
    mock_ss_client.get_citations.return_value = []

    # OA mock: set a spec so that get_work_by_doi_raw returns a proper dict
    mock_oa_client.__class__ = OpenAlexClient
    mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW

    resp = client.post(f"/api/enrich/works/{work.id}/semantic-scholar")
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["new_references"] == 1

    # The ref work in the DB must have openalex_id populated (OA pipeline ran)
    all_works = db_session.execute(select(Work)).scalars().all()
    ref_work = next((w for w in all_works if w.doi == ref_doi), None)
    assert ref_work is not None, "Reference work not found in DB"
    assert ref_work.openalex_id is not None, (
        "openalex_id should be populated via OA pipeline when S2 ref has a DOI"
    )
    # No duplicate works (one seed + one ref)
    assert len(all_works) == 2
