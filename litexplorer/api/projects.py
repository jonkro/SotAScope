"""CRUD routes for projects, topic lists, and topic-list work membership."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from litexplorer.services.work_lock import work_lock

logger = logging.getLogger(__name__)

from litexplorer.api.deps import get_db
from litexplorer.models.library import Citation, Venue, VenueAlias, Work, WorkAuthor, WorkLocation
from litexplorer.models.project import Project, ProjectIgnoredWork, ProjectVenueTier, TopicList, TopicListWork
from litexplorer.schemas.projects import (
    ProjectCreate,
    ProjectDetail,
    ProjectIgnoredWorkAdd,
    ProjectIgnoredWorkOut,
    ProjectOut,
    ProjectUpdate,
    ProjectVenueTierOut,
    ProjectVenueTierUpdate,
    TopicListCreate,
    TopicListDetail,
    TopicListOut,
    TopicListUpdate,
    TopicListWorkAdd,
    TopicListWorkOut,
)
from litexplorer.schemas.works import WorkOut
from litexplorer.schemas.project_merge import MergeDecisions, MergePreview
from litexplorer.schemas.project_import import ImportResult, ImportResolveRequest

router = APIRouter(prefix="/api/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Auto-enrichment background task
# ---------------------------------------------------------------------------

def _auto_enrich_bg(work_id: int) -> None:
    """Auto-enrichment background task run when a work is added to a topic list.

    Fetches backward citations, forward citations, and Crossref venue metadata
    in sequence.  Releases the work lock when done regardless of errors.
    Creates its own DB session (must NOT use the request-scoped session, which
    is closed by the time the background task runs).
    """
    from litexplorer.database import SessionLocal
    from litexplorer.api.enrichment import _get_client, _get_crossref_client
    from litexplorer.services.enrichment import EnrichmentService

    db = SessionLocal()
    try:
        client = _get_client(db)
        cr_client = _get_crossref_client(db)
        try:
            svc = EnrichmentService(db=db, client=client, crossref_client=cr_client)

            try:
                svc.fetch_backward_citations(work_id)
            except Exception:
                logger.exception("Auto-enrichment: backward citations failed for work %d", work_id)

            try:
                svc.fetch_forward_citations(work_id)
            except Exception:
                logger.exception("Auto-enrichment: forward citations failed for work %d", work_id)

            try:
                svc.enrich_from_crossref(work_id)
            except Exception:
                logger.warning("Auto-enrichment: Crossref enrichment failed for work %d", work_id)
        finally:
            client.close()
            cr_client.close()
    finally:
        work_lock.release(work_id)
        db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _get_topic_list(db: Session, project_id: int, topic_list_id: int) -> TopicList:
    tl = db.scalars(
        select(TopicList).where(
            TopicList.id == topic_list_id,
            TopicList.project_id == project_id,
        )
    ).one_or_none()
    if not tl:
        raise HTTPException(status_code=404, detail="Topic list not found")
    return tl


def _topic_list_detail(tl: TopicList) -> TopicListDetail:
    return TopicListDetail(
        **{c.key: getattr(tl, c.key) for c in TopicList.__table__.columns},
        works=[
            TopicListWorkOut(
                id=assoc.id,
                work_id=assoc.work_id,
                added_at=assoc.added_at,
                work=WorkOut.model_validate(assoc.work),
            )
            for assoc in tl.work_associations
        ],
    )


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------

@router.get("", response_model=list[ProjectOut])
def list_projects(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, description="Search project name"),
    db: Session = Depends(get_db),
):
    stmt = select(Project).order_by(Project.id)
    if q:
        stmt = stmt.where(Project.name.ilike(f"%{q}%"))
    return db.scalars(stmt.offset(offset).limit(limit)).all()


def _project_detail(project: Project) -> ProjectDetail:
    """Build a ProjectDetail response from a Project ORM instance."""
    return ProjectDetail(
        **{c.key: getattr(project, c.key) for c in Project.__table__.columns},
        topic_lists=[TopicListOut.model_validate(tl) for tl in project.topic_lists],
        ignored_works=[
            ProjectIgnoredWorkOut(
                id=assoc.id,
                work_id=assoc.work_id,
                work=WorkOut.model_validate(assoc.work),
            )
            for assoc in project.ignored_work_associations
        ],
    )


@router.post("", response_model=ProjectDetail, status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)):
    project = Project(**body.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return _project_detail(project)


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.scalars(
        select(Project)
        .where(Project.id == project_id)
        .options(
            joinedload(Project.topic_lists),
            joinedload(Project.ignored_work_associations).joinedload(ProjectIgnoredWork.work),
        )
    ).unique().one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_detail(project)


@router.patch("/{project_id}", response_model=ProjectDetail)
def update_project(project_id: int, body: ProjectUpdate, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    db.commit()
    db.refresh(project)
    return _project_detail(project)


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    db.delete(project)
    db.commit()


# ---------------------------------------------------------------------------
# Topic lists (nested under project)
# ---------------------------------------------------------------------------

@router.get("/{project_id}/topic-lists", response_model=list[TopicListOut])
def list_topic_lists(project_id: int, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    return db.scalars(
        select(TopicList)
        .where(TopicList.project_id == project_id)
        .order_by(TopicList.id)
    ).all()


@router.post("/{project_id}/topic-lists", response_model=TopicListDetail, status_code=201)
def create_topic_list(
    project_id: int, body: TopicListCreate, db: Session = Depends(get_db)
):
    _get_project(db, project_id)
    tl = TopicList(project_id=project_id, **body.model_dump())
    db.add(tl)
    db.commit()
    db.refresh(tl)
    return _topic_list_detail(tl)


@router.get("/{project_id}/topic-lists/{topic_list_id}", response_model=TopicListDetail)
def get_topic_list(project_id: int, topic_list_id: int, db: Session = Depends(get_db)):
    tl = db.scalars(
        select(TopicList)
        .where(TopicList.id == topic_list_id, TopicList.project_id == project_id)
        .options(
            joinedload(TopicList.work_associations).joinedload(TopicListWork.work)
        )
    ).unique().one_or_none()
    if not tl:
        raise HTTPException(status_code=404, detail="Topic list not found")
    return _topic_list_detail(tl)


@router.patch("/{project_id}/topic-lists/{topic_list_id}", response_model=TopicListDetail)
def update_topic_list(
    project_id: int,
    topic_list_id: int,
    body: TopicListUpdate,
    db: Session = Depends(get_db),
):
    tl = _get_topic_list(db, project_id, topic_list_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(tl, key, value)
    db.commit()
    db.refresh(tl)
    return _topic_list_detail(tl)


@router.delete("/{project_id}/topic-lists/{topic_list_id}", status_code=204)
def delete_topic_list(
    project_id: int, topic_list_id: int, db: Session = Depends(get_db)
):
    tl = _get_topic_list(db, project_id, topic_list_id)
    db.delete(tl)
    db.commit()


# ---------------------------------------------------------------------------
# Topic list works (add / remove papers from a topic list)
# ---------------------------------------------------------------------------

@router.post(
    "/{project_id}/topic-lists/{topic_list_id}/works",
    response_model=TopicListWorkOut,
    status_code=201,
)
def add_work_to_topic_list(
    project_id: int,
    topic_list_id: int,
    body: TopicListWorkAdd,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    tl = _get_topic_list(db, project_id, topic_list_id)
    work = db.get(Work, body.work_id)
    if not work:
        raise HTTPException(status_code=422, detail="Work not found")

    existing = db.scalars(
        select(TopicListWork).where(
            TopicListWork.topic_list_id == tl.id,
            TopicListWork.work_id == work.id,
        )
    ).one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Work already in this topic list")

    assoc = TopicListWork(topic_list_id=tl.id, work_id=work.id)
    db.add(assoc)
    db.commit()
    db.refresh(assoc)

    # Trigger auto-enrichment in the background (backward refs, forward cites, Crossref).
    # If the work is already being enriched, skip silently rather than blocking the add.
    if work_lock.acquire(work.id, "Auto-enrichment (new seed)"):
        background_tasks.add_task(_auto_enrich_bg, work.id)
    else:
        logger.info(
            "Work %d is already locked; skipping auto-enrichment after topic list add",
            work.id,
        )

    return TopicListWorkOut(
        id=assoc.id,
        work_id=assoc.work_id,
        added_at=assoc.added_at,
        work=WorkOut.model_validate(work),
    )


@router.delete(
    "/{project_id}/topic-lists/{topic_list_id}/works/{work_id}",
    status_code=204,
)
def remove_work_from_topic_list(
    project_id: int, topic_list_id: int, work_id: int, db: Session = Depends(get_db)
):
    tl = _get_topic_list(db, project_id, topic_list_id)
    assoc = db.scalars(
        select(TopicListWork).where(
            TopicListWork.topic_list_id == tl.id,
            TopicListWork.work_id == work_id,
        )
    ).one_or_none()
    if not assoc:
        raise HTTPException(status_code=404, detail="Work not in this topic list")
    db.delete(assoc)
    db.commit()


# ---------------------------------------------------------------------------
# Ignored works (mark papers as uninteresting per project)
# ---------------------------------------------------------------------------

@router.post(
    "/{project_id}/ignored-works",
    response_model=ProjectIgnoredWorkOut,
    status_code=201,
)
def add_ignored_work(
    project_id: int,
    body: ProjectIgnoredWorkAdd,
    db: Session = Depends(get_db),
):
    _get_project(db, project_id)
    work = db.get(Work, body.work_id)
    if not work:
        raise HTTPException(status_code=422, detail="Work not found")

    existing = db.scalars(
        select(ProjectIgnoredWork).where(
            ProjectIgnoredWork.project_id == project_id,
            ProjectIgnoredWork.work_id == work.id,
        )
    ).one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Work already ignored in this project")

    assoc = ProjectIgnoredWork(project_id=project_id, work_id=work.id)
    db.add(assoc)
    db.commit()
    db.refresh(assoc)
    return ProjectIgnoredWorkOut(
        id=assoc.id,
        work_id=assoc.work_id,
        work=WorkOut.model_validate(work),
    )


@router.delete(
    "/{project_id}/ignored-works/{work_id}",
    status_code=204,
)
def remove_ignored_work(
    project_id: int, work_id: int, db: Session = Depends(get_db)
):
    _get_project(db, project_id)
    assoc = db.scalars(
        select(ProjectIgnoredWork).where(
            ProjectIgnoredWork.project_id == project_id,
            ProjectIgnoredWork.work_id == work_id,
        )
    ).one_or_none()
    if not assoc:
        raise HTTPException(status_code=404, detail="Work not in ignored list")
    db.delete(assoc)
    db.commit()


# ---------------------------------------------------------------------------
# Per-project venue tiers
# ---------------------------------------------------------------------------

def _preferred_venue_name(venue: Venue) -> str:
    """Return the preferred display name: first alias by sort_order, else venue.name."""
    if venue.aliases:
        return venue.aliases[0].alias
    return venue.name


def _project_venue_ids(project_id: int, db: Session) -> set[int]:
    """Return all venue IDs that appear in this project (seeds + citation neighbors)."""
    # Seed work IDs
    seed_ids: set[int] = set(
        db.scalars(
            select(TopicListWork.work_id).join(
                TopicList, TopicListWork.topic_list_id == TopicList.id
            ).where(TopicList.project_id == project_id)
        ).all()
    )

    # Neighbor work IDs (backward + forward citations of seeds)
    neighbor_ids: set[int] = set()
    if seed_ids:
        bwd = db.execute(
            select(Citation.cited_work_id).where(Citation.citing_work_id.in_(seed_ids))
        ).scalars().all()
        fwd = db.execute(
            select(Citation.citing_work_id).where(Citation.cited_work_id.in_(seed_ids))
        ).scalars().all()
        neighbor_ids = set(bwd) | set(fwd)

    all_work_ids = seed_ids | neighbor_ids
    if not all_work_ids:
        return set()

    venue_ids: set[int] = set(
        db.scalars(
            select(Work.venue_id).where(
                Work.id.in_(all_work_ids),
                Work.venue_id.is_not(None),
            ).distinct()
        ).all()
    )
    return venue_ids


@router.get("/{project_id}/venue-tiers", response_model=list[ProjectVenueTierOut])
def list_project_venue_tiers(project_id: int, db: Session = Depends(get_db)):
    """Return tier info for all venues relevant to this project."""
    _get_project(db, project_id)

    venue_ids = _project_venue_ids(project_id, db)
    if not venue_ids:
        return []

    # Load venues with aliases eagerly (single extra query, avoids N+1)
    venues = db.scalars(
        select(Venue)
        .where(Venue.id.in_(venue_ids))
        .options(selectinload(Venue.aliases))
        .order_by(Venue.name)
    ).all()

    # Load all local overrides for this project in one query
    overrides: dict[int, int] = {}
    if venue_ids:
        rows = db.execute(
            select(ProjectVenueTier.venue_id, ProjectVenueTier.tier).where(
                ProjectVenueTier.project_id == project_id,
                ProjectVenueTier.venue_id.in_(venue_ids),
            )
        ).all()
        overrides = {vid: tier for vid, tier in rows}

    result: list[ProjectVenueTierOut] = []
    for venue in venues:
        local_tier = overrides.get(venue.id)
        alias_names = [a.alias for a in venue.aliases]  # already sorted by sort_order
        # all_names: deduplicated list of (alias names) + canonical name
        all_names = alias_names + ([] if venue.name in alias_names else [venue.name])
        result.append(
            ProjectVenueTierOut(
                venue_id=venue.id,
                venue_name=_preferred_venue_name(venue),
                all_names=all_names,
                global_tier=venue.tier,
                local_tier=local_tier,
                effective_tier=local_tier if local_tier is not None else venue.tier,
            )
        )
    return result


@router.put(
    "/{project_id}/venue-tiers/{venue_id}",
    response_model=ProjectVenueTierOut,
)
def set_project_venue_tier(
    project_id: int,
    venue_id: int,
    body: ProjectVenueTierUpdate,
    db: Session = Depends(get_db),
):
    """Create or update a per-project venue tier override."""
    _get_project(db, project_id)
    venue = db.get(Venue, venue_id)
    if not venue:
        raise HTTPException(status_code=404, detail="Venue not found")

    # Load aliases for the response
    db.refresh(venue)
    override = db.scalars(
        select(ProjectVenueTier).where(
            ProjectVenueTier.project_id == project_id,
            ProjectVenueTier.venue_id == venue_id,
        )
    ).one_or_none()

    if override:
        override.tier = body.tier
    else:
        override = ProjectVenueTier(
            project_id=project_id, venue_id=venue_id, tier=body.tier
        )
        db.add(override)
    db.commit()

    alias_names = [a.alias for a in venue.aliases]
    all_names = alias_names + ([] if venue.name in alias_names else [venue.name])
    return ProjectVenueTierOut(
        venue_id=venue.id,
        venue_name=_preferred_venue_name(venue),
        all_names=all_names,
        global_tier=venue.tier,
        local_tier=override.tier,
        effective_tier=override.tier,
    )


@router.delete("/{project_id}/venue-tiers/{venue_id}", status_code=204)
def reset_project_venue_tier(
    project_id: int, venue_id: int, db: Session = Depends(get_db)
):
    """Delete a per-project venue tier override (reverts to global tier)."""
    _get_project(db, project_id)
    override = db.scalars(
        select(ProjectVenueTier).where(
            ProjectVenueTier.project_id == project_id,
            ProjectVenueTier.venue_id == venue_id,
        )
    ).one_or_none()
    if not override:
        raise HTTPException(status_code=404, detail="No local override for this venue")
    db.delete(override)
    db.commit()


# ---------------------------------------------------------------------------
# Project merging
# ---------------------------------------------------------------------------

@router.get(
    "/{target_id}/merge-preview/{source_id}",
    response_model=MergePreview,
)
def get_merge_preview(
    target_id: int, source_id: int, db: Session = Depends(get_db)
):
    """Preview what merging source into target would do."""
    if target_id == source_id:
        raise HTTPException(status_code=400, detail="Cannot merge a project into itself")
    if not db.get(Project, target_id):
        raise HTTPException(status_code=404, detail="Target project not found")
    if not db.get(Project, source_id):
        raise HTTPException(status_code=404, detail="Source project not found")

    from litexplorer.services.project_merge import merge_preview as _preview
    return _preview(target_id, source_id, db)


@router.post(
    "/{target_id}/merge/{source_id}",
    response_model=ProjectDetail,
)
def merge_project(
    target_id: int,
    source_id: int,
    body: MergeDecisions,
    db: Session = Depends(get_db),
):
    """Merge source project into target. Source is deleted after merge."""
    if target_id == source_id:
        raise HTTPException(status_code=400, detail="Cannot merge a project into itself")
    if not db.get(Project, target_id):
        raise HTTPException(status_code=404, detail="Target project not found")
    if not db.get(Project, source_id):
        raise HTTPException(status_code=404, detail="Source project not found")

    from litexplorer.services.project_merge import execute_merge as _merge
    merged = _merge(target_id, source_id, body, db)
    return _project_detail(merged)


# ---------------------------------------------------------------------------
# BibTeX export (project-scoped)
# ---------------------------------------------------------------------------


@router.get("/{project_id}/export/bibtex")
def export_project_bibtex(
    project_id: int,
    work_ids: str = Query("", description="Comma-separated work IDs; empty = all project seeds"),
    db: Session = Depends(get_db),
) -> Response:
    """Export project seed works as a BibTeX file.

    If *work_ids* is provided (comma-separated integers), only those works are
    exported.  Otherwise all seed works across all topic lists in the project
    are exported.

    Returns a ``text/plain`` response with ``Content-Disposition: attachment``.
    """
    from litexplorer.services.bibtex_export import works_to_bibtex

    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if work_ids.strip():
        try:
            wids: list[int] = [int(x.strip()) for x in work_ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(status_code=422, detail="work_ids must be comma-separated integers")
    else:
        # Collect all unique seed IDs for this project
        seed_stmt = (
            select(TopicListWork.work_id)
            .join(TopicList, TopicList.id == TopicListWork.topic_list_id)
            .where(TopicList.project_id == project_id)
            .distinct()
        )
        wids = list(db.scalars(seed_stmt).all())

    if not wids:
        return Response(
            content="",
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="project.bib"'},
        )

    stmt = (
        select(Work)
        .where(Work.id.in_(wids))
        .options(
            selectinload(Work.authors).selectinload(WorkAuthor.author),
            selectinload(Work.venue).selectinload(Venue.aliases),
            selectinload(Work.locations),
        )
        .order_by(Work.publication_year, Work.title)
    )
    works = list(db.scalars(stmt).all())
    content = works_to_bibtex(works)

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in project.name)
    filename = f"{safe_name}.bib" if safe_name else "project.bib"

    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Project ZIP export
# ---------------------------------------------------------------------------


@router.get("/{project_id}/export")
def export_project_zip(
    project_id: int,
    db: Session = Depends(get_db),
) -> Response:
    """Export the full project as a .zip archive.

    The archive contains:
    - ``manifest.json``: structured JSON with project data (works, topic lists,
      extraction schemas + results, venue overrides, chat sessions, work notes,
      citation edges between seeds).
    - ``seeds.bib``: BibTeX for all seed works.

    Returns an ``application/zip`` response with ``Content-Disposition: attachment``.
    """
    from litexplorer.services.project_export import export_project

    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    buf = export_project(project_id, db)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in project.name)
    filename = f"{safe_name}.zip" if safe_name else "project.zip"

    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Project ZIP import
# ---------------------------------------------------------------------------


def _load_project_detail(project_id: int, db: Session) -> ProjectDetail:
    """Reload project with eager joins and return ProjectDetail."""
    project = db.scalars(
        select(Project)
        .where(Project.id == project_id)
        .options(
            joinedload(Project.topic_lists),
            joinedload(Project.ignored_work_associations).joinedload(
                ProjectIgnoredWork.work
            ),
        )
    ).unique().one()
    return _project_detail(project)


@router.post("/import", response_model=ImportResult, status_code=201)
async def import_project_zip(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> ImportResult:
    """Import a project from a .zip archive produced by the export endpoint.

    Returns an :class:`ImportResult`.  If ``needs_project_decision`` is True
    a temp project was created; follow up with
    ``POST /api/projects/import/{temp_id}/resolve`` to merge or rename it.
    Otherwise the project was created directly and auto-enrichment has been
    scheduled for all seed works.
    """
    from litexplorer.services.project_import import import_project as _import

    content = await file.read()
    try:
        result, seed_ids = _import(content, db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Schedule auto-enrichment for new seed works (only when no collision)
    if not result.needs_project_decision:
        for work_id in seed_ids:
            if work_lock.acquire(work_id, "Auto-enrichment (imported seed)"):
                background_tasks.add_task(_auto_enrich_bg, work_id)

    return result


@router.post("/import/{temp_id}/resolve", response_model=ProjectDetail)
def resolve_import_collision(
    temp_id: int,
    body: ImportResolveRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> ProjectDetail:
    """Resolve a project name collision that was detected during import.

    ``action="merge"`` merges the temp project into the existing project
    specified by ``target_project_id``, then deletes the temp project.

    ``action="rename"`` renames the temp project to ``new_name``.

    After resolution, auto-enrichment is scheduled for seed works.
    """
    from litexplorer.services.project_import import resolve_import as _resolve

    try:
        final_project, seed_ids = _resolve(
            temp_id,
            body.action,
            body.target_project_id,
            body.new_name,
            body.merge_decisions,
            db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    for work_id in seed_ids:
        if work_lock.acquire(work_id, "Auto-enrichment (imported seed)"):
            background_tasks.add_task(_auto_enrich_bg, work_id)

    return _load_project_detail(final_project.id, db)
