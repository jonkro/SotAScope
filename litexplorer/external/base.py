"""Intermediate dataclasses and abstract base for external literature clients.

These frozen dataclasses decouple external API responses from SQLAlchemy models,
satisfying the spec requirement to abstract all API integrations behind a clean
internal interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExternalAuthor:
    name: str
    external_id: str | None = None  # e.g. OpenAlex author ID


@dataclass(frozen=True)
class ExternalVenue:
    name: str
    external_id: str | None = None  # e.g. OpenAlex source ID
    venue_type: str | None = None  # 'conference' | 'journal'
    issn: str | None = None  # e.g. from Crossref
    publisher: str | None = None


@dataclass(frozen=True)
class ExternalLocation:
    url: str
    location_type: str  # 'venue' | 'preprint'
    is_primary: bool = False


@dataclass(frozen=True)
class ExternalWork:
    title: str
    external_id: str | None = None  # e.g. OpenAlex work ID "W..."
    doi: str | None = None  # bare DOI, no https://doi.org/ prefix
    arxiv_id: str | None = None
    semantic_scholar_id: str | None = None  # Semantic Scholar paper ID
    abstract: str | None = None
    publication_year: int | None = None
    citation_count: int | None = None
    citations_by_year: list[dict] | None = None  # [{"year": 2022, "cited_by_count": 45}, ...]
    venue: ExternalVenue | None = None
    authors: list[ExternalAuthor] = field(default_factory=list)
    locations: list[ExternalLocation] = field(default_factory=list)
    # OpenAlex IDs of referenced works (backward citations)
    referenced_work_ids: list[str] = field(default_factory=list)


class ExternalLiteratureClient(ABC):
    """Abstract interface for external literature data sources."""

    @abstractmethod
    def get_work_by_doi(self, doi: str) -> ExternalWork | None:
        """Fetch a single work by DOI. Returns None if not found."""

    @abstractmethod
    def get_works_by_ids(self, external_ids: list[str]) -> list[ExternalWork]:
        """Fetch multiple works by their external IDs (batch)."""

    @abstractmethod
    def get_forward_citations(self, external_id: str) -> list[ExternalWork]:
        """Fetch all works that cite the given work (forward citations)."""
