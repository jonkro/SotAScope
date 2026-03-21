"""Unit tests for the OpenAlex HTTP client (mocked httpx)."""

from unittest.mock import MagicMock

import httpx
import pytest

from sotascope.external.openalex import OpenAlexClient, parse_work
from tests.fixtures.openalex_responses import (
    SAMPLE_BATCH_RESPONSE,
    SAMPLE_FORWARD_CITATIONS_PAGE1,
    SAMPLE_FORWARD_CITATIONS_PAGE2,
    SAMPLE_FORWARD_CITATIONS_RESPONSE,
    SAMPLE_STUB_WORK_RAW,
    SAMPLE_WORK_RAW,
)


# ---------------------------------------------------------------------------
# parse_work unit tests (no HTTP)
# ---------------------------------------------------------------------------

class TestParseWork:
    def test_basic_fields(self):
        w = parse_work(SAMPLE_WORK_RAW)
        assert w.external_id == "W2963073370"
        assert w.doi == "10.1145/3230543.3230563"
        assert w.title == "Restructuring endpoint congestion control"
        assert w.publication_year == 2018
        assert w.citation_count == 85

    def test_doi_normalized(self):
        w = parse_work(SAMPLE_WORK_RAW)
        # Should not contain the https://doi.org/ prefix
        assert not w.doi.startswith("https://")
        assert w.doi == "10.1145/3230543.3230563"

    def test_abstract_reconstruction(self):
        w = parse_work(SAMPLE_WORK_RAW)
        assert w.abstract is not None
        assert w.abstract.startswith("We present a new approach")
        assert "congestion control framework." in w.abstract

    def test_no_abstract(self):
        w = parse_work(SAMPLE_STUB_WORK_RAW)
        assert w.abstract is None

    def test_arxiv_id_extraction(self):
        w = parse_work(SAMPLE_WORK_RAW)
        assert w.arxiv_id == "1810.03259"

    def test_no_arxiv_id(self):
        w = parse_work(SAMPLE_STUB_WORK_RAW)
        assert w.arxiv_id is None

    def test_venue_parsing(self):
        w = parse_work(SAMPLE_WORK_RAW)
        assert w.venue is not None
        assert w.venue.name == "ACM SIGCOMM"
        assert w.venue.external_id == "S1234567"
        assert w.venue.venue_type == "conference"

    def test_authors(self):
        w = parse_work(SAMPLE_WORK_RAW)
        assert len(w.authors) == 2
        assert w.authors[0].name == "Akshay Narayan"
        assert w.authors[0].external_id == "A111"

    def test_locations(self):
        w = parse_work(SAMPLE_WORK_RAW)
        assert len(w.locations) == 2
        venue_locs = [l for l in w.locations if l.location_type == "venue"]
        preprint_locs = [l for l in w.locations if l.location_type == "preprint"]
        assert len(venue_locs) == 1
        assert len(preprint_locs) == 1
        assert venue_locs[0].is_primary is True

    def test_referenced_works(self):
        w = parse_work(SAMPLE_WORK_RAW)
        assert len(w.referenced_work_ids) == 3
        assert "W1000000001" in w.referenced_work_ids

    def test_stub_work(self):
        w = parse_work(SAMPLE_STUB_WORK_RAW)
        assert w.external_id == "W9999999999"
        assert w.doi is None
        assert w.venue is None
        assert len(w.authors) == 0


# ---------------------------------------------------------------------------
# OpenAlexClient tests (mock httpx)
# ---------------------------------------------------------------------------

def _make_response(status_code: int, json_data: dict) -> httpx.Response:
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp,
        )
    return resp


class TestOpenAlexClient:
    @pytest.fixture()
    def client(self):
        c = OpenAlexClient(api_key="test-key")
        yield c
        c.close()

    def test_get_work_by_doi(self, client):
        client._http.get = MagicMock(return_value=_make_response(200, SAMPLE_WORK_RAW))
        work = client.get_work_by_doi("10.1145/3230543.3230563")
        assert work is not None
        assert work.doi == "10.1145/3230543.3230563"
        client._http.get.assert_called_once_with("/works/doi:10.1145/3230543.3230563")

    def test_get_work_by_doi_not_found(self, client):
        client._http.get = MagicMock(return_value=_make_response(404, {}))
        work = client.get_work_by_doi("10.9999/nonexistent")
        assert work is None

    def test_get_works_by_ids(self, client):
        client._http.get = MagicMock(return_value=_make_response(200, SAMPLE_BATCH_RESPONSE))
        works = client.get_works_by_ids(["W1000000001", "W9999999999"])
        assert len(works) == 2
        call_kwargs = client._http.get.call_args
        assert "openalex_id:" in call_kwargs.kwargs["params"]["filter"]

    def test_get_forward_citations_single_page(self, client):
        client._http.get = MagicMock(
            return_value=_make_response(200, SAMPLE_FORWARD_CITATIONS_RESPONSE)
        )
        works = client.get_forward_citations("W2963073370")
        assert len(works) == 1
        assert works[0].external_id == "W9999999999"

    def test_get_forward_citations_pagination(self, client):
        # First call returns page1 with next_cursor, second returns page2 without
        client._http.get = MagicMock(
            side_effect=[
                _make_response(200, SAMPLE_FORWARD_CITATIONS_PAGE1),
                _make_response(200, SAMPLE_FORWARD_CITATIONS_PAGE2),
            ]
        )
        works = client.get_forward_citations("W2963073370")
        assert len(works) == 2
        assert client._http.get.call_count == 2

