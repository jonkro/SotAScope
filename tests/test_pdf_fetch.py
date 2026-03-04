"""Tests for the open-access PDF fetch client and API endpoint."""

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(
    status_code: int = 200,
    content: bytes = b"%PDF-1.4 fake",
    content_type: str = "application/pdf",
    json_data: dict | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = {"content-type": content_type}
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


# ---------------------------------------------------------------------------
# fetch_pdf_from_arxiv
# ---------------------------------------------------------------------------

class TestFetchPDFFromArxiv:
    def test_success_returns_bytes(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_from_arxiv

        mock_resp = _mock_response(content=b"%PDF-1.4 arxiv", content_type="application/pdf")
        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp
            result = fetch_pdf_from_arxiv("2401.00001")

        assert result == b"%PDF-1.4 arxiv"

    def test_404_returns_none(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_from_arxiv

        mock_resp = _mock_response(status_code=404, content=b"Not found", content_type="text/html")
        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp
            result = fetch_pdf_from_arxiv("9999.00000")

        assert result is None

    def test_wrong_content_type_returns_none(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_from_arxiv

        mock_resp = _mock_response(content=b"<html>", content_type="text/html")
        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp
            result = fetch_pdf_from_arxiv("2401.00001")

        assert result is None

    def test_timeout_returns_none(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_from_arxiv
        import httpx

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.side_effect = httpx.TimeoutException("timeout")
            result = fetch_pdf_from_arxiv("2401.00001")

        assert result is None


# ---------------------------------------------------------------------------
# fetch_pdf_url_from_unpaywall
# ---------------------------------------------------------------------------

class TestFetchPDFUrlFromUnpaywall:
    def test_oa_available_returns_url(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_url_from_unpaywall

        mock_resp = _mock_response(
            content_type="application/json",
            json_data={"best_oa_location": {"url_for_pdf": "https://example.com/paper.pdf", "url": None}},
        )
        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp
            result = fetch_pdf_url_from_unpaywall("10.1000/test", "user@example.com")

        assert result == "https://example.com/paper.pdf"

    def test_no_url_for_pdf_falls_back_to_url_ending_pdf(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_url_from_unpaywall

        mock_resp = _mock_response(
            content_type="application/json",
            json_data={"best_oa_location": {"url_for_pdf": None, "url": "https://example.com/doc.pdf"}},
        )
        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp
            result = fetch_pdf_url_from_unpaywall("10.1000/test", "user@example.com")

        assert result == "https://example.com/doc.pdf"

    def test_not_oa_returns_none(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_url_from_unpaywall

        mock_resp = _mock_response(
            content_type="application/json",
            json_data={"best_oa_location": None},
        )
        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp
            result = fetch_pdf_url_from_unpaywall("10.1000/closed", "user@example.com")

        assert result is None

    def test_url_not_ending_pdf_returns_none(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_url_from_unpaywall

        mock_resp = _mock_response(
            content_type="application/json",
            json_data={"best_oa_location": {"url_for_pdf": None, "url": "https://example.com/landing"}},
        )
        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp
            result = fetch_pdf_url_from_unpaywall("10.1000/test", "user@example.com")

        assert result is None


# ---------------------------------------------------------------------------
# fetch_pdf_from_url
# ---------------------------------------------------------------------------

class TestFetchPDFFromURL:
    def test_success_returns_bytes(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_from_url

        mock_resp = _mock_response(content=b"%PDF-1.4 ok", content_type="application/pdf")
        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp
            result = fetch_pdf_from_url("https://example.com/paper.pdf")

        assert result == b"%PDF-1.4 ok"

    def test_pdf_magic_bytes_accepted_without_content_type(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_from_url

        mock_resp = _mock_response(content=b"%PDF-1.4 bytes", content_type="application/octet-stream")
        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp
            result = fetch_pdf_from_url("https://example.com/paper.pdf")

        assert result == b"%PDF-1.4 bytes"

    def test_non_pdf_content_type_returns_none(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_from_url

        mock_resp = _mock_response(content=b"<html>not a pdf</html>", content_type="text/html")
        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp
            result = fetch_pdf_from_url("https://example.com/landing")

        assert result is None

    def test_timeout_returns_none(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_from_url
        import httpx

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.side_effect = httpx.TimeoutException("timeout")
            result = fetch_pdf_from_url("https://example.com/slow.pdf")

        assert result is None


# ---------------------------------------------------------------------------
# fetch_pdf_for_work
# ---------------------------------------------------------------------------

class TestFetchPDFForWork:
    def _make_work(self, arxiv_id=None, doi=None):
        work = MagicMock()
        work.arxiv_id = arxiv_id
        work.doi = doi
        return work

    def test_arxiv_path_succeeds(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_for_work

        work = self._make_work(arxiv_id="2401.00001")
        pdf_bytes = b"%PDF-1.4 arxiv"

        with patch("litexplorer.external.pdf_fetch.fetch_pdf_from_arxiv", return_value=pdf_bytes) as mock_arxiv:
            result = fetch_pdf_for_work(None, work)

        mock_arxiv.assert_called_once_with("2401.00001", verify=True)
        assert result is not None
        assert result[0] == pdf_bytes
        assert result[1].endswith(".pdf")

    def test_doi_only_path_succeeds(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_for_work

        work = self._make_work(doi="10.1000/test")
        pdf_bytes = b"%PDF-1.4 upw"

        with patch("litexplorer.external.pdf_fetch.fetch_pdf_from_arxiv") as mock_arxiv, \
             patch("litexplorer.external.pdf_fetch.fetch_pdf_url_from_unpaywall", return_value="https://ex.com/p.pdf") as mock_upw, \
             patch("litexplorer.external.pdf_fetch.fetch_pdf_from_url", return_value=pdf_bytes) as mock_url:
            result = fetch_pdf_for_work(None, work, email="u@example.com")

        mock_arxiv.assert_not_called()
        mock_upw.assert_called_once_with("10.1000/test", "u@example.com", verify=True)
        mock_url.assert_called_once_with("https://ex.com/p.pdf", verify=True)
        assert result is not None
        assert result[0] == pdf_bytes

    def test_neither_returns_none(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_for_work

        work = self._make_work()
        result = fetch_pdf_for_work(None, work)
        assert result is None

    def test_arxiv_fails_then_unpaywall_succeeds(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_for_work

        work = self._make_work(arxiv_id="2401.00001", doi="10.1000/test")
        pdf_bytes = b"%PDF-1.4 upw"

        with patch("litexplorer.external.pdf_fetch.fetch_pdf_from_arxiv", return_value=None), \
             patch("litexplorer.external.pdf_fetch.fetch_pdf_url_from_unpaywall", return_value="https://ex.com/p.pdf"), \
             patch("litexplorer.external.pdf_fetch.fetch_pdf_from_url", return_value=pdf_bytes):
            result = fetch_pdf_for_work(None, work, email="u@example.com")

        assert result is not None
        assert result[0] == pdf_bytes

    def test_url_found_but_download_fails_raises(self):
        from litexplorer.external.pdf_fetch import fetch_pdf_for_work, PDFFetchError

        work = self._make_work(doi="10.1000/test")

        with patch("litexplorer.external.pdf_fetch.fetch_pdf_url_from_unpaywall", return_value="https://ex.com/p.pdf"), \
             patch("litexplorer.external.pdf_fetch.fetch_pdf_from_url", return_value=None):
            with pytest.raises(PDFFetchError):
                fetch_pdf_for_work(None, work, email="u@example.com")


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def pdf_storage_dir(tmp_path, monkeypatch):
    storage = tmp_path / "pdfs"
    storage.mkdir()
    monkeypatch.setattr("litexplorer.api.works._get_pdf_root", lambda db: storage)
    return storage


@pytest.fixture()
def work_id(client):
    resp = client.post("/api/works", json={"title": "Test Paper"})
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.fixture()
def work_with_arxiv(client):
    resp = client.post("/api/works", json={"title": "ArXiv Paper", "arxiv_id": "2401.00001"})
    assert resp.status_code == 201
    return resp.json()["id"]


_FETCH_FN = "litexplorer.external.pdf_fetch.fetch_pdf_for_work"
_SSL_FN = "litexplorer.api.enrichment._get_ssl_verify"
_EMAIL_FN = "litexplorer.api.enrichment._get_contact_email"


class TestFetchPDFEndpoint:
    def test_success_creates_workpdf_and_triggers_extraction(
        self, client, work_with_arxiv, pdf_storage_dir
    ):
        pdf_bytes = b"%PDF-1.4 fetched"
        mock_result = (pdf_bytes, "2401.00001.pdf")

        with patch(_FETCH_FN, return_value=mock_result), \
             patch(_SSL_FN, return_value=True), \
             patch(_EMAIL_FN, return_value="test@example.com"):
            resp = client.post(f"/api/works/{work_with_arxiv}/pdfs/fetch")

        assert resp.status_code == 201
        data = resp.json()
        assert data["filename"] == "2401.00001.pdf"
        assert data["is_primary"] is True
        assert data["work_id"] == work_with_arxiv
        assert "extraction_status" in data

        # File saved to disk
        assert (pdf_storage_dir / str(work_with_arxiv) / "2401.00001.pdf").is_file()

    def test_second_fetch_not_primary(self, client, work_with_arxiv, pdf_storage_dir):
        pdf_bytes = b"%PDF-1.4 fetched"

        with patch(_FETCH_FN, return_value=(pdf_bytes, "first.pdf")), \
             patch(_SSL_FN, return_value=True), \
             patch(_EMAIL_FN, return_value=""):
            client.post(f"/api/works/{work_with_arxiv}/pdfs/fetch")

        with patch(_FETCH_FN, return_value=(pdf_bytes, "second.pdf")), \
             patch(_SSL_FN, return_value=True), \
             patch(_EMAIL_FN, return_value=""):
            resp = client.post(f"/api/works/{work_with_arxiv}/pdfs/fetch")

        assert resp.json()["is_primary"] is False

    def test_no_oa_pdf_returns_404(self, client, work_with_arxiv, pdf_storage_dir):
        with patch(_FETCH_FN, return_value=None), \
             patch(_SSL_FN, return_value=True), \
             patch(_EMAIL_FN, return_value=""):
            resp = client.post(f"/api/works/{work_with_arxiv}/pdfs/fetch")

        assert resp.status_code == 404
        assert "open-access" in resp.json()["detail"].lower()

    def test_download_failure_returns_502(self, client, work_with_arxiv, pdf_storage_dir):
        from litexplorer.external.pdf_fetch import PDFFetchError

        with patch(_FETCH_FN, side_effect=PDFFetchError("connection refused")), \
             patch(_SSL_FN, return_value=True), \
             patch(_EMAIL_FN, return_value=""):
            resp = client.post(f"/api/works/{work_with_arxiv}/pdfs/fetch")

        assert resp.status_code == 502
        assert "PDF download failed" in resp.json()["detail"]

    def test_no_arxiv_no_doi_returns_400(self, client, work_id, pdf_storage_dir):
        resp = client.post(f"/api/works/{work_id}/pdfs/fetch")
        assert resp.status_code == 400
        assert "arXiv" in resp.json()["detail"] or "DOI" in resp.json()["detail"]

    def test_work_not_found_returns_404(self, client, pdf_storage_dir):
        resp = client.post("/api/works/9999/pdfs/fetch")
        assert resp.status_code == 404
