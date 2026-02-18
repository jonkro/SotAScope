"""CRUD routes for works, locations, authors, citations, and BibTeX import."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from litexplorer.api.deps import get_db
from litexplorer.models.library import (
    Author,
    Citation,
    Work,
    WorkAuthor,
    WorkLocation,
)
from litexplorer.schemas.works import (
    AuthorCreate,
    AuthorOut,
    BibtexImportRequest,
    BibtexImportResult,
    CitationWorkBrief,
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
    return WorkDetail(
        **{c.key: getattr(work, c.key) for c in Work.__table__.columns},
        venue_name=work.venue.name if work.venue else None,
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

@router.get("", response_model=list[WorkOut])
def list_works(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, description="Search title"),
    venue_id: int | None = Query(None),
    year: int | None = Query(None),
    db: Session = Depends(get_db),
):
    stmt = select(Work).order_by(Work.id)
    if q:
        stmt = stmt.where(Work.title.ilike(f"%{q}%"))
    if venue_id is not None:
        stmt = stmt.where(Work.venue_id == venue_id)
    if year is not None:
        stmt = stmt.where(Work.publication_year == year)
    stmt = stmt.offset(offset).limit(limit)
    return db.scalars(stmt).all()


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
