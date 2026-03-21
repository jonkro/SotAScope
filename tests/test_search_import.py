"""Tests for the search-based import endpoints.

POST /api/enrich/search-import/candidates
POST /api/enrich/search-import/confirm
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from litexplorer.api.deps import get_db
from litexplorer.external.base import ExternalWork
from litexplorer.external.crossref import CrossrefClient
from litexplorer.external.openalex import OpenAlexClient
from litexplorer.external.semantic_scholar import SemanticScholarClient
from litexplorer.models.base import Base
from litexplorer.models.library import Work


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
def mock_cr_client():
    mock = MagicMock(spec=CrossrefClient)
    mock.search_works.return_value = []
    mock.get_work_by_doi_raw.return_value = None
    return mock


@pytest.fixture()
def mock_ss_client():
    mock = MagicMock(spec=SemanticScholarClient)
    mock.search_by_title.return_value = []
    mock.get_paper_by_id.return_value = None
    return mock


@pytest.fixture()
def mock_oa_client():
    mock = MagicMock(spec=OpenAlexClient)
    mock.get_work_by_doi_raw.return_value = None
    return mock


@pytest.fixture()
def client(db_session, mock_cr_client, mock_ss_client, mock_oa_client):
    from litexplorer.app import app

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db

    with patch("litexplorer.api.enrichment._get_crossref_client") as mock_get_cr, \
         patch("litexplorer.api.enrichment._get_ss_client") as mock_get_ss, \
         patch("litexplorer.api.enrichment._get_client") as mock_get_oa:
        mock_get_cr.return_value = mock_cr_client
        mock_get_ss.return_value = mock_ss_client
        mock_get_oa.return_value = mock_oa_client
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    app.dependency_overrides.clear()


# Sample Crossref and S2 search result dicts

_CROSSREF_ITEM = {
    "DOI": "10.1145/test.123",
    "title": ["Attention Is All You Need"],
    "author": [{"given": "Ashish", "family": "Vaswani"}],
    "issued": {"date-parts": [[2017]]},
    "container-title": ["NeurIPS"],
    "score": 95.0,
}

_SS_ITEM = {
    "paperId": "abc123ss",
    "title": "Attention Is All You Need",
    "year": 2017,
    "authors": [{"name": "Ashish Vaswani"}],
    "externalIds": {},
}


# ---------------------------------------------------------------------------
# POST /api/enrich/search-import/candidates
# ---------------------------------------------------------------------------


class TestSearchImportCandidates:
    def test_crossref_results_returned(self, client, mock_cr_client):
        """When Crossref returns matches, candidates come from Crossref."""
        mock_cr_client.search_works.return_value = [_CROSSREF_ITEM]

        resp = client.post(
            "/api/enrich/search-import/candidates",
            json={"title": "Attention Is All You Need", "authors": "Vaswani", "year": 2017},
        )
        assert resp.status_code == 200, resp.text

        data = resp.json()
        assert len(data["candidates"]) == 1
        c = data["candidates"][0]
        assert c["doi"] == "10.1145/test.123"
        assert c["title"] == "Attention Is All You Need"
        assert c["source"] == "crossref"
        assert c["authors"] == ["Ashish Vaswani"]
        assert c["year"] == 2017
        assert c["score"] == 95.0
        assert c["venue"] == "NeurIPS"

    def test_ss_fallback_when_crossref_empty(self, client, mock_cr_client, mock_ss_client):
        """When Crossref returns no results, falls back to Semantic Scholar."""
        mock_cr_client.search_works.return_value = []
        mock_ss_client.search_by_title.return_value = [_SS_ITEM]

        resp = client.post(
            "/api/enrich/search-import/candidates",
            json={"title": "Attention Is All You Need"},
        )
        assert resp.status_code == 200, resp.text

        data = resp.json()
        assert len(data["candidates"]) == 1
        c = data["candidates"][0]
        assert c["semantic_scholar_id"] == "abc123ss"
        assert c["source"] == "semantic_scholar"
        assert c["doi"] is None
        assert c["authors"] == ["Ashish Vaswani"]
        assert c["year"] == 2017

    def test_ss_not_called_when_crossref_has_results(self, client, mock_cr_client, mock_ss_client):
        """S2 fallback is NOT triggered when Crossref returns at least one result."""
        mock_cr_client.search_works.return_value = [_CROSSREF_ITEM]

        client.post(
            "/api/enrich/search-import/candidates",
            json={"title": "Attention Is All You Need"},
        )
        mock_ss_client.search_by_title.assert_not_called()

    def test_no_results_from_either_source(self, client, mock_cr_client, mock_ss_client):
        """Returns empty list when both Crossref and S2 return nothing."""
        mock_cr_client.search_works.return_value = []
        mock_ss_client.search_by_title.return_value = []

        resp = client.post(
            "/api/enrich/search-import/candidates",
            json={"title": "Nonexistent Paper XYZ"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["candidates"] == []

    def test_title_required(self, client):
        """Missing title field returns 422."""
        resp = client.post("/api/enrich/search-import/candidates", json={})
        assert resp.status_code == 422

    def test_optional_fields_omitted(self, client, mock_cr_client):
        """Endpoint works with title only (no authors or year)."""
        mock_cr_client.search_works.return_value = [_CROSSREF_ITEM]

        resp = client.post(
            "/api/enrich/search-import/candidates",
            json={"title": "Attention Is All You Need"},
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["candidates"]) == 1

    def test_year_passed_as_filter_not_in_query_crossref(self, client, mock_cr_client):
        """Year must be passed as a separate keyword argument to search_works,
        NOT concatenated into the free-text query string."""
        mock_cr_client.search_works.return_value = [_CROSSREF_ITEM]

        client.post(
            "/api/enrich/search-import/candidates",
            json={"title": "Attention Is All You Need", "year": 2017},
        )

        call_kwargs = mock_cr_client.search_works.call_args
        # First positional arg is the query string — must NOT contain "2017"
        query_arg = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("query", "")
        assert "2017" not in query_arg, f"Year leaked into query string: {query_arg!r}"
        # year= kwarg must be set correctly
        assert call_kwargs.kwargs.get("year") == 2017

    def test_year_passed_as_filter_not_in_query_ss_fallback(
        self, client, mock_cr_client, mock_ss_client
    ):
        """Year must be passed as a separate keyword argument to search_by_title
        when S2 fallback is used, NOT concatenated into the query string."""
        mock_cr_client.search_works.return_value = []
        mock_ss_client.search_by_title.return_value = [_SS_ITEM]

        client.post(
            "/api/enrich/search-import/candidates",
            json={"title": "Attention Is All You Need", "year": 2017},
        )

        call_kwargs = mock_ss_client.search_by_title.call_args
        query_arg = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("query", "")
        assert "2017" not in query_arg, f"Year leaked into query string: {query_arg!r}"
        assert call_kwargs.kwargs.get("year") == 2017


# ---------------------------------------------------------------------------
# POST /api/enrich/search-import/confirm
# ---------------------------------------------------------------------------


class TestSearchImportConfirm:
    def test_confirm_with_doi(self, client, mock_oa_client):
        """Importing by DOI creates a work via the standard OpenAlex pipeline."""
        from tests.fixtures.openalex_responses import SAMPLE_WORK_RAW

        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW

        resp = client.post(
            "/api/enrich/search-import/confirm",
            json={"doi": "10.1145/3230543.3230563"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["work"]["doi"] == "10.1145/3230543.3230563"
        assert data["source"] in ("openalex", "crossref")

    def test_confirm_with_ss_id_only(self, client, mock_ss_client, db_session):
        """Importing by S2 ID creates a work when no DOI is provided."""
        ss_paper = ExternalWork(
            title="S2-Only Paper",
            doi=None,
            semantic_scholar_id="ss-only-id",
            publication_year=2020,
        )
        mock_ss_client.get_paper_by_id.return_value = ss_paper

        resp = client.post(
            "/api/enrich/search-import/confirm",
            json={"semantic_scholar_id": "ss-only-id"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["work"]["semantic_scholar_id"] == "ss-only-id"
        assert data["work"]["title"] == "S2-Only Paper"
        assert data["source"] == "semantic_scholar"

        # Work should be in the database
        from sqlalchemy import select
        works = db_session.execute(select(Work)).scalars().all()
        assert len(works) == 1
        assert works[0].semantic_scholar_id == "ss-only-id"

    def test_confirm_doi_preferred_over_ss_id(self, client, mock_oa_client):
        """When both doi and semantic_scholar_id are provided, DOI takes precedence."""
        from tests.fixtures.openalex_responses import SAMPLE_WORK_RAW

        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW

        resp = client.post(
            "/api/enrich/search-import/confirm",
            json={"doi": "10.1145/3230543.3230563", "semantic_scholar_id": "some-ss-id"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["work"]["doi"] == "10.1145/3230543.3230563"

    def test_confirm_neither_returns_400(self, client):
        """Neither doi nor semantic_scholar_id → 400."""
        resp = client.post("/api/enrich/search-import/confirm", json={})
        assert resp.status_code == 400
        assert "doi" in resp.json()["detail"].lower() or "semantic_scholar_id" in resp.json()["detail"].lower()

    def test_confirm_doi_not_found_returns_404(self, client, mock_oa_client, mock_cr_client):
        """DOI not found in OpenAlex or Crossref → 404."""
        mock_oa_client.get_work_by_doi_raw.return_value = None
        mock_cr_client.get_work_by_doi_raw.return_value = None

        resp = client.post(
            "/api/enrich/search-import/confirm",
            json={"doi": "10.9999/notfound"},
        )
        assert resp.status_code == 404

    def test_confirm_ss_id_not_found_returns_404(self, client, mock_ss_client):
        """S2 ID not found on Semantic Scholar → 404."""
        mock_ss_client.get_paper_by_id.return_value = None

        resp = client.post(
            "/api/enrich/search-import/confirm",
            json={"semantic_scholar_id": "nonexistent-ss-id"},
        )
        assert resp.status_code == 404

    def test_confirm_ss_id_with_doi_runs_oa_pipeline(
        self, client, mock_ss_client, mock_oa_client, db_session
    ):
        """Regression: importing by S2 ID when the paper HAS a DOI should run the
        OA pipeline so the resulting work gets both doi and openalex_id set.
        Previously, import_by_semantic_scholar_id called _upsert_work directly and
        bypassed OpenAlex, leaving openalex_id=None."""
        from tests.fixtures.openalex_responses import SAMPLE_WORK_RAW
        from sqlalchemy import select

        # S2 returns a paper that has a known DOI
        ss_paper = ExternalWork(
            title="Restructuring endpoint congestion control",
            doi="10.1145/3230543.3230563",
            semantic_scholar_id="ss-doi-paper",
            publication_year=2018,
        )
        mock_ss_client.get_paper_by_id.return_value = ss_paper
        # OA knows this paper
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW

        resp = client.post(
            "/api/enrich/search-import/confirm",
            json={"semantic_scholar_id": "ss-doi-paper"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # The work must have doi AND openalex_id populated (OA pipeline was used)
        assert data["work"]["doi"] == "10.1145/3230543.3230563"
        assert data["work"]["openalex_id"] is not None

        # Exactly one work in the database (no duplicates)
        works = db_session.execute(select(Work)).scalars().all()
        assert len(works) == 1
        assert works[0].doi == "10.1145/3230543.3230563"
        assert works[0].openalex_id is not None
        # S2 ID should also be stored
        assert works[0].semantic_scholar_id == "ss-doi-paper"
