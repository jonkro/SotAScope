"""Unit tests for the enrichment service (mock client, real in-memory DB)."""

import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from litexplorer.external.openalex import OpenAlexClient, parse_work
from litexplorer.models.base import Base
from litexplorer.models.cache import ApiCache
from litexplorer.models.library import (
    Author,
    Citation,
    Venue,
    VenueAlias,
    Work,
    WorkAuthor,
    WorkLocation,
)
from litexplorer.services.enrichment import EnrichmentService
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
def mock_client():
    return MagicMock(spec=OpenAlexClient)


@pytest.fixture()
def service(db_session, mock_client):
    return EnrichmentService(db=db_session, client=mock_client)


# ---------------------------------------------------------------------------
# import_by_doi
# ---------------------------------------------------------------------------

class TestImportByDoi:
    def test_imports_new_work(self, service, mock_client, db_session):
        mock_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        work = service.import_by_doi("10.1145/3230543.3230563")

        assert work is not None
        assert work.doi == "10.1145/3230543.3230563"
        assert work.openalex_id == "W2963073370"
        assert work.title == "Restructuring endpoint congestion control"
        assert work.publication_year == 2018
        assert work.citation_count == 85
        assert work.abstract is not None
        assert work.arxiv_id == "1810.03259"

    def test_creates_venue(self, service, mock_client, db_session):
        mock_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        work = service.import_by_doi("10.1145/3230543.3230563")

        assert work.venue_id is not None
        venue = db_session.get(Venue, work.venue_id)
        assert venue.name == "ACM SIGCOMM"
        assert venue.openalex_id == "S1234567"
        assert venue.venue_type == "conference"

    def test_creates_authors(self, service, mock_client, db_session):
        mock_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        work = service.import_by_doi("10.1145/3230543.3230563")

        work_authors = db_session.execute(
            select(WorkAuthor).where(WorkAuthor.work_id == work.id)
        ).scalars().all()
        assert len(work_authors) == 2
        # Check ordering
        names = []
        for wa in sorted(work_authors, key=lambda wa: wa.position):
            author = db_session.get(Author, wa.author_id)
            names.append(author.name)
        assert names == ["Akshay Narayan", "Frank Cangialosi"]

    def test_creates_locations(self, service, mock_client, db_session):
        mock_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        work = service.import_by_doi("10.1145/3230543.3230563")

        locations = db_session.execute(
            select(WorkLocation).where(WorkLocation.work_id == work.id)
        ).scalars().all()
        assert len(locations) == 2
        types = {loc.location_type for loc in locations}
        assert types == {"venue", "preprint"}

    def test_caches_response(self, service, mock_client, db_session):
        mock_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        service.import_by_doi("10.1145/3230543.3230563")

        cached = db_session.execute(
            select(ApiCache).where(
                ApiCache.source == "openalex",
                ApiCache.query_key == "work:doi:10.1145/3230543.3230563",
            )
        ).scalar_one_or_none()
        assert cached is not None
        assert cached.cache_type == "permanent"

    def test_uses_cache_on_second_call(self, service, mock_client, db_session):
        mock_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW

        work1 = service.import_by_doi("10.1145/3230543.3230563")
        work2 = service.import_by_doi("10.1145/3230543.3230563")

        # Only one API call should have been made
        assert mock_client.get_work_by_doi_raw.call_count == 1
        assert work1.id == work2.id

    def test_returns_none_when_not_found(self, service, mock_client):
        mock_client.get_work_by_doi_raw.return_value = None
        result = service.import_by_doi("10.9999/nonexistent")
        assert result is None

    def test_dedup_by_doi(self, service, mock_client, db_session):
        """Importing the same DOI twice returns the same Work."""
        mock_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW

        work1 = service.import_by_doi("10.1145/3230543.3230563")
        # Clear cache to force re-fetch but still dedup
        db_session.execute(
            select(ApiCache).where(ApiCache.query_key == "work:doi:10.1145/3230543.3230563")
        )
        work2 = service.import_by_doi("10.1145/3230543.3230563")

        assert work1.id == work2.id
        # Should only be one Work in DB
        count = db_session.execute(select(Work)).scalars().all()
        assert len(count) == 1

    def test_update_without_overwrite(self, service, mock_client, db_session):
        """Existing fields are not overwritten, except citation_count."""
        mock_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        work = service.import_by_doi("10.1145/3230543.3230563")
        original_abstract = work.abstract

        # Modify the cached response to have different abstract and citation count
        modified = dict(SAMPLE_WORK_RAW)
        modified["abstract_inverted_index"] = {"Different": [0], "abstract.": [1]}
        modified["cited_by_count"] = 999

        # Clear cache and re-import
        cache_entry = db_session.execute(
            select(ApiCache).where(ApiCache.query_key == "work:doi:10.1145/3230543.3230563")
        ).scalar_one()
        cache_entry.response_json = json.dumps(modified)
        db_session.commit()

        work2 = service.import_by_doi("10.1145/3230543.3230563")
        assert work2.id == work.id
        # Abstract should NOT change (update-without-overwrite)
        assert work2.abstract == original_abstract
        # Citation count SHOULD be updated
        assert work2.citation_count == 999


# ---------------------------------------------------------------------------
# fetch_backward_citations
# ---------------------------------------------------------------------------

class TestFetchBackwardCitations:
    def test_fetches_and_persists(self, service, mock_client, db_session):
        # First import the seed work
        mock_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        seed = service.import_by_doi("10.1145/3230543.3230563")

        # Mock batch fetch of referenced works
        mock_client.get_works_by_ids_raw.return_value = [SAMPLE_REFERENCED_WORK_RAW]

        refs = service.fetch_backward_citations(seed.id)
        assert len(refs) == 1
        assert refs[0].doi == "10.1234/example.2015"

        # Citation edge should exist
        citation = db_session.execute(
            select(Citation).where(
                Citation.citing_work_id == seed.id,
                Citation.cited_work_id == refs[0].id,
            )
        ).scalar_one_or_none()
        assert citation is not None
        assert citation.source == "openalex"

    def test_caches_backward_citations(self, service, mock_client, db_session):
        mock_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        seed = service.import_by_doi("10.1145/3230543.3230563")

        mock_client.get_works_by_ids_raw.return_value = [SAMPLE_REFERENCED_WORK_RAW]
        service.fetch_backward_citations(seed.id)

        cached = db_session.execute(
            select(ApiCache).where(
                ApiCache.query_key == f"backward_citations:{seed.openalex_id}"
            )
        ).scalar_one_or_none()
        assert cached is not None
        assert cached.cache_type == "permanent"

    def test_uses_cache_on_second_call(self, service, mock_client, db_session):
        mock_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        seed = service.import_by_doi("10.1145/3230543.3230563")

        mock_client.get_works_by_ids_raw.return_value = [SAMPLE_REFERENCED_WORK_RAW]
        service.fetch_backward_citations(seed.id)
        service.fetch_backward_citations(seed.id)

        # Only one batch fetch call
        assert mock_client.get_works_by_ids_raw.call_count == 1

    def test_raises_for_missing_work(self, service):
        with pytest.raises(ValueError, match="not found"):
            service.fetch_backward_citations(9999)

    def test_raises_for_no_openalex_id(self, service, db_session):
        work = Work(title="No OA ID", doi="10.0000/test")
        db_session.add(work)
        db_session.commit()
        with pytest.raises(ValueError, match="no OpenAlex ID"):
            service.fetch_backward_citations(work.id)


# ---------------------------------------------------------------------------
# fetch_forward_citations
# ---------------------------------------------------------------------------

class TestFetchForwardCitations:
    def test_fetches_and_persists(self, service, mock_client, db_session):
        mock_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        seed = service.import_by_doi("10.1145/3230543.3230563")

        mock_client.get_forward_citations_raw.return_value = [SAMPLE_STUB_WORK_RAW]
        citers = service.fetch_forward_citations(seed.id)

        assert len(citers) == 1
        assert citers[0].title == "Some Citing Paper With Minimal Data"

        # Citation edge: citer -> seed
        citation = db_session.execute(
            select(Citation).where(
                Citation.citing_work_id == citers[0].id,
                Citation.cited_work_id == seed.id,
            )
        ).scalar_one_or_none()
        assert citation is not None

    def test_cache_is_timestamped(self, service, mock_client, db_session):
        mock_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        seed = service.import_by_doi("10.1145/3230543.3230563")

        mock_client.get_forward_citations_raw.return_value = [SAMPLE_STUB_WORK_RAW]
        service.fetch_forward_citations(seed.id)

        cached = db_session.execute(
            select(ApiCache).where(
                ApiCache.query_key == f"forward_citations:{seed.openalex_id}"
            )
        ).scalar_one_or_none()
        assert cached is not None
        assert cached.cache_type == "timestamped"

    def test_force_refresh_bypasses_cache(self, service, mock_client, db_session):
        mock_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        seed = service.import_by_doi("10.1145/3230543.3230563")

        mock_client.get_forward_citations_raw.return_value = [SAMPLE_STUB_WORK_RAW]
        service.fetch_forward_citations(seed.id)
        service.fetch_forward_citations(seed.id, force_refresh=True)

        # Two API calls (first + forced refresh)
        assert mock_client.get_forward_citations_raw.call_count == 2


# ---------------------------------------------------------------------------
# Venue alias auto-creation
# ---------------------------------------------------------------------------

class TestVenueAliasAutoCreation:
    def test_creates_alias_for_different_name(self, service, mock_client, db_session):
        mock_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW
        service.import_by_doi("10.1145/3230543.3230563")

        # Now import another work from the same venue (same openalex_id) but different name
        modified = dict(SAMPLE_WORK_RAW)
        modified["id"] = "https://openalex.org/W1111111111"
        modified["doi"] = "https://doi.org/10.1145/9999999.9999999"
        modified["primary_location"] = {
            "source": {
                "id": "https://openalex.org/S1234567",  # same venue ID
                "display_name": "SIGCOMM '18",  # different name
                "type": "conference",
            },
            "is_primary": True,
            "landing_page_url": "https://dl.acm.org/doi/10.1145/9999999.9999999",
        }
        modified["referenced_works"] = []
        modified["authorships"] = []
        modified["locations"] = [modified["primary_location"]]

        mock_client.get_work_by_doi_raw.return_value = modified
        service.import_by_doi("10.1145/9999999.9999999")

        aliases = db_session.execute(select(VenueAlias)).scalars().all()
        assert len(aliases) == 1
        assert aliases[0].alias == "SIGCOMM '18"
