"""Timeline API — returns all data needed for the citation timeline visualization."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from litexplorer.api.deps import get_db
from litexplorer.models.cache import ApiCache
from litexplorer.models.library import Citation, Venue, Work
from litexplorer.models.project import Project, ProjectIgnoredWork, TopicList, TopicListWork
from litexplorer.schemas.timeline import (
    SeedCitation,
    TimelineNeighborWork,
    TimelineResponse,
    TimelineSeedWork,
)
from litexplorer.schemas.projects import TopicListOut

router = APIRouter(prefix="/api/projects", tags=["timeline"])


@router.get("/{project_id}/timeline", response_model=TimelineResponse)
def get_project_timeline(
    project_id: int,
    db: Session = Depends(get_db),
):
    """Return all data for the citation timeline of a project.

    Gathers seed works (from topic lists), backward + forward citation
    neighbors, enrichment status, and tier-based venue ids.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # 1. Seed works: work_id -> set of topic_list_ids
    seed_topic_map: dict[int, set[int]] = defaultdict(set)
    rows = db.execute(
        select(TopicListWork.work_id, TopicList.id).join(
            TopicList, TopicListWork.topic_list_id == TopicList.id
        ).where(TopicList.project_id == project_id)
    ).all()
    for work_id, tl_id in rows:
        seed_topic_map[work_id].add(tl_id)

    seed_ids = set(seed_topic_map.keys())

    # 1b. Ignored works for this project
    ignored_work_ids: set[int] = set(
        db.scalars(
            select(ProjectIgnoredWork.work_id).where(
                ProjectIgnoredWork.project_id == project_id
            )
        ).all()
    )

    # 2. Citation edges involving seeds
    backward_edges: list[tuple[int, int]] = []  # (seed citing_id, cited neighbor_id)
    forward_edges: list[tuple[int, int]] = []   # (neighbor citing_id, seed cited_id)
    seed_citations: list[SeedCitation] = []

    if seed_ids:
        # Backward: seeds cite others
        bwd_rows = db.execute(
            select(Citation.citing_work_id, Citation.cited_work_id).where(
                Citation.citing_work_id.in_(seed_ids)
            )
        ).all()
        for citing_id, cited_id in bwd_rows:
            if cited_id in seed_ids:
                seed_citations.append(
                    SeedCitation(citing_seed_id=citing_id, cited_seed_id=cited_id)
                )
            else:
                backward_edges.append((citing_id, cited_id))

        # Forward: others cite seeds
        fwd_rows = db.execute(
            select(Citation.citing_work_id, Citation.cited_work_id).where(
                Citation.cited_work_id.in_(seed_ids)
            )
        ).all()
        for citing_id, cited_id in fwd_rows:
            if citing_id in seed_ids:
                # Already captured as seed_citation above (or will be)
                sc = SeedCitation(citing_seed_id=citing_id, cited_seed_id=cited_id)
                if sc not in seed_citations:
                    seed_citations.append(sc)
            else:
                forward_edges.append((citing_id, cited_id))

    # 3. Collect all neighbor work ids and their connections
    bwd_neighbor_seeds: dict[int, list[int]] = defaultdict(list)
    for seed_id, neighbor_id in backward_edges:
        bwd_neighbor_seeds[neighbor_id].append(seed_id)

    fwd_neighbor_seeds: dict[int, list[int]] = defaultdict(list)
    for neighbor_id, seed_id in forward_edges:
        fwd_neighbor_seeds[neighbor_id].append(seed_id)

    # 3b. Remove ignored works from neighbors
    for wid in ignored_work_ids:
        bwd_neighbor_seeds.pop(wid, None)
        fwd_neighbor_seeds.pop(wid, None)

    # 4. Bulk load all Work objects
    all_work_ids = seed_ids | set(bwd_neighbor_seeds.keys()) | set(fwd_neighbor_seeds.keys())
    works_by_id: dict[int, Work] = {}
    if all_work_ids:
        work_rows = db.execute(
            select(Work).where(Work.id.in_(all_work_ids))
        ).scalars().all()
        works_by_id = {w.id: w for w in work_rows}

    # 5. Enrichment status for seeds — check ApiCache for fetch records
    #    (Citation row existence is insufficient: a work with 0 references
    #     has no Citation rows but the fetch was still done.)
    seeds_with_bwd: set[int] = set()
    seeds_with_fwd: set[int] = set()
    for sid in seed_ids:
        w = works_by_id.get(sid)
        if w and w.openalex_id:
            bwd_key = f"backward_citations:{w.openalex_id}"
            if db.execute(
                select(ApiCache.id).where(
                    ApiCache.source == "openalex",
                    ApiCache.query_key == bwd_key,
                ).limit(1)
            ).scalar_one_or_none() is not None:
                seeds_with_bwd.add(sid)

            fwd_key = f"forward_citations:{w.openalex_id}"
            if db.execute(
                select(ApiCache.id).where(
                    ApiCache.source == "openalex",
                    ApiCache.query_key == fwd_key,
                ).limit(1)
            ).scalar_one_or_none() is not None:
                seeds_with_fwd.add(sid)

    # Forward citations fetched_at from ApiCache
    fwd_cache_timestamps: dict[int, str | None] = {}
    for sid in seed_ids:
        w = works_by_id.get(sid)
        if w and w.openalex_id:
            cache_key = f"forward_citations:{w.openalex_id}"
            cache_row = db.execute(
                select(ApiCache.fetched_at).where(
                    ApiCache.source == "openalex",
                    ApiCache.query_key == cache_key,
                )
            ).scalar_one_or_none()
            fwd_cache_timestamps[sid] = cache_row
        else:
            fwd_cache_timestamps[sid] = None

    # 6. Tier-based venue ids — read directly from Venue.tier
    tier1_venue_ids: list[int] = []
    ignored_venue_ids: list[int] = []

    # Collect all venue_ids referenced by works in this timeline
    all_venue_ids = {
        w.venue_id for w in works_by_id.values() if w.venue_id is not None
    }
    if all_venue_ids:
        venue_rows = db.execute(
            select(Venue.id, Venue.tier).where(Venue.id.in_(all_venue_ids))
        ).all()
        for vid, tier in venue_rows:
            if tier == 1:
                tier1_venue_ids.append(vid)
            elif tier == 3:
                ignored_venue_ids.append(vid)

    # 7. Build response
    seeds_out: list[TimelineSeedWork] = []
    for wid in sorted(seed_ids):
        w = works_by_id.get(wid)
        if w is None:
            continue
        seeds_out.append(TimelineSeedWork(
            id=w.id,
            doi=w.doi,
            arxiv_id=w.arxiv_id,
            title=w.title,
            publication_year=w.publication_year,
            venue_id=w.venue_id,
            citation_count=w.citation_count,
            topic_list_ids=sorted(seed_topic_map[wid]),
            has_backward_citations=wid in seeds_with_bwd,
            has_forward_citations=wid in seeds_with_fwd,
            forward_citations_fetched_at=fwd_cache_timestamps.get(wid),
        ))

    neighbors_out: list[TimelineNeighborWork] = []
    for nid in sorted(bwd_neighbor_seeds.keys()):
        w = works_by_id.get(nid)
        if w is None:
            continue
        neighbors_out.append(TimelineNeighborWork(
            id=w.id,
            doi=w.doi,
            arxiv_id=w.arxiv_id,
            title=w.title,
            publication_year=w.publication_year,
            venue_id=w.venue_id,
            citation_count=w.citation_count,
            direction="backward",
            connected_seed_ids=sorted(bwd_neighbor_seeds[nid]),
        ))

    for nid in sorted(fwd_neighbor_seeds.keys()):
        w = works_by_id.get(nid)
        if w is None:
            continue
        neighbors_out.append(TimelineNeighborWork(
            id=w.id,
            doi=w.doi,
            arxiv_id=w.arxiv_id,
            title=w.title,
            publication_year=w.publication_year,
            venue_id=w.venue_id,
            citation_count=w.citation_count,
            direction="forward",
            connected_seed_ids=sorted(fwd_neighbor_seeds[nid]),
        ))

    topic_lists_out = [
        TopicListOut.model_validate(tl) for tl in project.topic_lists
    ]

    return TimelineResponse(
        seeds=seeds_out,
        neighbors=neighbors_out,
        topic_lists=topic_lists_out,
        tier1_venue_ids=tier1_venue_ids,
        ignored_venue_ids=ignored_venue_ids,
        seed_citations=seed_citations,
    )
