"""Semantic Scholar Academic Graph API client.

Handles paper lookup by DOI or Semantic Scholar paper ID, and fetching
backward citations (references) and forward citations with cursor pagination.
"""

from __future__ import annotations

import logging

import httpx

from litexplorer.external.base import ExternalWork

logger = logging.getLogger(__name__)

_DOI_PREFIX = "https://doi.org/"

# Maximum items per paginated request (S2 limit)
_PAGE_SIZE = 500

# Fields requested for individual paper lookups
_PAPER_FIELDS = "paperId,externalIds,title,year,citationCount,abstract"
# Fields requested for bulk citation/reference lists (skip abstract to reduce payload)
_CITATION_FIELDS = "paperId,externalIds,title,year,citationCount"


def _normalize_doi(raw: str | None) -> str | None:
    """Strip the https://doi.org/ prefix if present; lowercase."""
    if not raw:
        return None
    doi = raw.removeprefix(_DOI_PREFIX).strip()
    return doi.lower() if doi else None


def _parse_paper(raw: dict) -> ExternalWork:
    """Parse a single Semantic Scholar paper dict into an ExternalWork."""
    paper_id = raw.get("paperId") or None
    ext_ids = raw.get("externalIds") or {}

    doi = _normalize_doi(ext_ids.get("DOI"))
    arxiv_id = ext_ids.get("ArXiv") or None

    return ExternalWork(
        title=raw.get("title") or "(untitled)",
        doi=doi,
        arxiv_id=arxiv_id,
        semantic_scholar_id=paper_id,
        abstract=raw.get("abstract"),
        publication_year=raw.get("year"),
        citation_count=raw.get("citationCount"),
    )


class SemanticScholarClient:
    """Synchronous Semantic Scholar Academic Graph API client."""

    def __init__(
        self,
        base_url: str = "https://api.semanticscholar.org/graph/v1",
        api_key: str | None = None,
        verify: bool = True,
    ):
        headers: dict[str, str] = {"User-Agent": "LitExplorer/0.1"}
        if api_key:
            headers["x-api-key"] = api_key
        self._http = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=30.0,
            verify=verify,
        )

    def close(self) -> None:
        self._http.close()

    # -- Public API -----------------------------------------------------------

    def get_paper(self, paper_id: str) -> ExternalWork | None:
        """Fetch a single paper by Semantic Scholar ID or prefixed identifier.

        Use "DOI:{doi}" or "ARXIV:{arxiv_id}" as paper_id for lookup by
        external identifier.  Returns None if not found.
        """
        resp = self._http.get(f"/paper/{paper_id}", params={"fields": _PAPER_FIELDS})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return _parse_paper(resp.json())

    def get_paper_by_doi(self, doi: str) -> ExternalWork | None:
        """Fetch a paper by DOI. Returns None if not found."""
        return self.get_paper(f"DOI:{doi}")

    def get_paper_by_id(self, paper_id: str) -> ExternalWork | None:
        """Fetch a paper by its Semantic Scholar paper ID. Returns None if not found."""
        return self.get_paper(paper_id)

    def search_by_title(self, query: str, limit: int = 5) -> list[dict]:
        """Search for papers by title (or combined bibliographic query).

        Returns raw Semantic Scholar paper dicts including paperId, externalIds,
        title, year, and authors.  Returns an empty list on 400/404/429.
        """
        fields = "paperId,externalIds,title,year,authors"
        resp = self._http.get(
            "/paper/search",
            params={"query": query, "fields": fields, "limit": limit},
        )
        if resp.status_code in (400, 404):
            return []
        resp.raise_for_status()
        return resp.json().get("data") or []

    def get_references(self, paper_id: str) -> list[ExternalWork]:
        """Fetch all references (backward citations) for a paper, paginated."""
        results: list[ExternalWork] = []
        offset = 0
        while True:
            resp = self._http.get(
                f"/paper/{paper_id}/references",
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
        results: list[ExternalWork] = []
        offset = 0
        while True:
            resp = self._http.get(
                f"/paper/{paper_id}/citations",
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
