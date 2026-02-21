"""OpenAlex API client.

Handles HTTP transport, response parsing, abstract reconstruction,
DOI normalization, arXiv ID extraction, batch fetching, and cursor pagination.
"""

from __future__ import annotations

import logging
import re

import httpx

from litexplorer.external.base import (
    ExternalAuthor,
    ExternalLiteratureClient,
    ExternalLocation,
    ExternalVenue,
    ExternalWork,
)

logger = logging.getLogger(__name__)

_ARXIV_RE = re.compile(r"arxiv\.org/abs/(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
_DOI_PREFIX = "https://doi.org/"

# OpenAlex batch filter supports up to 50 IDs per request
_BATCH_CHUNK_SIZE = 50
_PER_PAGE = 200


def _normalize_doi(raw: str | None) -> str | None:
    """Strip the https://doi.org/ prefix if present; lowercase."""
    if not raw:
        return None
    doi = raw.removeprefix(_DOI_PREFIX).strip()
    return doi.lower() if doi else None


def _extract_arxiv_id(locations: list[dict]) -> str | None:
    """Scan OpenAlex locations for an arXiv URL and extract the ID."""
    for loc in locations:
        for url_field in ("landing_page_url", "pdf_url"):
            url = loc.get(url_field) or ""
            m = _ARXIV_RE.search(url)
            if m:
                return m.group(1)
    return None


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """Reconstruct plaintext abstract from OpenAlex's inverted index format."""
    if not inverted_index:
        return None
    # Build (position, word) pairs and sort by position
    pairs: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            pairs.append((pos, word))
    if not pairs:
        return None
    pairs.sort(key=lambda p: p[0])
    return " ".join(word for _, word in pairs)


def parse_work(raw: dict) -> ExternalWork:
    """Parse a single OpenAlex work JSON dict into an ExternalWork.

    This is a public function so the enrichment service can re-parse cached
    raw JSON without going through the HTTP client.
    """
    # External ID: strip the URL prefix to get just "W1234567890"
    openalex_id = raw.get("id", "")
    if openalex_id.startswith("https://openalex.org/"):
        openalex_id = openalex_id.removeprefix("https://openalex.org/")

    # DOI
    doi = _normalize_doi(raw.get("doi"))

    # arXiv ID from locations
    raw_locations = raw.get("locations") or []
    arxiv_id = _extract_arxiv_id(raw_locations)

    # Abstract
    abstract = _reconstruct_abstract(raw.get("abstract_inverted_index"))

    # Venue / source
    venue = None
    primary_location = raw.get("primary_location") or {}
    source = primary_location.get("source") or {}
    if source.get("display_name"):
        source_id = (source.get("id") or "").removeprefix("https://openalex.org/")
        source_type = source.get("type")
        venue_type = None
        if source_type == "journal":
            venue_type = "journal"
        elif source_type in ("conference", "repository"):
            venue_type = source_type
        venue = ExternalVenue(
            name=source["display_name"],
            external_id=source_id or None,
            venue_type=venue_type,
        )

    # Authors
    authors: list[ExternalAuthor] = []
    for authorship in raw.get("authorships") or []:
        author_info = authorship.get("author") or {}
        name = author_info.get("display_name")
        if not name:
            continue
        ext_id = (author_info.get("id") or "").removeprefix("https://openalex.org/")
        authors.append(ExternalAuthor(name=name, external_id=ext_id or None))

    # Locations
    locations: list[ExternalLocation] = []
    for loc in raw_locations:
        url = loc.get("landing_page_url") or loc.get("pdf_url")
        if not url:
            continue
        is_primary = bool(loc.get("is_primary"))
        loc_type = "preprint" if "arxiv.org" in url.lower() else "venue"
        locations.append(ExternalLocation(url=url, location_type=loc_type, is_primary=is_primary))

    # Referenced works (backward citation IDs)
    referenced_work_ids: list[str] = []
    for ref_url in raw.get("referenced_works") or []:
        ref_id = ref_url.removeprefix("https://openalex.org/")
        if ref_id:
            referenced_work_ids.append(ref_id)

    # Per-year citation counts
    raw_counts = raw.get("counts_by_year")
    citations_by_year = None
    if raw_counts and isinstance(raw_counts, list):
        citations_by_year = [
            {"year": entry["year"], "cited_by_count": entry["cited_by_count"]}
            for entry in raw_counts
            if isinstance(entry, dict) and "year" in entry and "cited_by_count" in entry
        ]

    return ExternalWork(
        title=raw.get("title") or raw.get("display_name") or "(untitled)",
        external_id=openalex_id or None,
        doi=doi,
        arxiv_id=arxiv_id,
        abstract=abstract,
        publication_year=raw.get("publication_year"),
        citation_count=raw.get("cited_by_count"),
        citations_by_year=citations_by_year,
        venue=venue,
        authors=authors,
        locations=locations,
        referenced_work_ids=referenced_work_ids,
    )


class OpenAlexClient(ExternalLiteratureClient):
    """Synchronous OpenAlex API client using httpx."""

    def __init__(self, base_url: str = "https://api.openalex.org", api_key: str | None = None):
        headers = {"User-Agent": f"LitExplorer/0.1 (mailto:{api_key or 'litexplorer@local'})"}
        params = {}
        if api_key:
            params["mailto"] = api_key
        self._http = httpx.Client(base_url=base_url, headers=headers, params=params, timeout=30.0)

    def close(self):
        self._http.close()

    # -- Public API -----------------------------------------------------------

    def get_work_by_doi(self, doi: str) -> ExternalWork | None:
        """Fetch a single work by DOI. Returns None if not found."""
        resp = self._http.get(f"/works/doi:{doi}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return parse_work(resp.json())

    def get_work_by_doi_raw(self, doi: str) -> dict | None:
        """Fetch raw JSON for a single work by DOI. Returns None if not found."""
        resp = self._http.get(f"/works/doi:{doi}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def get_work_by_id_raw(self, openalex_id: str) -> dict | None:
        """Fetch raw JSON for a single work by its OpenAlex ID. Returns None if not found."""
        resp = self._http.get(f"/works/{openalex_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def get_works_by_ids(self, external_ids: list[str]) -> list[ExternalWork]:
        """Fetch multiple works by OpenAlex IDs in batches of 50."""
        results: list[ExternalWork] = []
        for i in range(0, len(external_ids), _BATCH_CHUNK_SIZE):
            chunk = external_ids[i : i + _BATCH_CHUNK_SIZE]
            pipe_filter = "|".join(chunk)
            resp = self._http.get(
                "/works",
                params={"filter": f"openalex_id:{pipe_filter}", "per-page": _PER_PAGE},
            )
            resp.raise_for_status()
            for item in resp.json().get("results", []):
                results.append(parse_work(item))
        return results

    def get_works_by_ids_raw(self, external_ids: list[str]) -> list[dict]:
        """Fetch raw JSON for multiple works by OpenAlex IDs in batches of 50."""
        results: list[dict] = []
        for i in range(0, len(external_ids), _BATCH_CHUNK_SIZE):
            chunk = external_ids[i : i + _BATCH_CHUNK_SIZE]
            pipe_filter = "|".join(chunk)
            resp = self._http.get(
                "/works",
                params={"filter": f"openalex_id:{pipe_filter}", "per-page": _PER_PAGE},
            )
            resp.raise_for_status()
            results.extend(resp.json().get("results", []))
        return results

    def get_forward_citations(self, external_id: str) -> list[ExternalWork]:
        """Fetch all works citing the given work, using cursor pagination."""
        results: list[ExternalWork] = []
        cursor = "*"
        while cursor:
            resp = self._http.get(
                "/works",
                params={
                    "filter": f"cites:{external_id}",
                    "per-page": _PER_PAGE,
                    "cursor": cursor,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("results", []):
                results.append(parse_work(item))
            cursor = data.get("meta", {}).get("next_cursor")
        return results

    def get_forward_citations_raw(self, external_id: str) -> list[dict]:
        """Fetch all raw JSON for works citing the given work, cursor-paginated."""
        results: list[dict] = []
        cursor = "*"
        while cursor:
            resp = self._http.get(
                "/works",
                params={
                    "filter": f"cites:{external_id}",
                    "per-page": _PER_PAGE,
                    "cursor": cursor,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("results", []))
            cursor = data.get("meta", {}).get("next_cursor")
        return results
