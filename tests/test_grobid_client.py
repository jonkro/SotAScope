"""Tests for GrobidClient (litexplorer/external/grobid.py)."""

import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import httpx
import pytest

from litexplorer.external.grobid import GrobidClient, GrobidError, GrobidReference

# ---------------------------------------------------------------------------
# Realistic TEI XML fixture (mirrors a GROBID processReferences response)
# ---------------------------------------------------------------------------

# Four entries:
#   1. Full metadata — DOI, two authors (full_name), journal, volume, pages, year, raw
#   2. arXiv preprint — arXiv ID, one author, year, no DOI
#   3. Title/authors/journal only — no identifiers; authors expressed as given+surname
#   4. Minimal/broken — no title or IDs, only a raw citation string

TEI_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text>
    <back>
      <div type="references">
        <listBibl>
          <biblStruct>
            <analytic>
              <title level="a" type="main">Attention Is All You Need</title>
              <author>
                <persName><forename type="first">Ashish</forename><surname>Vaswani</surname></persName>
              </author>
              <author>
                <persName><forename type="first">Noam</forename><surname>Shazeer</surname></persName>
              </author>
              <idno type="DOI">10.48550/arXiv.1706.03762</idno>
            </analytic>
            <monogr>
              <title level="j">Advances in Neural Information Processing Systems</title>
              <imprint>
                <biblScope unit="volume">30</biblScope>
                <biblScope unit="page" from="5998" to="6008"/>
                <date type="published" when="2017"/>
              </imprint>
            </monogr>
            <note type="raw_reference">Vaswani et al. 2017 NeurIPS</note>
          </biblStruct>
          <biblStruct>
            <analytic>
              <title level="a" type="main">BERT: Pre-training of Deep Bidirectional Transformers</title>
              <author>
                <persName><forename type="first">Jacob</forename><surname>Devlin</surname></persName>
              </author>
              <idno type="arXiv">arXiv:1810.04805</idno>
            </analytic>
            <monogr>
              <title level="j">arXiv preprint</title>
              <imprint>
                <date type="published" when="2018"/>
              </imprint>
            </monogr>
          </biblStruct>
          <biblStruct>
            <analytic>
              <title level="a" type="main">Deep Residual Learning for Image Recognition</title>
              <author>
                <persName><forename type="first">Kaiming</forename><surname>He</surname></persName>
              </author>
              <author>
                <persName><forename type="first">Xiangyu</forename><surname>Zhang</surname></persName>
              </author>
            </analytic>
            <monogr>
              <title level="j">IEEE Conference on Computer Vision and Pattern Recognition</title>
              <imprint>
                <date type="published" when="2016"/>
              </imprint>
            </monogr>
          </biblStruct>
          <biblStruct>
            <note type="raw_reference">Broken reference with no structured data available.</note>
          </biblStruct>
        </listBibl>
      </div>
    </back>
  </text>
</TEI>
"""

# ---------------------------------------------------------------------------
# Mock helpers — build lightweight stand-ins for grobid_tei_xml objects
# ---------------------------------------------------------------------------

def _make_author(full_name=None, given_name=None, surname=None):
    """Return a mock author object whose attributes mirror GrobidBiblio authors."""
    a = MagicMock()
    a.full_name = full_name
    a.given_name = given_name
    a.surname = surname
    return a


def _make_biblio(
    title=None,
    authors=None,
    doi=None,
    arxiv_id=None,
    journal=None,
    volume=None,
    first_page=None,
    last_page=None,
    date=None,
    unstructured=None,
):
    """Return a mock GrobidBiblio object with explicitly set fields.

    All attributes are set explicitly so that MagicMock's default of returning
    a truthy MagicMock for unknown attributes does not corrupt the mapping logic.
    """
    b = MagicMock()
    b.title = title
    b.authors = authors if authors is not None else []
    b.doi = doi
    b.arxiv_id = arxiv_id
    b.journal = journal
    b.volume = volume
    b.first_page = first_page
    b.last_page = last_page
    b.date = date
    b.unstructured = unstructured
    return b


def _make_doc(citations):
    """Return a mock grobid_tei_xml GrobidDocument."""
    doc = MagicMock()
    doc.citations = citations
    return doc


def _http_ok(text=TEI_XML):
    """Return a mock 200 httpx.Response carrying *text*."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.text = text
    return resp


@contextmanager
def _mock_grobid_tei_xml(doc):
    """Inject a fake ``grobid_tei_xml`` module into sys.modules.

    ``_parse_tei_xml`` does ``import grobid_tei_xml`` *inside* the function, so
    ``patch("grobid_tei_xml.parse_document_xml")`` would try to import the real
    package (which may not be installed).  Instead we patch sys.modules directly
    so the lazy import resolves to our mock immediately.
    """
    fake_module = MagicMock()
    fake_module.parse_citations_xml.return_value = doc.citations
    with patch.dict(sys.modules, {"grobid_tei_xml": fake_module}):
        yield fake_module


def _http_error(status_code, text="error"):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    return resp


# Module-level citation list that mirrors TEI_XML so the two stay in sync.
def _four_entry_doc():
    entry1 = _make_biblio(
        title="Attention Is All You Need",
        authors=[
            _make_author(full_name="Ashish Vaswani"),
            _make_author(full_name="Noam Shazeer"),
        ],
        doi="10.48550/arXiv.1706.03762",
        journal="Advances in Neural Information Processing Systems",
        volume="30",
        first_page="5998",
        last_page="6008",
        date=2017,
        unstructured="Vaswani et al. 2017 NeurIPS",
    )
    entry2 = _make_biblio(
        title="BERT: Pre-training of Deep Bidirectional Transformers",
        authors=[_make_author(full_name="Jacob Devlin")],
        arxiv_id="1810.04805",
        journal="arXiv preprint",
        date=2018,
    )
    entry3 = _make_biblio(
        title="Deep Residual Learning for Image Recognition",
        authors=[
            _make_author(given_name="Kaiming", surname="He"),
            _make_author(given_name="Xiangyu", surname="Zhang"),
        ],
        journal="IEEE Conference on Computer Vision and Pattern Recognition",
        date=2016,
    )
    entry4 = _make_biblio(
        unstructured="Broken reference with no structured data available.",
    )
    return _make_doc([entry1, entry2, entry3, entry4])


# ---------------------------------------------------------------------------
# check_health()
# ---------------------------------------------------------------------------


class TestCheckHealth:
    def test_returns_true_on_200(self):
        with patch.object(httpx.Client, "get", return_value=_http_ok(text="OK")):
            client = GrobidClient("http://localhost:8070")
            assert client.check_health() is True
            client.close()

    def test_returns_false_on_connect_error(self):
        with patch.object(
            httpx.Client, "get", side_effect=httpx.ConnectError("Connection refused")
        ):
            client = GrobidClient("http://localhost:8070")
            assert client.check_health() is False
            client.close()

    def test_returns_false_on_timeout(self):
        with patch.object(
            httpx.Client, "get", side_effect=httpx.ReadTimeout("Timed out")
        ):
            client = GrobidClient("http://localhost:8070")
            assert client.check_health() is False
            client.close()

    def test_returns_false_on_non_200(self):
        with patch.object(
            httpx.Client, "get", return_value=_http_error(503, "Service Unavailable")
        ):
            client = GrobidClient("http://localhost:8070")
            assert client.check_health() is False
            client.close()

    def test_calls_isalive_endpoint(self):
        with patch.object(
            httpx.Client, "get", return_value=_http_ok(text="OK")
        ) as mock_get:
            client = GrobidClient("http://localhost:8070")
            client.check_health()
            client.close()

        mock_get.assert_called_once_with(
            "http://localhost:8070/api/isalive", timeout=3.0
        )

    def test_trailing_slash_stripped_from_base_url(self):
        """A trailing slash on the configured URL must not produce a double-slash."""
        with patch.object(
            httpx.Client, "get", return_value=_http_ok(text="OK")
        ) as mock_get:
            client = GrobidClient("http://localhost:8070/")
            client.check_health()
            client.close()

        called_url = mock_get.call_args[0][0]
        assert "//api" not in called_url
        assert called_url.endswith("/api/isalive")


# ---------------------------------------------------------------------------
# extract_references() — happy path
# ---------------------------------------------------------------------------


class TestExtractReferences:
    """Tests for the full HTTP→parse→map pipeline of extract_references()."""

    def _extract(self, doc):
        """Convenience: mock HTTP layer and grobid_tei_xml, return GrobidReference list."""
        with (
            patch.object(httpx.Client, "post", return_value=_http_ok()),
            _mock_grobid_tei_xml(doc),
        ):
            client = GrobidClient("http://localhost:8070")
            refs = client.extract_references(b"%PDF-1.4 fake content")
            client.close()
        return refs

    def test_returns_all_four_entries(self):
        refs = self._extract(_four_entry_doc())
        assert len(refs) == 4

    def test_all_results_are_grobid_reference_instances(self):
        refs = self._extract(_four_entry_doc())
        assert all(isinstance(r, GrobidReference) for r in refs)

    def test_entry_with_doi_fields(self):
        """Entry 1: DOI, two full-name authors, journal, volume, page range, year, raw."""
        refs = self._extract(_four_entry_doc())
        r = refs[0]
        assert r.title == "Attention Is All You Need"
        assert r.doi == "10.48550/arXiv.1706.03762"
        assert r.arxiv_id is None
        assert r.authors == ["Ashish Vaswani", "Noam Shazeer"]
        assert r.journal == "Advances in Neural Information Processing Systems"
        assert r.volume == "30"
        assert r.pages == "5998-6008"
        assert r.year == "2017"
        assert r.raw_string == "Vaswani et al. 2017 NeurIPS"

    def test_entry_with_arxiv_id(self):
        """Entry 2: arXiv ID present, no DOI, single author."""
        refs = self._extract(_four_entry_doc())
        r = refs[1]
        assert r.title == "BERT: Pre-training of Deep Bidirectional Transformers"
        assert r.arxiv_id == "1810.04805"
        assert r.doi is None
        assert r.authors == ["Jacob Devlin"]
        assert r.year == "2018"
        assert r.raw_string is None

    def test_entry_with_given_surname_authors(self):
        """Entry 3: authors have given_name + surname but no full_name — must be joined."""
        refs = self._extract(_four_entry_doc())
        r = refs[2]
        assert r.title == "Deep Residual Learning for Image Recognition"
        assert r.doi is None
        assert r.arxiv_id is None
        assert r.authors == ["Kaiming He", "Xiangyu Zhang"]
        assert r.journal == "IEEE Conference on Computer Vision and Pattern Recognition"
        assert r.year == "2016"
        assert r.pages is None

    def test_minimal_broken_entry(self):
        """Entry 4: no title or identifiers; only raw_string survives."""
        refs = self._extract(_four_entry_doc())
        r = refs[3]
        assert r.title is None
        assert r.doi is None
        assert r.arxiv_id is None
        assert r.authors == []
        assert r.year is None
        assert r.raw_string == "Broken reference with no structured data available."

    def test_empty_citation_list_returns_empty_list(self):
        refs = self._extract(_make_doc([]))
        assert refs == []

    def test_single_page_no_last_page(self):
        """When last_page is absent, pages should be just the first_page string."""
        single = _make_biblio(title="A Paper", first_page="42")
        refs = self._extract(_make_doc([single]))
        assert refs[0].pages == "42"

    def test_year_is_stringified_int(self):
        """grobid_tei_xml stores dates as ints; GrobidReference.year must be a str."""
        entry = _make_biblio(title="Test", date=2023)
        refs = self._extract(_make_doc([entry]))
        assert refs[0].year == "2023"

    def test_sends_pdf_to_processreferences_endpoint(self):
        pdf_bytes = b"%PDF-1.4 test content"
        with (
            patch.object(httpx.Client, "post", return_value=_http_ok()) as mock_post,
            _mock_grobid_tei_xml(_make_doc([])),
        ):
            client = GrobidClient("http://localhost:8070")
            client.extract_references(pdf_bytes)
            client.close()

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "http://localhost:8070/api/processReferences"
        _fname, uploaded_bytes, content_type = kwargs["files"]["input"]
        assert uploaded_bytes is pdf_bytes
        assert content_type == "application/pdf"
        assert kwargs["data"]["includeRawCitations"] == "1"

    def test_xml_text_forwarded_to_parser(self):
        """The exact text from the HTTP response must be passed to grobid_tei_xml."""
        with (
            patch.object(httpx.Client, "post", return_value=_http_ok()),
            _mock_grobid_tei_xml(_make_doc([])) as fake_module,
        ):
            client = GrobidClient("http://localhost:8070")
            client.extract_references(b"PDF")
            client.close()

        fake_module.parse_citations_xml.assert_called_once_with(TEI_XML)


# ---------------------------------------------------------------------------
# extract_references() — error paths
# ---------------------------------------------------------------------------


class TestExtractReferencesErrors:
    def test_raises_grobid_error_on_503(self):
        with patch.object(
            httpx.Client, "post", return_value=_http_error(503, "Service Unavailable")
        ):
            client = GrobidClient("http://localhost:8070")
            with pytest.raises(GrobidError, match="503"):
                client.extract_references(b"PDF")
            client.close()

    def test_raises_grobid_error_on_422(self):
        """Any non-200 status code should raise GrobidError mentioning the code."""
        with patch.object(
            httpx.Client, "post", return_value=_http_error(422, "Unprocessable Entity")
        ):
            client = GrobidClient("http://localhost:8070")
            with pytest.raises(GrobidError, match="422"):
                client.extract_references(b"PDF")
            client.close()

    def test_raises_grobid_error_on_timeout(self):
        with patch.object(
            httpx.Client, "post", side_effect=httpx.ReadTimeout("Read timed out")
        ):
            client = GrobidClient("http://localhost:8070")
            with pytest.raises(GrobidError, match="timed out"):
                client.extract_references(b"PDF")
            client.close()

    def test_raises_grobid_error_on_connect_error(self):
        with patch.object(
            httpx.Client, "post", side_effect=httpx.ConnectError("Connection refused")
        ):
            client = GrobidClient("http://localhost:8070")
            with pytest.raises(GrobidError, match="Failed to connect"):
                client.extract_references(b"PDF")
            client.close()

    def test_error_message_includes_base_url(self):
        """The ConnectError message must identify which server was unreachable."""
        with patch.object(
            httpx.Client,
            "post",
            side_effect=httpx.ConnectError("refused"),
        ):
            client = GrobidClient("http://localhost:8070")
            with pytest.raises(GrobidError, match="localhost:8070"):
                client.extract_references(b"PDF")
            client.close()

    def test_error_message_includes_response_body(self):
        """GrobidError for a non-200 response should include (a prefix of) the body."""
        with patch.object(
            httpx.Client,
            "post",
            return_value=_http_error(503, "Service is down for maintenance"),
        ):
            client = GrobidClient("http://localhost:8070")
            with pytest.raises(GrobidError, match="Service is down"):
                client.extract_references(b"PDF")
            client.close()
