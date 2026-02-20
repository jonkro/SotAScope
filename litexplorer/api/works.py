"""CRUD routes for works, locations, authors, citations, and BibTeX import."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete as sa_delete, exists, func, or_, select
from sqlalchemy.orm import Session, joinedload

from litexplorer.api.deps import get_db
from litexplorer.models.library import (
    Author,
    Citation,
    Venue,
    VenueAlias,
    Work,
    WorkAuthor,
    WorkLocation,
)
from litexplorer.models.project import ProjectIgnoredWork, TopicListWork
from litexplorer.schemas.works import (
    AuthorCreate,
    AuthorOut,
    BibtexImportRequest,
    BibtexImportResult,
    CitationWorkBrief,
    DuplicateGroup,
    WorkAuthorAdd,
    WorkCreate,
    WorkDetail,
    WorkLocationCreate,
    WorkLocationOut,
    WorkOut,
    WorkUpdate,
)

router = APIRouter(prefix="/api/works", tags=["works"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_work(db: Session, work_id: int) -> Work:
    work = db.get(Work, work_id)
    if not work:
        raise HTTPException(status_code=404, detail="Work not found")
    return work


def _work_detail(work: Work) -> WorkDetail:
    venue_name = None
    venue_display_name = None
    if work.venue:
        venue_name = work.venue.name
        venue_display_name = (
            work.venue.aliases[0].alias if work.venue.aliases else work.venue.name
        )
    return WorkDetail(
        **{c.key: getattr(work, c.key) for c in Work.__table__.columns},
        venue_name=venue_name,
        venue_display_name=venue_display_name,
        locations=[WorkLocationOut.model_validate(loc) for loc in work.locations],
        authors=sorted(
            [
                {"author": {"id": wa.author.id, "name": wa.author.name}, "position": wa.position}
                for wa in work.authors
            ],
            key=lambda x: x["position"],
        ),
    )


# ---------------------------------------------------------------------------
# Work CRUD
# ---------------------------------------------------------------------------

@router.get("/duplicates", response_model=list[DuplicateGroup])
def detect_duplicates(db: Session = Depends(get_db)):
    """Detect duplicate works by DOI, bibtex_key, or title+year."""
    groups: list[DuplicateGroup] = []
    seen_ids: set[frozenset[int]] = set()

    def _add_group(reason: str, works: list[Work]) -> None:
        if len(works) < 2:
            return
        key = frozenset(w.id for w in works)
        if key in seen_ids:
            return
        seen_ids.add(key)
        groups.append(DuplicateGroup(
            reason=reason,
            works=[WorkOut.model_validate(w) for w in works],
        ))

    # 1. Same DOI (case-insensitive)
    doi_dupes = (
        db.execute(
            select(func.lower(Work.doi))
            .where(Work.doi.isnot(None))
            .group_by(func.lower(Work.doi))
            .having(func.count() > 1)
        )
        .scalars()
        .all()
    )
    for doi_lower in doi_dupes:
        works = db.scalars(
            select(Work).where(func.lower(Work.doi) == doi_lower)
        ).all()
        _add_group(f"Same DOI: {doi_lower}", list(works))

    # 2. Same bibtex_key (case-insensitive)
    bk_dupes = (
        db.execute(
            select(func.lower(Work.bibtex_key))
            .where(Work.bibtex_key.isnot(None))
            .group_by(func.lower(Work.bibtex_key))
            .having(func.count() > 1)
        )
        .scalars()
        .all()
    )
    for bk_lower in bk_dupes:
        works = db.scalars(
            select(Work).where(func.lower(Work.bibtex_key) == bk_lower)
        ).all()
        _add_group(f"Same BibTeX key: {bk_lower}", list(works))

    # 3. Same title + year (case-insensitive exact match)
    title_year_dupes = (
        db.execute(
            select(func.lower(Work.title), Work.publication_year)
            .where(Work.publication_year.isnot(None))
            .group_by(func.lower(Work.title), Work.publication_year)
            .having(func.count() > 1)
        )
        .all()
    )
    for title_lower, year in title_year_dupes:
        works = db.scalars(
            select(Work).where(
                func.lower(Work.title) == title_lower,
                Work.publication_year == year,
            )
        ).all()
        _add_group(f"Same title + year ({year})", list(works))

    return groups


@router.get("", response_model=list[WorkOut])
def list_works(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, description="Search title, authors, or venue"),
    venue_id: int | None = Query(None),
    year: int | None = Query(None),
    db: Session = Depends(get_db),
):
    # Correlated subqueries for display fields
    first_author_sq = (
        select(Author.name)
        .join(WorkAuthor, WorkAuthor.author_id == Author.id)
        .where(WorkAuthor.work_id == Work.id)
        .order_by(WorkAuthor.position)
        .limit(1)
        .correlate(Work)
        .scalar_subquery()
        .label("first_author_name")
    )
    author_count_sq = (
        select(func.count())
        .select_from(WorkAuthor)
        .where(WorkAuthor.work_id == Work.id)
        .correlate(Work)
        .scalar_subquery()
        .label("author_count")
    )
    preferred_alias_sq = (
        select(VenueAlias.alias)
        .where(VenueAlias.venue_id == Work.venue_id)
        .order_by(VenueAlias.sort_order)
        .limit(1)
        .correlate(Work)
        .scalar_subquery()
    )
    venue_display_sq = func.coalesce(preferred_alias_sq, Venue.name).label("venue_display_name")

    stmt = (
        select(Work, first_author_sq, author_count_sq, venue_display_sq, Venue.tier.label("venue_tier"))
        .outerjoin(Venue, Work.venue_id == Venue.id)
        .order_by(Work.id)
    )
    if q:
        pattern = f"%{q}%"
        author_match = exists(
            select(1)
            .select_from(WorkAuthor)
            .join(Author, WorkAuthor.author_id == Author.id)
            .where(WorkAuthor.work_id == Work.id, Author.name.ilike(pattern))
        )
        alias_match = exists(
            select(1)
            .select_from(VenueAlias)
            .where(VenueAlias.venue_id == Work.venue_id, VenueAlias.alias.ilike(pattern))
        )
        stmt = stmt.where(or_(
            Work.title.ilike(pattern),
            author_match,
            Venue.name.ilike(pattern),
            alias_match,
        ))
    if venue_id is not None:
        stmt = stmt.where(Work.venue_id == venue_id)
    if year is not None:
        stmt = stmt.where(Work.publication_year == year)
    stmt = stmt.offset(offset).limit(limit)

    rows = db.execute(stmt).all()
    results = []
    for work, first_author, a_count, v_display, v_tier in rows:
        d = {c.key: getattr(work, c.key) for c in Work.__table__.columns}
        d["first_author_name"] = first_author
        d["author_count"] = a_count or 0
        d["venue_display_name"] = v_display
        d["venue_tier"] = v_tier
        results.append(WorkOut(**d))
    return results


@router.post("", response_model=WorkDetail, status_code=201)
def create_work(body: WorkCreate, db: Session = Depends(get_db)):
    data = body.model_dump(exclude={"locations", "authors"})
    work = Work(**data)
    db.add(work)

    for loc in body.locations:
        db.add(WorkLocation(work=work, **loc.model_dump()))

    for wa in body.authors:
        # Verify author exists
        if not db.get(Author, wa.author_id):
            raise HTTPException(status_code=422, detail=f"Author {wa.author_id} not found")
        db.add(WorkAuthor(work=work, author_id=wa.author_id, position=wa.position))

    db.commit()
    db.refresh(work)
    return _work_detail(work)


@router.get("/{work_id}", response_model=WorkDetail)
def get_work(work_id: int, db: Session = Depends(get_db)):
    work = db.scalars(
        select(Work)
        .where(Work.id == work_id)
        .options(
            joinedload(Work.venue),
            joinedload(Work.locations),
            joinedload(Work.authors).joinedload(WorkAuthor.author),
        )
    ).unique().one_or_none()
    if not work:
        raise HTTPException(status_code=404, detail="Work not found")
    return _work_detail(work)


@router.patch("/{work_id}", response_model=WorkDetail)
def update_work(work_id: int, body: WorkUpdate, db: Session = Depends(get_db)):
    work = _get_work(db, work_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(work, key, value)
    db.commit()
    db.refresh(work)
    return _work_detail(work)


@router.delete("/{work_id}", status_code=204)
def delete_work(work_id: int, db: Session = Depends(get_db)):
    work = _get_work(db, work_id)
    db.delete(work)
    db.commit()


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

@router.post("/{work_id}/locations", response_model=WorkLocationOut, status_code=201)
def add_location(work_id: int, body: WorkLocationCreate, db: Session = Depends(get_db)):
    work = _get_work(db, work_id)
    loc = WorkLocation(work_id=work.id, **body.model_dump())
    db.add(loc)
    db.commit()
    db.refresh(loc)
    return loc


@router.delete("/{work_id}/locations/{location_id}", status_code=204)
def remove_location(work_id: int, location_id: int, db: Session = Depends(get_db)):
    loc = db.scalars(
        select(WorkLocation).where(
            WorkLocation.id == location_id, WorkLocation.work_id == work_id
        )
    ).one_or_none()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    db.delete(loc)
    db.commit()


# ---------------------------------------------------------------------------
# Authors
# ---------------------------------------------------------------------------

@router.post("/{work_id}/authors", response_model=AuthorOut, status_code=201)
def link_author(work_id: int, body: WorkAuthorAdd, db: Session = Depends(get_db)):
    work = _get_work(db, work_id)
    author = db.get(Author, body.author_id)
    if not author:
        raise HTTPException(status_code=422, detail="Author not found")
    # Check for duplicate
    existing = db.scalars(
        select(WorkAuthor).where(
            WorkAuthor.work_id == work.id, WorkAuthor.author_id == author.id
        )
    ).one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Author already linked to this work")
    db.add(WorkAuthor(work_id=work.id, author_id=author.id, position=body.position))
    db.commit()
    return author


@router.delete("/{work_id}/authors/{author_id}", status_code=204)
def unlink_author(work_id: int, author_id: int, db: Session = Depends(get_db)):
    wa = db.scalars(
        select(WorkAuthor).where(
            WorkAuthor.work_id == work_id, WorkAuthor.author_id == author_id
        )
    ).one_or_none()
    if not wa:
        raise HTTPException(status_code=404, detail="Author link not found")
    db.delete(wa)
    db.commit()


# ---------------------------------------------------------------------------
# Top-level author CRUD (separate from work-scoped)
# ---------------------------------------------------------------------------

authors_router = APIRouter(prefix="/api/authors", tags=["authors"])


@authors_router.get("", response_model=list[AuthorOut])
def list_authors(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, description="Search by name"),
    db: Session = Depends(get_db),
):
    stmt = select(Author).order_by(Author.name)
    if q:
        stmt = stmt.where(Author.name.ilike(f"%{q}%"))
    return db.scalars(stmt.offset(offset).limit(limit)).all()


@authors_router.post("", response_model=AuthorOut, status_code=201)
def create_author(body: AuthorCreate, db: Session = Depends(get_db)):
    author = Author(**body.model_dump())
    db.add(author)
    db.commit()
    db.refresh(author)
    return author


# ---------------------------------------------------------------------------
# Citations (read-only — populated by external API integrations)
# ---------------------------------------------------------------------------

@router.get("/{work_id}/citations/forward", response_model=list[CitationWorkBrief])
def forward_citations(
    work_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Papers that cite this work (forward neighbors)."""
    _get_work(db, work_id)
    stmt = (
        select(Work)
        .join(Citation, Citation.citing_work_id == Work.id)
        .where(Citation.cited_work_id == work_id)
        .order_by(Work.publication_year.desc())
        .offset(offset)
        .limit(limit)
    )
    return db.scalars(stmt).all()


@router.get("/{work_id}/citations/backward", response_model=list[CitationWorkBrief])
def backward_citations(
    work_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Works cited by this work (backward neighbors / references)."""
    _get_work(db, work_id)
    stmt = (
        select(Work)
        .join(Citation, Citation.cited_work_id == Work.id)
        .where(Citation.citing_work_id == work_id)
        .order_by(Work.publication_year.desc())
        .offset(offset)
        .limit(limit)
    )
    return db.scalars(stmt).all()


# ---------------------------------------------------------------------------
# BibTeX import
# ---------------------------------------------------------------------------

@router.post("/import/bibtex", response_model=BibtexImportResult)
def import_bibtex(body: BibtexImportRequest, db: Session = Depends(get_db)):
    """Parse a BibTeX string and create works for each entry."""
    import bibtexparser

    bib_db = bibtexparser.loads(body.bibtex)

    imported_works: list[Work] = []
    skipped = 0

    for entry in bib_db.entries:
        doi = entry.get("doi")
        bibtex_key = entry.get("ID", "")

        # Skip duplicates: check DOI first, then bibtex_key as fallback
        if doi:
            existing = db.scalars(select(Work).where(Work.doi == doi)).one_or_none()
            if existing:
                skipped += 1
                continue
        elif bibtex_key:
            existing = db.scalars(
                select(Work).where(Work.bibtex_key == bibtex_key)
            ).one_or_none()
            if existing:
                skipped += 1
                continue

        title = entry.get("title", "Untitled")
        year_str = entry.get("year")
        year = int(year_str) if year_str and year_str.isdigit() else None

        work = Work(
            doi=doi,
            title=title,
            publication_year=year,
            abstract=entry.get("abstract"),
            bibtex_key=bibtex_key or None,
            bibtex_entry=_entry_to_bibtex(entry),
        )
        db.add(work)
        imported_works.append(work)

    db.commit()
    for w in imported_works:
        db.refresh(w)

    return BibtexImportResult(
        imported=len(imported_works),
        skipped=skipped,
        works=[WorkOut.model_validate(w) for w in imported_works],
        needs_doi_resolution=[w.id for w in imported_works if not w.doi],
    )


def _entry_to_bibtex(entry: dict) -> str:
    """Reconstruct a single BibTeX entry string from a parsed dict."""
    entry_type = entry.get("ENTRYTYPE", "article")
    key = entry.get("ID", "unknown")
    fields = {k: v for k, v in entry.items() if k not in ("ENTRYTYPE", "ID")}
    lines = [f"@{entry_type}{{{key},"]
    for k, v in fields.items():
        lines.append(f"  {k} = {{{v}}},")
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Merge works
# ---------------------------------------------------------------------------

def _repoint_citations_citing(db: Session, source_id: int, target_id: int) -> None:
    """Re-point citations where source is the citing work."""
    rows = db.scalars(
        select(Citation).where(Citation.citing_work_id == source_id)
    ).all()
    for c in rows:
        if c.cited_work_id == target_id:
            db.delete(c)
            continue
        existing = db.scalars(
            select(Citation).where(
                Citation.citing_work_id == target_id,
                Citation.cited_work_id == c.cited_work_id,
            )
        ).one_or_none()
        if existing:
            db.delete(c)
        else:
            c.citing_work_id = target_id


def _repoint_citations_cited(db: Session, source_id: int, target_id: int) -> None:
    """Re-point citations where source is the cited work."""
    rows = db.scalars(
        select(Citation).where(Citation.cited_work_id == source_id)
    ).all()
    for c in rows:
        if c.citing_work_id == target_id:
            db.delete(c)
            continue
        existing = db.scalars(
            select(Citation).where(
                Citation.citing_work_id == c.citing_work_id,
                Citation.cited_work_id == target_id,
            )
        ).one_or_none()
        if existing:
            db.delete(c)
        else:
            c.cited_work_id = target_id


def _repoint_topic_list_works(db: Session, source_id: int, target_id: int) -> None:
    """Move source's topic list memberships to target."""
    rows = db.scalars(
        select(TopicListWork).where(TopicListWork.work_id == source_id)
    ).all()
    for tlw in rows:
        existing = db.scalars(
            select(TopicListWork).where(
                TopicListWork.topic_list_id == tlw.topic_list_id,
                TopicListWork.work_id == target_id,
            )
        ).one_or_none()
        if existing:
            db.delete(tlw)
        else:
            tlw.work_id = target_id


def _repoint_project_ignored_works(db: Session, source_id: int, target_id: int) -> None:
    """Move source's ignored-work marks to target."""
    rows = db.scalars(
        select(ProjectIgnoredWork).where(ProjectIgnoredWork.work_id == source_id)
    ).all()
    for piw in rows:
        existing = db.scalars(
            select(ProjectIgnoredWork).where(
                ProjectIgnoredWork.project_id == piw.project_id,
                ProjectIgnoredWork.work_id == target_id,
            )
        ).one_or_none()
        if existing:
            db.delete(piw)
        else:
            piw.work_id = target_id


def _merge_authors(db: Session, source: Work, target: Work) -> None:
    """Move source's author links to target, skipping duplicates."""
    max_pos = db.scalar(
        select(func.max(WorkAuthor.position)).where(WorkAuthor.work_id == target.id)
    ) or 0
    for wa in list(source.authors):
        existing = db.scalars(
            select(WorkAuthor).where(
                WorkAuthor.work_id == target.id,
                WorkAuthor.author_id == wa.author_id,
            )
        ).one_or_none()
        if existing:
            db.delete(wa)
        else:
            max_pos += 1
            wa.work_id = target.id
            wa.position = max_pos


def _merge_locations(db: Session, source: Work, target: Work) -> None:
    """Move source's locations to target."""
    for loc in list(source.locations):
        loc.work_id = target.id


def _fill_metadata(db: Session, source: Work, target: Work) -> None:
    """Fill None fields on target from source. Take higher citation_count.

    Unique fields are nulled on source and flushed first so that
    SQLAlchemy doesn't emit two conflicting UPDATEs in the same batch.
    """
    _UNIQUE_FIELDS = ("doi", "arxiv_id", "openalex_id", "bibtex_key")

    # Snapshot values before nulling source
    saved = {f: getattr(source, f) for f in _UNIQUE_FIELDS}
    for f in _UNIQUE_FIELDS:
        setattr(source, f, None)
    # Flush the nulls so the DB releases the unique slots
    db.flush()

    for field in (
        "doi", "arxiv_id", "openalex_id", "abstract", "publication_year",
        "venue_id", "bibtex_key", "bibtex_entry", "pdf_path",
        "doi_auto_resolved", "created_by",
    ):
        src_val = saved[field] if field in saved else getattr(source, field)
        if getattr(target, field) is None and src_val is not None:
            setattr(target, field, src_val)
    # Title: prefer non-"Untitled" / non-"(untitled)"
    if target.title.lower() in ("untitled", "(untitled)") and source.title.lower() not in ("untitled", "(untitled)"):
        target.title = source.title
    # Take higher citation count
    src_cc = source.citation_count or 0
    tgt_cc = target.citation_count or 0
    if src_cc > tgt_cc:
        target.citation_count = source.citation_count


@router.post("/{target_id}/merge/{source_id}", response_model=WorkDetail)
def merge_works(target_id: int, source_id: int, db: Session = Depends(get_db)):
    """Merge source work into target work, then delete source."""
    if target_id == source_id:
        raise HTTPException(status_code=400, detail="Cannot merge a work into itself")

    target = _get_work(db, target_id)
    source = _get_work(db, source_id)

    _repoint_citations_citing(db, source.id, target.id)
    _repoint_citations_cited(db, source.id, target.id)
    _repoint_topic_list_works(db, source.id, target.id)
    _repoint_project_ignored_works(db, source.id, target.id)
    _merge_authors(db, source, target)
    _merge_locations(db, source, target)
    _fill_metadata(db, source, target)

    db.delete(source)
    db.flush()

    # Reload with eager joins for the response
    result = db.scalars(
        select(Work)
        .where(Work.id == target.id)
        .options(
            joinedload(Work.venue),
            joinedload(Work.locations),
            joinedload(Work.authors).joinedload(WorkAuthor.author),
        )
    ).unique().one()

    db.commit()
    return _work_detail(result)
