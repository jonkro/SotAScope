"""Pydantic request/response models for enrichment endpoints."""

from datetime import datetime

from pydantic import BaseModel

from litexplorer.schemas.works import WorkOut


class EnrichDOIRequest(BaseModel):
    doi: str


class EnrichDOIBatchRequest(BaseModel):
    dois: list[str]


class EnrichDOIResult(BaseModel):
    work: WorkOut
    source: str = "openalex"
    cached: bool = False


class EnrichDOIBatchResult(BaseModel):
    results: list[EnrichDOIResult]
    errors: list[dict]  # {"doi": str, "error": str}


class CitationResult(BaseModel):
    works: list[WorkOut]
    count: int
    cached: bool = False
    fetched_at: datetime | None = None


class CrossrefEnrichResult(BaseModel):
    work: WorkOut
    venue_issn: str | None = None
    venue_publisher: str | None = None
