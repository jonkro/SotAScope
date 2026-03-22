"""Semantic Scholar Academic Graph API client.

Handles paper lookup by DOI or Semantic Scholar paper ID, and fetching
backward citations (references) and forward citations with cursor pagination.
"""

from __future__ import annotations

import logging
import threading
import time

import httpx

from sotascope.external.base import ExternalVenue, ExternalWork

logger = logging.getLogger(__name__)

_DOI_PREFIX = "https://doi.org/"

# Maximum items per paginated request (S2 limit)
_PAGE_SIZE = 500

# Fields requested for individual paper lookups
_PAPER_FIELDS = "paperId,corpusId,externalIds,title,year,citationCount,abstract,venue"
# Fields requested for bulk citation/reference lists (skip abstract to reduce payload)
_CITATION_FIELDS = "paperId,corpusId,externalIds,title,year,citationCount"

# ---------------------------------------------------------------------------
# Process-level rate limiter — shared across all SemanticScholarClient instances
# so that concurrent requests from different endpoint handlers don't exceed S2
# rate limits.  S2 enforces 1 req/s regardless of whether an API key is used;
# without a key the limit is applied globally across all users on the same IP.
# ---------------------------------------------------------------------------
_RATE_LOCK = threading.Lock()
_LAST_CALL_TIME: float = 0.0

# 1.1 requests per second — a small margin above S2's enforced 1 req/s limit
# to avoid triggering 429s right at the boundary.
_MIN_INTERVAL: float = 1.1


def _throttle(min_interval: float) -> None:
    """Block the calling thread until the minimum interval since the last S2
    call has elapsed, then record the new call time.  Thread-safe."""
    global _LAST_CALL_TIME
    with _RATE_LOCK:
        now = time.monotonic()
        wait = min_interval - (now - _LAST_CALL_TIME)
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL_TIME = time.monotonic()


def _normalize_doi(raw: str | None) -> str | None:
    """Strip the https://doi.org/ prefix if present; lowercase."""
    if not raw:
        return None
    doi = raw.removeprefix(_DOI_PREFIX).strip()
    return doi.lower() if doi else None


def _to_s2_paper_id(id_str: str) -> str:
    """Convert a stored semantic_scholar_id to the S2 API endpoint identifier.

    CorpusIds are stored as plain numeric strings (e.g. "123456789").
    S2 API endpoints require the ``CorpusId:`` prefix for numeric IDs.
    40-char hex SHA IDs and already-prefixed strings (e.g. ``DOI:``) pass through unchanged.
    """
    if id_str.isdigit():
        return f"CorpusId:{id_str}"
    return id_str


def _parse_paper(raw: dict) -> ExternalWork:
    """Parse a single Semantic Scholar paper dict into an ExternalWork.

    Stores the stable ``corpusId`` (numeric string) as ``semantic_scholar_id``
    when available, falling back to ``paperId`` (40-char SHA) for papers that
    S2 has not yet assigned a corpus ID.
    """
    corpus_id = raw.get("corpusId")
    paper_id = raw.get("paperId") or None
    ext_ids = raw.get("externalIds") or {}

    # Prefer corpusId (stable numeric) over paperId (SHA that can change)
    semantic_scholar_id = str(corpus_id) if corpus_id is not None else paper_id

    doi = _normalize_doi(ext_ids.get("DOI"))
    arxiv_id = ext_ids.get("ArXiv") or None

    # Venue: S2 provides a plain string name; no external_id or ISSN available.
    venue = None
    venue_name = (raw.get("venue") or "").strip()
    if venue_name:
        venue = ExternalVenue(name=venue_name)

    return ExternalWork(
        title=raw.get("title") or "(untitled)",
        doi=doi,
        arxiv_id=arxiv_id,
        semantic_scholar_id=semantic_scholar_id,
        abstract=raw.get("abstract"),
        publication_year=raw.get("year"),
        citation_count=raw.get("citationCount"),
        venue=venue,
    )


class SemanticScholarClient:
    """Synchronous Semantic Scholar Academic Graph API client.

    All outgoing HTTP calls are throttled via the module-level ``_throttle()``
    function at 1 req/s (S2's enforced limit regardless of API key).  The
    throttle is process-global so concurrent calls from different request
    handlers are serialised correctly.
    """

    def __init__(
        self,
        base_url: str = "https://api.semanticscholar.org/graph/v1",
        api_key: str | None = None,
        verify: bool = True,
    ):
        headers: dict[str, str] = {"User-Agent": "SotAScope/0.1"}
        if api_key:
            headers["x-api-key"] = api_key
        self._http = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=30.0,
            verify=verify,
        )
        self._min_interval = _MIN_INTERVAL

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Throttled HTTP call through the shared process-level rate limiter."""
        _throttle(self._min_interval)
        return self._http.request(method, path, **kwargs)

    # -- Public API -----------------------------------------------------------

    def get_paper(self, paper_id: str) -> ExternalWork | None:
        """Fetch a single paper by Semantic Scholar ID or prefixed identifier.

        Use "DOI:{doi}" or "ARXIV:{arxiv_id}" as paper_id for lookup by
        external identifier.  Returns None if not found.
        """
        resp = self._call("GET", f"/paper/{paper_id}", params={"fields": _PAPER_FIELDS})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return _parse_paper(resp.json())

    def get_paper_by_doi(self, doi: str) -> ExternalWork | None:
        """Fetch a paper by DOI. Returns None if not found."""
        return self.get_paper(f"DOI:{doi}")

    def get_paper_by_id(self, paper_id: str) -> ExternalWork | None:
        """Fetch a paper by its Semantic Scholar paper ID or CorpusId.

        Accepts both 40-char SHA IDs and numeric CorpusId strings (e.g. "123456789").
        Numeric IDs are automatically prefixed with ``CorpusId:`` for the API call.
        Returns None if not found.
        """
        return self.get_paper(_to_s2_paper_id(paper_id))

    def search_by_title(self, query: str, limit: int = 5, year: int | None = None) -> list[dict]:
        """Search for papers by title (or combined bibliographic query).

        Returns raw Semantic Scholar paper dicts including paperId, corpusId,
        externalIds, title, year, and authors.  Returns an empty list on 400/404/429.

        When *year* is provided it is passed as the ``year`` query parameter so
        that S2 restricts results to that publication year.  The year is NOT
        included in the free-text query string.
        """
        fields = "paperId,corpusId,externalIds,title,year,authors"
        params: dict = {"query": query, "fields": fields, "limit": limit}
        if year is not None:
            params["year"] = year
        resp = self._call("GET", "/paper/search", params=params)
        if resp.status_code in (400, 404):
            return []
        resp.raise_for_status()
        return resp.json().get("data") or []

    def get_references(self, paper_id: str) -> list[ExternalWork]:
        """Fetch all references (backward citations) for a paper, paginated."""
        api_id = _to_s2_paper_id(paper_id)
        results: list[ExternalWork] = []
        offset = 0
        while True:
            resp = self._call(
                "GET",
                f"/paper/{api_id}/references",
                params={"fields": _CITATION_FIELDS, "limit": _PAGE_SIZE, "offset": offset},
            )
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("data") or []:
                cited = item.get("citedPaper")
                if cited:
                    results.append(_parse_paper(cited))
            if "next" not in data:
                break
            offset = data["next"]
        return results

    def get_citations(self, paper_id: str) -> list[ExternalWork]:
        """Fetch all citing papers (forward citations) for a paper, paginated."""
        api_id = _to_s2_paper_id(paper_id)
        results: list[ExternalWork] = []
        offset = 0
        while True:
            resp = self._call(
                "GET",
                f"/paper/{api_id}/citations",
                params={"fields": _CITATION_FIELDS, "limit": _PAGE_SIZE, "offset": offset},
            )
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("data") or []:
                citing = item.get("citingPaper")
                if citing:
                    results.append(_parse_paper(citing))
            if "next" not in data:
                break
            offset = data["next"]
        return results
