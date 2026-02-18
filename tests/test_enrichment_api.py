"""API integration tests for enrichment endpoints (mock client + test HTTP)."""

import json
from unittest.mock import MagicMock, patch

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

    def test_no_api_key(self, db_session):
        """Returns 503 when no API key is configured."""
        from litexplorer.app import app

        def _override_get_db():
            try:
                yield db_session
            finally:
                pass

        app.dependency_overrides[get_db] = _override_get_db

        # Don't patch _get_client — let it check the real (empty) settings
        with patch("litexplorer.api.enrichment.settings") as mock_settings:
            mock_settings.openalex_api_key = None
            # Need to un-patch _get_client for this test
            with patch.dict("litexplorer.api.enrichment.__dict__", {}):
                # Reload the actual _get_client function
                from litexplorer.api.enrichment import _get_client
                with patch("litexplorer.api.enrichment._get_client", _get_client):
                    with TestClient(app, raise_server_exceptions=False) as c:
                        resp = c.post("/api/enrich/doi", json={"doi": "10.1145/test"})
                        assert resp.status_code == 503

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
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert len(data["works"]) >= 1

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
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

    def test_force_refresh(self, client, mock_oa_client):
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        resp = client.post("/api/enrich/doi", json={"doi": "10.1145/3230543.3230563"})
        work_id = resp.json()["work"]["id"]

        mock_oa_client.get_forward_citations_raw.return_value = [SAMPLE_STUB_WORK_RAW]
        resp = client.post(
            f"/api/enrich/works/{work_id}/citations/forward?force_refresh=true"
        )
        assert resp.status_code == 200
