"""Crossref API client.

Handles DOI resolution and authoritative venue metadata enrichment.
Does NOT extend ExternalLiteratureClient — Crossref's role (DOI lookup +
venue metadata) is fundamentally different from OpenAlex's citation graph.
"""

from __future__ import annotations

import logging

import httpx

from litexplorer.external.base import (
    ExternalAuthor,
    ExternalVenue,
    ExternalWork,
)

logger = logging.getLogger(__name__)

_DOI_PREFIX = "https://doi.org/"

# Crossref type -> our venue_type
_TYPE_MAP: dict[str, str] = {
    "journal-article": "journal",
    "proceedings-article": "conference",
}


def _normalize_doi(raw: str | None) -> str | None:
    """Strip the https://doi.org/ prefix if present; lowercase."""
    if not raw:
        return None
    doi = raw.removeprefix(_DOI_PREFIX).strip()
    return doi.lower() if doi else None


def parse_crossref_work(raw: dict) -> ExternalWork:
    """Parse a Crossref work message dict into an ExternalWork."""
    # DOI
    doi = _normalize_doi(raw.get("DOI"))

    # Title
    titles = raw.get("title") or []
    title = titles[0] if titles else "(untitled)"

    # Publication year from issued.date-parts
    pub_year = None
    issued = raw.get("issued") or {}
    date_parts = issued.get("date-parts") or []
    if date_parts and date_parts[0] and date_parts[0][0]:
        pub_year = date_parts[0][0]

    # Citation count
    citation_count = raw.get("is-referenced-by-count")

    # Venue
    venue = None
    container_titles = raw.get("container-title") or []
    venue_name = container_titles[0] if container_titles else None
    if venue_name:
        issns = raw.get("ISSN") or []
        issn = issns[0] if issns else None
        publisher = raw.get("publisher")
        cr_type = raw.get("type") or ""
        venue_type = _TYPE_MAP.get(cr_type)
        venue = ExternalVenue(
            name=venue_name,
            venue_type=venue_type,
            issn=issn,
            publisher=publisher,
        )

    # Authors
    authors: list[ExternalAuthor] = []
    for author_raw in raw.get("author") or []:
        given = author_raw.get("given") or ""
        family = author_raw.get("family") or ""
        name = f"{given} {family}".strip()
        if name:
            authors.append(ExternalAuthor(name=name))

    # Abstract (some Crossref records include it as HTML-ish text)
    abstract = raw.get("abstract")

    return ExternalWork(
        title=title,
        doi=doi,
        abstract=abstract,
        publication_year=pub_year,
        citation_count=citation_count,
        venue=venue,
        authors=authors,
    )


class CrossrefClient:
    """Synchronous Crossref API client using httpx."""

    def __init__(
        self,
        base_url: str = "https://api.crossref.org",
        mailto: str | None = None,
    ):
        ua = "LitExplorer/0.1"
        if mailto:
            ua += f" (mailto:{mailto})"
        self._http = httpx.Client(
            base_url=base_url,
            headers={"User-Agent": ua},
            timeout=30.0,
        )

    def close(self) -> None:
        self._http.close()

    def get_work_by_doi(self, doi: str) -> ExternalWork | None:
        """Fetch a single work by DOI. Returns None if not found."""
        raw = self.get_work_by_doi_raw(doi)
        if raw is None:
            return None
        return parse_crossref_work(raw)

    def get_work_by_doi_raw(self, doi: str) -> dict | None:
        """Fetch raw Crossref message dict for a DOI. Returns None if not found."""
        doi = doi.strip()
        resp = self._http.get(f"/works/{doi}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return data.get("message")
