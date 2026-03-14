"""API integration tests for enrichment endpoints (mock client + test HTTP)."""

import json
from unittest.mock import MagicMock, patch

from litexplorer.external.semantic_scholar import SemanticScholarClient

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from litexplorer.api.deps import get_db
from litexplorer.external.crossref import CrossrefClient
from litexplorer.external.openalex import OpenAlexClient
from litexplorer.models.base import Base
from litexplorer.models.library import Work
from tests.fixtures.openalex_responses import (
    SAMPLE_REFERENCED_WORK_RAW,
    SAMPLE_STUB_WORK_RAW,
    SAMPLE_WORK_RAW,
)


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
    return MagicMock(spec=OpenAlexClient)


@pytest.fixture()
def mock_cr_client():
    mock = MagicMock(spec=CrossrefClient)
    mock.get_work_by_doi_raw.return_value = None
    return mock


@pytest.fixture()
def client(db_session, mock_oa_client, mock_cr_client):
    """TestClient with mocked OpenAlex + Crossref clients."""
    from litexplorer.app import app

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db

    with patch("litexplorer.api.enrichment._get_client") as mock_get_client, \
         patch("litexplorer.api.enrichment._get_crossref_client") as mock_get_cr:
        mock_get_client.return_value = mock_oa_client
        mock_get_cr.return_value = mock_cr_client
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /api/enrich/doi
# ---------------------------------------------------------------------------

class TestEnrichDOI:
    def test_success(self, client, mock_oa_client):
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        resp = client.post("/api/enrich/doi", json={"doi": "10.1145/3230543.3230563"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["work"]["doi"] == "10.1145/3230543.3230563"
        assert data["work"]["title"] == "Restructuring endpoint congestion control"
        assert data["source"] == "openalex"

    def test_not_found(self, client, mock_oa_client):
        mock_oa_client.get_work_by_doi_raw.return_value = None
        resp = client.post("/api/enrich/doi", json={"doi": "10.9999/nope"})
        assert resp.status_code == 404

    def test_no_email_still_proceeds(self, db_session, mock_oa_client, mock_cr_client):
        """Proceeds without polite pool when no email is configured (no 503)."""
        from litexplorer.app import app

        def _override_get_db():
            try:
                yield db_session
            finally:
                pass

        app.dependency_overrides[get_db] = _override_get_db

        mock_oa_client.get_work_by_doi_raw.return_value = None

        with patch("litexplorer.api.enrichment._get_client", return_value=mock_oa_client), \
             patch("litexplorer.api.enrichment._get_crossref_client", return_value=mock_cr_client):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post("/api/enrich/doi", json={"doi": "10.1145/test"})
                # Should get 404 (not found) rather than 503 (service unavailable)
                assert resp.status_code == 404

        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /api/enrich/doi/batch
# ---------------------------------------------------------------------------

class TestEnrichDOIBatch:
    def test_partial_success(self, client, mock_oa_client):
        def side_effect(doi):
            if "3230543" in doi:
                return SAMPLE_WORK_RAW
            return None

        mock_oa_client.get_work_by_doi_raw.side_effect = side_effect

        resp = client.post(
            "/api/enrich/doi/batch",
            json={"dois": ["10.1145/3230543.3230563", "10.9999/missing"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        assert len(data["errors"]) == 1
        assert data["errors"][0]["doi"] == "10.9999/missing"

    def test_all_success(self, client, mock_oa_client):
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        resp = client.post(
            "/api/enrich/doi/batch",
            json={"dois": ["10.1145/3230543.3230563"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        assert len(data["errors"]) == 0


# ---------------------------------------------------------------------------
# POST /api/enrich/works/{id}/citations/backward
# ---------------------------------------------------------------------------

class TestBackwardCitations:
    def test_success(self, client, mock_oa_client, db_session):
        # First import a seed work
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        resp = client.post("/api/enrich/doi", json={"doi": "10.1145/3230543.3230563"})
        work_id = resp.json()["work"]["id"]

        mock_oa_client.get_works_by_ids_raw.return_value = [SAMPLE_REFERENCED_WORK_RAW]
        resp = client.post(f"/api/enrich/works/{work_id}/citations/backward")
        # Endpoint returns 202 immediately; background task runs asynchronously
        assert resp.status_code == 202
        data = resp.json()
        assert data["work_id"] == work_id

    def test_zero_raw_count_when_no_oa_refs(self, client, mock_oa_client, db_session):
        """Endpoint accepts the request and returns 202 regardless of ref count."""
        from tests.fixtures.openalex_responses import SAMPLE_WORK_RAW_NO_REFS
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW_NO_REFS
        resp = client.post("/api/enrich/doi", json={"doi": "10.9999/no-refs"})
        assert resp.status_code == 200
        work_id = resp.json()["work"]["id"]

        resp = client.post(f"/api/enrich/works/{work_id}/citations/backward")
        assert resp.status_code == 202

    def test_work_not_found(self, client):
        resp = client.post("/api/enrich/works/9999/citations/backward")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/enrich/works/{id}/citations/forward
# ---------------------------------------------------------------------------

class TestForwardCitations:
    def test_success(self, client, mock_oa_client):
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        resp = client.post("/api/enrich/doi", json={"doi": "10.1145/3230543.3230563"})
        work_id = resp.json()["work"]["id"]

        mock_oa_client.get_forward_citations_raw.return_value = [SAMPLE_STUB_WORK_RAW]
        resp = client.post(f"/api/enrich/works/{work_id}/citations/forward")
        assert resp.status_code == 202
        assert resp.json()["work_id"] == work_id

    def test_force_refresh(self, client, mock_oa_client):
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        resp = client.post("/api/enrich/doi", json={"doi": "10.1145/3230543.3230563"})
        work_id = resp.json()["work"]["id"]

        mock_oa_client.get_forward_citations_raw.return_value = [SAMPLE_STUB_WORK_RAW]
        resp = client.post(
            f"/api/enrich/works/{work_id}/citations/forward?force_refresh=true"
        )
        assert resp.status_code == 202


# ---------------------------------------------------------------------------
# POST /api/enrich/doi — arXiv ID routing
# ---------------------------------------------------------------------------

# Minimal OA raw response for an arXiv-only paper used in routing tests.
_ARXIV_RAW = {
    "id": "https://openalex.org/W9898989898",
    "doi": None,
    "title": "An arXiv-Only Paper",
    "display_name": "An arXiv-Only Paper",
    "publication_year": 2023,
    "cited_by_count": 5,
    "abstract_inverted_index": None,
    "primary_location": {
        "source": None,
        "is_primary": True,
        "landing_page_url": "https://arxiv.org/abs/2301.12345",
    },
    "locations": [
        {
            "source": None,
            "is_primary": True,
            "landing_page_url": "https://arxiv.org/abs/2301.12345",
            "pdf_url": None,
        }
    ],
    "authorships": [],
    "referenced_works": [],
    "counts_by_year": [],
}


class TestEnrichDOIArxivRouting:
    def test_doi_prefix_routes_to_doi_import(self, client, mock_oa_client):
        """Identifiers starting with '10.' go through the DOI path."""
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW

        resp = client.post("/api/enrich/doi", json={"doi": "10.1145/3230543.3230563"})

        assert resp.status_code == 200
        assert resp.json()["identifier_type"] == "doi"
        mock_oa_client.get_work_by_doi_raw.assert_called_once()
        mock_oa_client.get_work_by_arxiv_id_raw.assert_not_called()

    def test_modern_arxiv_id_routes_to_arxiv_import(self, client, mock_oa_client):
        """Modern arXiv IDs (NNNN.NNNNN) go through the arXiv path."""
        mock_oa_client.get_work_by_arxiv_id_raw.return_value = _ARXIV_RAW

        resp = client.post("/api/enrich/doi", json={"doi": "2301.12345"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["identifier_type"] == "arxiv"
        assert data["work"]["arxiv_id"] == "2301.12345"
        mock_oa_client.get_work_by_arxiv_id_raw.assert_called_once_with("2301.12345")
        mock_oa_client.get_work_by_doi_raw.assert_not_called()

    def test_old_style_arxiv_id_routes_to_arxiv_import(self, client, mock_oa_client):
        """Old-style arXiv IDs (category/NNNNNNN) go through the arXiv path, not DOI."""
        old_style_raw = dict(_ARXIV_RAW)
        old_style_raw["locations"] = [
            {
                "source": None,
                "is_primary": True,
                "landing_page_url": "https://arxiv.org/abs/hep-th/0601001",
                "pdf_url": None,
            }
        ]
        mock_oa_client.get_work_by_arxiv_id_raw.return_value = old_style_raw

        resp = client.post("/api/enrich/doi", json={"doi": "hep-th/0601001"})

        assert resp.status_code == 200
        assert resp.json()["identifier_type"] == "arxiv"
        mock_oa_client.get_work_by_arxiv_id_raw.assert_called_once_with("hep-th/0601001")
        mock_oa_client.get_work_by_doi_raw.assert_not_called()

    def test_arxiv_not_found_returns_404(self, client, mock_oa_client):
        """404 when OA and S2 both return nothing for an arXiv ID."""
        mock_oa_client.get_work_by_arxiv_id_raw.return_value = None
        mock_ss = MagicMock(spec=SemanticScholarClient)
        mock_ss.get_paper.return_value = None

        with patch("litexplorer.api.enrichment._get_ss_client", return_value=mock_ss):
            resp = client.post("/api/enrich/doi", json={"doi": "9999.99999"})

        assert resp.status_code == 404
        assert "arXiv ID not found" in resp.json()["detail"]

    def test_arxiv_s2_fallback_when_oa_misses(self, client, mock_oa_client, db_session):
        """When OA returns nothing, the S2 fallback is tried and can succeed."""
        from litexplorer.external.base import ExternalWork

        mock_oa_client.get_work_by_arxiv_id_raw.return_value = None
        s2_paper = ExternalWork(
            title="Found Via S2",
            arxiv_id="2301.12345",
            semantic_scholar_id="s2xyz",
            doi=None,
            publication_year=2023,
            citation_count=3,
        )
        mock_ss = MagicMock(spec=SemanticScholarClient)
        mock_ss.get_paper.return_value = s2_paper

        with patch("litexplorer.api.enrichment._get_ss_client", return_value=mock_ss):
            resp = client.post("/api/enrich/doi", json={"doi": "2301.12345"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["identifier_type"] == "arxiv"
        assert data["work"]["title"] == "Found Via S2"
        mock_ss.get_paper.assert_called_once_with("ARXIV:2301.12345")


# ---------------------------------------------------------------------------
# normalize_identifier — unit tests
# ---------------------------------------------------------------------------

from litexplorer.services.enrichment import normalize_identifier  # noqa: E402


class TestNormalizeIdentifier:
    def test_strips_arxiv_prefix(self):
        assert normalize_identifier("arXiv:2204.05862") == "2204.05862"

    def test_strips_arxiv_prefix_lowercase(self):
        assert normalize_identifier("arxiv:2204.05862") == "2204.05862"

    def test_strips_arxiv_prefix_uppercase(self):
        assert normalize_identifier("ARXIV:2204.05862") == "2204.05862"

    def test_strips_version_suffix(self):
        assert normalize_identifier("2402.03300v3") == "2402.03300"

    def test_strips_version_suffix_multidigit(self):
        assert normalize_identifier("2402.03300v12") == "2402.03300"

    def test_strips_both_prefix_and_suffix(self):
        assert normalize_identifier("arXiv:2402.03300v3") == "2402.03300"

    def test_doi_unaffected(self):
        assert normalize_identifier("10.1234/foo") == "10.1234/foo"

    def test_doi_unaffected_with_whitespace(self):
        assert normalize_identifier("  10.1234/foo  ") == "10.1234/foo"

    def test_old_style_arxiv_version_stripped(self):
        assert normalize_identifier("hep-th/0601001v2") == "hep-th/0601001"

    def test_strips_whitespace(self):
        assert normalize_identifier("  2301.12345  ") == "2301.12345"

    def test_no_changes_needed(self):
        assert normalize_identifier("2301.12345") == "2301.12345"


# ---------------------------------------------------------------------------
# POST /api/enrich/doi — normalization via endpoint
# ---------------------------------------------------------------------------

class TestEnrichDOIIdentifierNormalization:
    """Verify that the endpoint normalizes identifiers before routing."""

    def test_arxiv_prefix_stripped_before_routing(self, client, mock_oa_client):
        """'arXiv:2204.05862' is normalized to '2204.05862' before the arXiv path."""
        mock_oa_client.get_work_by_arxiv_id_raw.return_value = _ARXIV_RAW

        resp = client.post("/api/enrich/doi", json={"doi": "arXiv:2204.05862"})

        assert resp.status_code == 200
        assert resp.json()["identifier_type"] == "arxiv"
        mock_oa_client.get_work_by_arxiv_id_raw.assert_called_once_with("2204.05862")

    def test_version_suffix_stripped_before_routing(self, client, mock_oa_client):
        """'2402.03300v3' is normalized to '2402.03300'."""
        mock_oa_client.get_work_by_arxiv_id_raw.return_value = _ARXIV_RAW

        resp = client.post("/api/enrich/doi", json={"doi": "2402.03300v3"})

        assert resp.status_code == 200
        assert resp.json()["identifier_type"] == "arxiv"
        mock_oa_client.get_work_by_arxiv_id_raw.assert_called_once_with("2402.03300")

    def test_prefix_and_suffix_both_stripped(self, client, mock_oa_client):
        """'arXiv:2402.03300v3' → both prefix and suffix stripped → '2402.03300'."""
        mock_oa_client.get_work_by_arxiv_id_raw.return_value = _ARXIV_RAW

        resp = client.post("/api/enrich/doi", json={"doi": "arXiv:2402.03300v3"})

        assert resp.status_code == 200
        assert resp.json()["identifier_type"] == "arxiv"
        mock_oa_client.get_work_by_arxiv_id_raw.assert_called_once_with("2402.03300")

    def test_doi_unaffected_by_normalization(self, client, mock_oa_client):
        """'10.1234/foo' is still detected as a DOI and routed to DOI import."""
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW

        resp = client.post("/api/enrich/doi", json={"doi": "10.1145/3230543.3230563"})

        assert resp.status_code == 200
        assert resp.json()["identifier_type"] == "doi"
        mock_oa_client.get_work_by_doi_raw.assert_called_once()
        mock_oa_client.get_work_by_arxiv_id_raw.assert_not_called()

    def test_old_style_arxiv_version_stripped(self, client, mock_oa_client):
        """'hep-th/0601001v2' → version stripped → 'hep-th/0601001'."""
        old_style_raw = dict(_ARXIV_RAW)
        old_style_raw["locations"] = [
            {
                "source": None,
                "is_primary": True,
                "landing_page_url": "https://arxiv.org/abs/hep-th/0601001",
                "pdf_url": None,
            }
        ]
        mock_oa_client.get_work_by_arxiv_id_raw.return_value = old_style_raw

        resp = client.post("/api/enrich/doi", json={"doi": "hep-th/0601001v2"})

        assert resp.status_code == 200
        assert resp.json()["identifier_type"] == "arxiv"
        mock_oa_client.get_work_by_arxiv_id_raw.assert_called_once_with("hep-th/0601001")
