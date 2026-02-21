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
