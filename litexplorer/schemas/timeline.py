"""Timeline endpoint response models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from litexplorer.schemas.projects import TopicListOut


class TimelineSeedWork(BaseModel):
    id: int
    doi: str | None
    arxiv_id: str | None
    title: str
    publication_year: int | None
    venue_id: int | None
    citation_count: int | None
    citations_by_year: list[dict] | None
    topic_list_ids: list[int]
    has_backward_citations: bool
    has_forward_citations: bool
    forward_citations_fetched_at: datetime | None
    backward_citations_no_oa_data: bool = False  # True when OA returned empty reference list
    oa_forward_no_data: bool = False             # True when OA forward fetch returned empty list
    has_pdfs: bool = False
    # Semantic Scholar enrichment status (tracked via api_cache entries)
    s2_refs_fetched: bool = False    # S2 refs fetch was attempted for this seed
    s2_refs_no_data: bool = False    # S2 refs fetched but returned 0 results
    s2_citing_fetched: bool = False  # S2 citing fetch was attempted for this seed
    s2_citing_no_data: bool = False  # S2 citing fetched but returned 0 results
    # GROBID extraction status (tracked via grobid_references:{work_id} cache key)
    grobid_fetched: bool = False     # GROBID extraction was run for this seed


class TimelineNeighborWork(BaseModel):
    id: int
    doi: str | None
    arxiv_id: str | None
    title: str
    publication_year: int | None
    venue_id: int | None
    citation_count: int | None
    citations_by_year: list[dict] | None
    direction: str  # 'backward' | 'forward'
    connected_seed_ids: list[int]
    has_citation_data: bool = True  # False when openalex_id is None (no OA citation graph data)
    relevance_score: float = 0.0    # pre-computed by compute_relevance_score(); used for client-side visibility threshold


class SeedCitation(BaseModel):
    citing_seed_id: int
    cited_seed_id: int


class TimelineResponse(BaseModel):
    seeds: list[TimelineSeedWork]
    neighbors: list[TimelineNeighborWork]
    topic_lists: list[TopicListOut]
    tier1_venue_ids: list[int]
    ignored_venue_ids: list[int]
    seed_citations: list[SeedCitation]
