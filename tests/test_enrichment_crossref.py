"""Tests for Crossref integration in the enrichment service and API."""

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
from litexplorer.models.cache import ApiCache
from litexplorer.models.library import Venue, VenueAlias, Work
from litexplorer.services.enrichment import EnrichmentService
from tests.fixtures.crossref_responses import (
    SAMPLE_CROSSREF_JOURNAL_WORK,
    SAMPLE_CROSSREF_WORK,
)
from tests.fixtures.openalex_responses import SAMPLE_WORK_RAW


# ---------------------------------------------------------------------------
# Shared fixtures
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
    return MagicMock(spec=OpenAlexClient)


@pytest.fixture()
def mock_cr_client():
    return MagicMock(spec=CrossrefClient)


@pytest.fixture()
def service(db_session, mock_oa_client, mock_cr_client):
    return EnrichmentService(
        db=db_session, client=mock_oa_client, crossref_client=mock_cr_client
    )


# ---------------------------------------------------------------------------
# Service: import_by_doi with Crossref fallback
# ---------------------------------------------------------------------------


class TestImportByDoiCrossrefFallback:
    def test_falls_back_to_crossref_when_openalex_returns_none(
        self, service, mock_oa_client, mock_cr_client, db_session
    ):
        mock_oa_client.get_work_by_doi_raw.return_value = None
        mock_cr_client.get_work_by_doi_raw.return_value = SAMPLE_CROSSREF_WORK

        work = service.import_by_doi("10.1145/3230543.3230563")

        assert work is not None
        assert work.doi == "10.1145/3230543.3230563"
        assert work.title == "Restructuring endpoint congestion control"
        assert work.citation_count == 92
        assert work.publication_year == 2018

    def test_crossref_fallback_creates_venue_with_issn(
        self, service, mock_oa_client, mock_cr_client, db_session
    ):
        mock_oa_client.get_work_by_doi_raw.return_value = None
        mock_cr_client.get_work_by_doi_raw.return_value = SAMPLE_CROSSREF_WORK

        work = service.import_by_doi("10.1145/3230543.3230563")

        assert work.venue_id is not None
        venue = db_session.get(Venue, work.venue_id)
        assert venue.issn == "0146-4833"
        assert venue.publisher == "Association for Computing Machinery (ACM)"
        assert venue.venue_type == "conference"

    def test_crossref_fallback_caches_under_crossref_source(
        self, service, mock_oa_client, mock_cr_client, db_session
    ):
        mock_oa_client.get_work_by_doi_raw.return_value = None
        mock_cr_client.get_work_by_doi_raw.return_value = SAMPLE_CROSSREF_WORK

        service.import_by_doi("10.1145/3230543.3230563")

        cached = db_session.execute(
            select(ApiCache).where(
                ApiCache.source == "crossref",
                ApiCache.query_key == "work:doi:10.1145/3230543.3230563",
            )
        ).scalar_one_or_none()
        assert cached is not None
        assert cached.cache_type == "permanent"

    def test_crossref_fallback_uses_cache(
        self, service, mock_oa_client, mock_cr_client, db_session
    ):
        mock_oa_client.get_work_by_doi_raw.return_value = None
        mock_cr_client.get_work_by_doi_raw.return_value = SAMPLE_CROSSREF_WORK

        service.import_by_doi("10.1145/3230543.3230563")
        service.import_by_doi("10.1145/3230543.3230563")

        # OpenAlex called twice (once per import_by_doi), Crossref only once
        assert mock_oa_client.get_work_by_doi_raw.call_count == 2
        assert mock_cr_client.get_work_by_doi_raw.call_count == 1

    def test_returns_none_when_both_sources_miss(
        self, service, mock_oa_client, mock_cr_client
    ):
        mock_oa_client.get_work_by_doi_raw.return_value = None
        mock_cr_client.get_work_by_doi_raw.return_value = None

        result = service.import_by_doi("10.9999/nonexistent")
        assert result is None

    def test_no_crossref_fallback_when_client_not_set(self, db_session, mock_oa_client):
        """Without crossref_client, returns None when OpenAlex misses."""
        svc = EnrichmentService(db=db_session, client=mock_oa_client)
        mock_oa_client.get_work_by_doi_raw.return_value = None

        result = svc.import_by_doi("10.9999/nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# Service: enrich_from_crossref
# ---------------------------------------------------------------------------


class TestEnrichFromCrossref:
    def test_enriches_venue_with_issn_and_publisher(
        self, service, mock_oa_client, mock_cr_client, db_session
    ):
        # First import via OpenAlex (no ISSN)
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        work = service.import_by_doi("10.1145/3230543.3230563")
        venue = db_session.get(Venue, work.venue_id)
        assert venue.issn is None
        assert venue.publisher is None

        # Now enrich from Crossref
        mock_cr_client.get_work_by_doi_raw.return_value = SAMPLE_CROSSREF_WORK
        enriched = service.enrich_from_crossref(work.id)

        venue = db_session.get(Venue, enriched.venue_id)
        assert venue.issn == "0146-4833"
        assert venue.publisher == "Association for Computing Machinery (ACM)"

    def test_caches_crossref_response(
        self, service, mock_oa_client, mock_cr_client, db_session
    ):
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        work = service.import_by_doi("10.1145/3230543.3230563")

        mock_cr_client.get_work_by_doi_raw.return_value = SAMPLE_CROSSREF_WORK
        service.enrich_from_crossref(work.id)

        cached = db_session.execute(
            select(ApiCache).where(
                ApiCache.source == "crossref",
                ApiCache.query_key == "work:doi:10.1145/3230543.3230563",
            )
        ).scalar_one_or_none()
        assert cached is not None

    def test_uses_cache_on_second_enrich(
        self, service, mock_oa_client, mock_cr_client, db_session
    ):
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        work = service.import_by_doi("10.1145/3230543.3230563")

        mock_cr_client.get_work_by_doi_raw.return_value = SAMPLE_CROSSREF_WORK
        service.enrich_from_crossref(work.id)
        service.enrich_from_crossref(work.id)

        assert mock_cr_client.get_work_by_doi_raw.call_count == 1

    def test_raises_for_missing_work(self, service):
        with pytest.raises(ValueError, match="not found"):
            service.enrich_from_crossref(9999)

    def test_raises_for_no_doi(self, service, db_session):
        work = Work(title="No DOI Paper")
        db_session.add(work)
        db_session.commit()
        with pytest.raises(ValueError, match="no DOI"):
            service.enrich_from_crossref(work.id)

    def test_raises_for_crossref_not_found(
        self, service, mock_oa_client, mock_cr_client, db_session
    ):
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        work = service.import_by_doi("10.1145/3230543.3230563")

        mock_cr_client.get_work_by_doi_raw.return_value = None
        with pytest.raises(ValueError, match="not found in Crossref"):
            service.enrich_from_crossref(work.id)

    def test_does_not_overwrite_existing_issn(
        self, service, mock_oa_client, mock_cr_client, db_session
    ):
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        work = service.import_by_doi("10.1145/3230543.3230563")

        # Manually set ISSN on venue
        venue = db_session.get(Venue, work.venue_id)
        venue.issn = "existing-issn"
        db_session.commit()

        mock_cr_client.get_work_by_doi_raw.return_value = SAMPLE_CROSSREF_WORK
        service.enrich_from_crossref(work.id)

        venue = db_session.get(Venue, work.venue_id)
        assert venue.issn == "existing-issn"  # not overwritten

    def test_assigns_venue_when_work_has_none(
        self, service, mock_cr_client, db_session
    ):
        """If a work has no venue, enrich_from_crossref should create one."""
        work = Work(title="Test Paper", doi="10.1109/tnet.2020.3027697")
        db_session.add(work)
        db_session.commit()

        mock_cr_client.get_work_by_doi_raw.return_value = SAMPLE_CROSSREF_JOURNAL_WORK
        enriched = service.enrich_from_crossref(work.id)

        assert enriched.venue_id is not None
        venue = db_session.get(Venue, enriched.venue_id)
        assert venue.issn == "1063-6692"
        assert venue.venue_type == "journal"


# ---------------------------------------------------------------------------
# Service: ISSN-based venue matching in _resolve_venue
# ---------------------------------------------------------------------------


class TestISSNVenueMatching:
    def test_matches_by_issn_and_creates_alias(
        self, service, mock_oa_client, mock_cr_client, db_session
    ):
        """When a venue already exists with a matching ISSN, reuse it and alias the new name."""
        # Create existing venue with ISSN
        existing = Venue(
            name="IEEE/ACM Trans. Networking",
            issn="1063-6692",
            venue_type="journal",
        )
        db_session.add(existing)
        db_session.commit()

        # Import a work via Crossref fallback that has same ISSN but different name
        mock_oa_client.get_work_by_doi_raw.return_value = None
        mock_cr_client.get_work_by_doi_raw.return_value = SAMPLE_CROSSREF_JOURNAL_WORK

        work = service.import_by_doi("10.1109/tnet.2020.3027697")

        assert work.venue_id == existing.id
        # Alias should have been created for the Crossref name
        aliases = db_session.execute(
            select(VenueAlias).where(VenueAlias.venue_id == existing.id)
        ).scalars().all()
        alias_names = [a.alias for a in aliases]
        assert "IEEE/ACM Transactions on Networking" in alias_names


# ---------------------------------------------------------------------------
# API: POST /api/enrich/works/{id}/crossref
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client(db_session, mock_oa_client, mock_cr_client):
    """TestClient with both OpenAlex and Crossref clients mocked."""
    from litexplorer.app import app

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db

    with patch("litexplorer.api.enrichment._get_client") as mock_get_oa, \
         patch("litexplorer.api.enrichment._get_crossref_client") as mock_get_cr:
        mock_get_oa.return_value = mock_oa_client
        mock_get_cr.return_value = mock_cr_client
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    app.dependency_overrides.clear()


class TestCrossrefEnrichEndpoint:
    def test_success(self, api_client, mock_oa_client, mock_cr_client, db_session):
        # Import a work first
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        resp = api_client.post("/api/enrich/doi", json={"doi": "10.1145/3230543.3230563"})
        assert resp.status_code == 200
        work_id = resp.json()["work"]["id"]

        # Crossref enrichment is now backgrounded — returns 202 immediately
        mock_cr_client.get_work_by_doi_raw.return_value = SAMPLE_CROSSREF_WORK
        resp = api_client.post(f"/api/enrich/works/{work_id}/crossref")
        assert resp.status_code == 202
        assert resp.json()["work_id"] == work_id

    def test_work_not_found(self, api_client):
        resp = api_client.post("/api/enrich/works/9999/crossref")
        assert resp.status_code == 404

    def test_work_without_doi(self, api_client, db_session):
        work = Work(title="No DOI Paper")
        db_session.add(work)
        db_session.commit()

        resp = api_client.post(f"/api/enrich/works/{work.id}/crossref")
        assert resp.status_code == 404
        assert "no DOI" in resp.json()["detail"]

    def test_crossref_not_found(self, api_client, mock_oa_client, mock_cr_client):
        # "Not found in Crossref" happens inside the background task — the endpoint
        # returns 202 immediately after validating work existence and DOI presence.
        mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        resp = api_client.post("/api/enrich/doi", json={"doi": "10.1145/3230543.3230563"})
        work_id = resp.json()["work"]["id"]

        mock_cr_client.get_work_by_doi_raw.return_value = None
        resp = api_client.post(f"/api/enrich/works/{work_id}/crossref")
        assert resp.status_code == 202


class TestDoiFallbackAPI:
    def test_crossref_fallback_through_api(self, api_client, mock_oa_client, mock_cr_client):
        """POST /api/enrich/doi falls back to Crossref when OpenAlex returns None."""
        mock_oa_client.get_work_by_doi_raw.return_value = None
        mock_cr_client.get_work_by_doi_raw.return_value = SAMPLE_CROSSREF_WORK

        resp = api_client.post("/api/enrich/doi", json={"doi": "10.1145/3230543.3230563"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["work"]["doi"] == "10.1145/3230543.3230563"
        assert data["work"]["title"] == "Restructuring endpoint congestion control"
