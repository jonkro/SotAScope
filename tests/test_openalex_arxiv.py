"""Tests for OpenAlexClient.get_work_by_arxiv_id and EnrichmentService.import_by_arxiv_id."""

import json
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from litexplorer.external.openalex import OpenAlexClient, parse_work
from litexplorer.models.base import Base
from litexplorer.models.cache import ApiCache
from litexplorer.models.library import Work
from litexplorer.services.enrichment import EnrichmentService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# A realistic OpenAlex response for an arXiv-only paper (no journal DOI).
# Represents GET /works/arxiv:2301.12345
SAMPLE_ARXIV_WORK_RAW = {
    "id": "https://openalex.org/W3333333333",
    "doi": None,
    "title": "Attention Is All You Need: A Study",
    "display_name": "Attention Is All You Need: A Study",
    "publication_year": 2023,
    "cited_by_count": 42,
    "abstract_inverted_index": {
        "We": [0],
        "propose": [1],
        "a": [2],
        "transformer.": [3],
    },
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
            "pdf_url": "https://arxiv.org/pdf/2301.12345",
        }
    ],
    "authorships": [
        {
            "author": {
                "id": "https://openalex.org/A999",
                "display_name": "Alice Researcher",
            },
            "author_position": "first",
        },
    ],
    "referenced_works": [],
    "counts_by_year": [
        {"year": 2023, "cited_by_count": 42},
    ],
}

# A response where the arXiv paper also has a published DOI.
SAMPLE_ARXIV_WITH_DOI_RAW = {
    "id": "https://openalex.org/W4444444444",
    "doi": "https://doi.org/10.1145/9999.8888",
    "title": "Old-Style arXiv Paper",
    "display_name": "Old-Style arXiv Paper",
    "publication_year": 2006,
    "cited_by_count": 300,
    "abstract_inverted_index": None,
    "primary_location": {
        "source": {
            "id": "https://openalex.org/S5555555",
            "display_name": "Journal of Examples",
            "type": "journal",
        },
        "is_primary": True,
        "landing_page_url": "https://example.com/paper",
    },
    "locations": [
        {
            "source": None,
            "is_primary": False,
            "landing_page_url": "https://arxiv.org/abs/hep-th/0601001",
            "pdf_url": None,
        },
    ],
    "authorships": [],
    "referenced_works": [],
    "counts_by_year": [],
}


def _make_response(status_code: int, json_data: dict) -> httpx.Response:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


# ---------------------------------------------------------------------------
# OpenAlexClient.get_work_by_arxiv_id — direct HTTP tests (mock httpx)
# ---------------------------------------------------------------------------

class TestGetWorkByArxivId:
    @pytest.fixture()
    def client(self):
        c = OpenAlexClient(api_key="test-key")
        yield c
        c.close()

    def test_successful_lookup(self, client):
        client._http.get = MagicMock(
            return_value=_make_response(200, SAMPLE_ARXIV_WORK_RAW)
        )
        work = client.get_work_by_arxiv_id("2301.12345")

        assert work is not None
        assert work.external_id == "W3333333333"
        assert work.title == "Attention Is All You Need: A Study"
        assert work.doi is None
        assert work.arxiv_id == "2301.12345"
        assert work.publication_year == 2023
        assert work.citation_count == 42

    def test_calls_correct_endpoint(self, client):
        client._http.get = MagicMock(
            return_value=_make_response(200, SAMPLE_ARXIV_WORK_RAW)
        )
        client.get_work_by_arxiv_id("2301.12345")
        client._http.get.assert_called_once_with("/works/arxiv:2301.12345")

    def test_old_style_arxiv_id(self, client):
        """Old-style arXiv IDs (e.g. hep-th/0601001) are passed through as-is."""
        client._http.get = MagicMock(
            return_value=_make_response(200, SAMPLE_ARXIV_WITH_DOI_RAW)
        )
        work = client.get_work_by_arxiv_id("hep-th/0601001")

        assert work is not None
        assert work.doi == "10.1145/9999.8888"
        client._http.get.assert_called_once_with("/works/arxiv:hep-th/0601001")

    def test_not_found_returns_none(self, client):
        client._http.get = MagicMock(return_value=_make_response(404, {}))
        result = client.get_work_by_arxiv_id("9999.99999")
        assert result is None

    def test_parses_abstract(self, client):
        client._http.get = MagicMock(
            return_value=_make_response(200, SAMPLE_ARXIV_WORK_RAW)
        )
        work = client.get_work_by_arxiv_id("2301.12345")
        assert work.abstract is not None
        assert "transformer" in work.abstract

    def test_parses_citations_by_year(self, client):
        client._http.get = MagicMock(
            return_value=_make_response(200, SAMPLE_ARXIV_WORK_RAW)
        )
        work = client.get_work_by_arxiv_id("2301.12345")
        assert work.citations_by_year == [{"year": 2023, "cited_by_count": 42}]


# ---------------------------------------------------------------------------
# EnrichmentService.import_by_arxiv_id — caching + service-layer tests
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
def mock_client():
    return MagicMock(spec=OpenAlexClient)


@pytest.fixture()
def service(db_session, mock_client):
    return EnrichmentService(db=db_session, client=mock_client)


class TestImportByArxivId:
    def test_imports_new_work(self, service, mock_client, db_session):
        mock_client.get_work_by_arxiv_id_raw.return_value = SAMPLE_ARXIV_WORK_RAW

        work = service.import_by_arxiv_id("2301.12345")

        assert work is not None
        assert work.arxiv_id == "2301.12345"
        assert work.openalex_id == "W3333333333"
        assert work.title == "Attention Is All You Need: A Study"
        assert work.publication_year == 2023
        assert work.citation_count == 42
        assert work.doi is None

    def test_returns_none_when_not_found(self, service, mock_client):
        mock_client.get_work_by_arxiv_id_raw.return_value = None
        result = service.import_by_arxiv_id("9999.99999")
        assert result is None

    def test_caches_response(self, service, mock_client, db_session):
        mock_client.get_work_by_arxiv_id_raw.return_value = SAMPLE_ARXIV_WORK_RAW

        service.import_by_arxiv_id("2301.12345")

        cached = db_session.execute(
            select(ApiCache).where(
                ApiCache.source == "openalex",
                ApiCache.query_key == "work:arxiv:2301.12345",
            )
        ).scalar_one_or_none()
        assert cached is not None
        assert cached.cache_type == "permanent"

    def test_uses_cache_on_second_call(self, service, mock_client, db_session):
        mock_client.get_work_by_arxiv_id_raw.return_value = SAMPLE_ARXIV_WORK_RAW

        work1 = service.import_by_arxiv_id("2301.12345")
        work2 = service.import_by_arxiv_id("2301.12345")

        # API should only be called once; second call reads from cache
        assert mock_client.get_work_by_arxiv_id_raw.call_count == 1
        assert work1.id == work2.id

    def test_dedup_by_arxiv_id(self, service, mock_client, db_session):
        """Importing the same arXiv ID twice returns the same Work row."""
        mock_client.get_work_by_arxiv_id_raw.return_value = SAMPLE_ARXIV_WORK_RAW

        work1 = service.import_by_arxiv_id("2301.12345")
        # Clear cache to force a re-fetch, but dedup should still fire
        db_session.execute(
            select(ApiCache).where(ApiCache.query_key == "work:arxiv:2301.12345")
        )
        work2 = service.import_by_arxiv_id("2301.12345")

        assert work1.id == work2.id
        count = db_session.execute(select(Work)).scalars().all()
        assert len(count) == 1

    def test_strips_whitespace_from_arxiv_id(self, service, mock_client, db_session):
        mock_client.get_work_by_arxiv_id_raw.return_value = SAMPLE_ARXIV_WORK_RAW

        work = service.import_by_arxiv_id("  2301.12345  ")

        assert work is not None
        mock_client.get_work_by_arxiv_id_raw.assert_called_once_with("2301.12345")

    def test_old_style_arxiv_id(self, service, mock_client, db_session):
        """Old-style arXiv IDs with slashes work correctly."""
        mock_client.get_work_by_arxiv_id_raw.return_value = SAMPLE_ARXIV_WITH_DOI_RAW

        work = service.import_by_arxiv_id("hep-th/0601001")

        assert work is not None
        assert work.doi == "10.1145/9999.8888"
        mock_client.get_work_by_arxiv_id_raw.assert_called_once_with("hep-th/0601001")
        # Cache key uses the raw arXiv ID
        cached = db_session.execute(
            select(ApiCache).where(
                ApiCache.source == "openalex",
                ApiCache.query_key == "work:arxiv:hep-th/0601001",
            )
        ).scalar_one_or_none()
        assert cached is not None
