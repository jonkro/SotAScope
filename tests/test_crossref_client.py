"""Unit tests for the Crossref client and response parsing."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from sotascope.external.crossref import CrossrefClient, parse_crossref_work
from tests.fixtures.crossref_responses import (
    SAMPLE_CROSSREF_JOURNAL_WORK,
    SAMPLE_CROSSREF_MINIMAL_WORK,
    SAMPLE_CROSSREF_RESPONSE,
    SAMPLE_CROSSREF_WORK,
)


# ---------------------------------------------------------------------------
# parse_crossref_work
# ---------------------------------------------------------------------------


class TestParseCrossrefWork:
    def test_conference_article(self):
        ext = parse_crossref_work(SAMPLE_CROSSREF_WORK)
        assert ext.title == "Restructuring endpoint congestion control"
        assert ext.doi == "10.1145/3230543.3230563"
        assert ext.publication_year == 2018
        assert ext.citation_count == 92
        assert ext.abstract == "<p>We present a new approach to congestion control.</p>"

        assert ext.venue is not None
        assert ext.venue.name == "Proceedings of the 2018 Conference of the ACM Special Interest Group on Data Communication"
        assert ext.venue.venue_type == "conference"
        assert ext.venue.issn == "0146-4833"
        assert ext.venue.publisher == "Association for Computing Machinery (ACM)"

        assert len(ext.authors) == 2
        assert ext.authors[0].name == "Akshay Narayan"
        assert ext.authors[1].name == "Frank Cangialosi"

    def test_journal_article(self):
        ext = parse_crossref_work(SAMPLE_CROSSREF_JOURNAL_WORK)
        assert ext.venue is not None
        assert ext.venue.venue_type == "journal"
        assert ext.venue.issn == "1063-6692"
        assert ext.venue.publisher == "Institute of Electrical and Electronics Engineers (IEEE)"
        assert ext.doi == "10.1109/tnet.2020.3027697"
        assert ext.publication_year == 2021

    def test_minimal_work(self):
        ext = parse_crossref_work(SAMPLE_CROSSREF_MINIMAL_WORK)
        assert ext.title == "A Minimal Entry"
        assert ext.doi == "10.9999/minimal.2023"
        assert ext.publication_year == 2023
        assert ext.citation_count == 0
        assert ext.venue is None  # no container-title
        assert ext.authors == []

    def test_empty_title_fallback(self):
        raw = {"DOI": "10.0/x", "title": [], "issued": {"date-parts": [[2020]]}}
        ext = parse_crossref_work(raw)
        assert ext.title == "(untitled)"

    def test_no_date_parts(self):
        raw = {"DOI": "10.0/y", "title": ["T"], "issued": {}}
        ext = parse_crossref_work(raw)
        assert ext.publication_year is None

    def test_doi_normalization(self):
        raw = {"DOI": "https://doi.org/10.1234/TEST.Upper", "title": ["T"]}
        ext = parse_crossref_work(raw)
        assert ext.doi == "10.1234/test.upper"


# ---------------------------------------------------------------------------
# CrossrefClient HTTP interactions
# ---------------------------------------------------------------------------


class TestCrossrefClient:
    def test_get_work_by_doi_success(self):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_CROSSREF_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch.object(httpx.Client, "get", return_value=mock_resp) as mock_get:
            client = CrossrefClient(base_url="https://api.crossref.org")
            result = client.get_work_by_doi("10.1145/3230543.3230563")
            client.close()

        mock_get.assert_called_once_with("/works/10.1145/3230543.3230563")
        assert result is not None
        assert result.doi == "10.1145/3230543.3230563"
        assert result.title == "Restructuring endpoint congestion control"

    def test_get_work_by_doi_not_found(self):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 404

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = CrossrefClient()
            result = client.get_work_by_doi("10.0000/nonexistent")
            client.close()

        assert result is None

    def test_get_work_by_doi_raw(self):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_CROSSREF_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = CrossrefClient()
            raw = client.get_work_by_doi_raw("10.1145/3230543.3230563")
            client.close()

        assert raw is not None
        assert raw["DOI"] == "10.1145/3230543.3230563"

    def test_user_agent_with_mailto(self):
        """Verify the polite pool User-Agent header is set when mailto is provided."""
        with patch.object(httpx.Client, "__init__", return_value=None) as mock_init:
            mock_init.return_value = None
            client = CrossrefClient.__new__(CrossrefClient)
            # Call init manually to inspect
            CrossrefClient.__init__(client, mailto="test@example.com")

        call_kwargs = mock_init.call_args[1]
        assert "mailto:test@example.com" in call_kwargs["headers"]["User-Agent"]

    def test_doi_whitespace_stripped(self):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 404

        with patch.object(httpx.Client, "get", return_value=mock_resp) as mock_get:
            client = CrossrefClient()
            client.get_work_by_doi("  10.1234/test  ")
            client.close()

        mock_get.assert_called_once_with("/works/10.1234/test")
