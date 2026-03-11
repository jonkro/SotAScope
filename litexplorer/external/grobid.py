"""GROBID PDF reference extraction client.

GROBID is a machine-learning library for extracting structured data from PDFs.
This client communicates with a locally-running GROBID instance over HTTP.

Run GROBID via Docker:
    docker run --rm -p 8070:8070 lfoppiano/grobid:0.8.1

Configure the URL in Settings → grobid_url (e.g. http://localhost:8070).
Leave the setting empty to disable GROBID integration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


class GrobidError(Exception):
    """Raised when the GROBID client encounters an unrecoverable error."""


@dataclass
class GrobidReference:
    """A single bibliographic reference extracted by GROBID."""

    title: str | None = None
    authors: list[str] = field(default_factory=list)
    doi: str | None = None
    arxiv_id: str | None = None
    journal: str | None = None
    volume: str | None = None
    pages: str | None = None
    year: str | None = None
    raw_string: str | None = None


class GrobidClient:
    """HTTP client for a locally-running GROBID instance.

    Parameters
    ----------
    base_url:
        Base URL of the GROBID service, e.g. ``http://localhost:8070``.
        Must not have a trailing slash.
    ssl_verify:
        Whether to verify TLS certificates.  Set to ``False`` when behind
        a corporate HTTPS proxy with a custom CA.
    """

    def __init__(self, base_url: str, ssl_verify: bool = True) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            headers={"User-Agent": "LitExplorer/0.1"},
            verify=ssl_verify,
            timeout=120.0,  # reference extraction can be slow for large PDFs
        )

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_health(self) -> bool:
        """Return True if the GROBID service is reachable and alive."""
        try:
            resp = self._http.get(
                f"{self._base_url}/api/isalive",
                timeout=3.0,
            )
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError):
            return False

    def extract_references(self, pdf_bytes: bytes) -> list[GrobidReference]:
        """Extract bibliographic references from a PDF.

        Sends the PDF to GROBID's ``processReferences`` endpoint and parses
        the returned TEI XML via ``grobid_tei_xml``.

        Parameters
        ----------
        pdf_bytes:
            Raw bytes of the PDF file.

        Returns
        -------
        list[GrobidReference]
            Parsed references.  Empty list if GROBID found none.

        Raises
        ------
        GrobidError
            On connection failure, timeout, or a non-200 HTTP response.
        """
        try:
            resp = self._http.post(
                f"{self._base_url}/api/processReferences",
                files={"input": ("document.pdf", pdf_bytes, "application/pdf")},
                data={
                    "consolidateCitations": "0",
                    "includeRawCitations": "1",
                },
            )
        except httpx.TimeoutException as exc:
            raise GrobidError(
                f"GROBID request timed out: {exc}"
            ) from exc
        except (httpx.ConnectError, httpx.RequestError) as exc:
            raise GrobidError(
                f"Failed to connect to GROBID at {self._base_url}: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise GrobidError(
                f"GROBID returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

        return _parse_tei_xml(resp.text)


# ------------------------------------------------------------------
# TEI XML parsing helpers
# ------------------------------------------------------------------

def _parse_tei_xml(xml_text: str) -> list[GrobidReference]:
    """Parse a GROBID TEI XML response into a list of GrobidReference objects."""
    try:
        import grobid_tei_xml  # type: ignore[import]
    except ImportError as exc:
        raise GrobidError(
            "grobid-tei-xml is not installed. "
            "Run: pip install grobid-tei-xml"
        ) from exc

    try:
        citations = grobid_tei_xml.parse_citations_xml(xml_text)
    except Exception as exc:
        raise GrobidError(f"Failed to parse GROBID TEI XML: {exc}") from exc

    results: list[GrobidReference] = []
    for bib in (citations or []):
        results.append(_biblio_to_reference(bib))
    return results


def _biblio_to_reference(bib) -> GrobidReference:  # type: ignore[no-untyped-def]
    """Convert a ``grobid_tei_xml.GrobidBiblio`` object to a ``GrobidReference``."""
    # Authors: prefer full_name; fall back to building from given + surname
    authors: list[str] = []
    for author in (getattr(bib, "authors", None) or []):
        full = getattr(author, "full_name", None)
        if full:
            authors.append(full.strip())
        else:
            given = getattr(author, "given_name", None) or ""
            surname = getattr(author, "surname", None) or ""
            name = f"{given} {surname}".strip()
            if name:
                authors.append(name)

    # Year: grobid_tei_xml stores it as an int or None
    year_raw = getattr(bib, "date", None)
    year = str(year_raw) if year_raw is not None else None

    # Pages: may be stored as "first_page-last_page" or a single value
    pages: str | None = None
    first_page = getattr(bib, "first_page", None)
    last_page = getattr(bib, "last_page", None)
    if first_page and last_page:
        pages = f"{first_page}-{last_page}"
    elif first_page:
        pages = str(first_page)

    return GrobidReference(
        title=getattr(bib, "title", None) or None,
        authors=authors,
        doi=getattr(bib, "doi", None) or None,
        arxiv_id=getattr(bib, "arxiv_id", None) or None,
        journal=getattr(bib, "journal", None) or None,
        volume=getattr(bib, "volume", None) or None,
        pages=pages,
        year=year,
        raw_string=getattr(bib, "unstructured", None) or None,
    )
