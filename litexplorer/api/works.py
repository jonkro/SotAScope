"""CRUD routes for works, locations, authors, citations, and BibTeX import."""

import logging
import os
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete as sa_delete, exists, func, or_, select
from sqlalchemy.orm import Session, joinedload

from litexplorer.api.deps import get_db
from litexplorer.config import settings
from litexplorer.models.library import (
    Author,
    Citation,
    Venue,
    VenueAlias,
    Work,
    WorkAuthor,
    WorkDOI,
    WorkLocation,
    WorkNote,
    WorkPDF,
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
    WorkPDFOut,
    WorkUpdate,
)
from litexplorer.schemas.notes import WorkNoteCreate, WorkNoteOut, WorkNoteUpdate

router = APIRouter(prefix="/api/works", tags=["works"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_work(db: Session, work_id: int) -> Work:
    work = db.get(Work, work_id)
    if not work:
        raise HTTPException(status_code=404, detail="Work not found")
    return work


def _get_pdf_root(db: Session) -> Path:
    """Resolve PDF storage root: DB setting > config default."""
    from litexplorer.api.settings import get_setting_value
    custom = get_setting_value(db, "pdf_storage_path")
    if custom:
        return Path(custom)
    return settings.pdf_dir


def _secure_filename(name: str) -> str:
    """Sanitize a filename: strip path components, replace unsafe chars."""
    name = os.path.basename(name)
    name = re.sub(r"[^\w.\-]", "_", name)
    name = name.lstrip("._")
    return name or "unnamed.pdf"


def _move_to_orphaned(src: Path, orphaned_dir: Path) -> None:
    """Move a file to the orphaned directory, appending _N on collision."""
    orphaned_dir.mkdir(parents=True, exist_ok=True)
    dest = orphaned_dir / src.name
    counter = 1
    while dest.exists():
        stem = src.stem
        dest = orphaned_dir / f"{stem}_{counter}{src.suffix}"
        counter += 1
    shutil.move(str(src), str(dest))


def _txt_path_for(pdf_root: Path, work_id: int, filename: str) -> Path:
    """Return the companion .txt path for a PDF file."""
    return pdf_root / str(work_id) / Path(filename).with_suffix(".txt")


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
    # Move PDF files to orphaned directory before cascade-deleting DB rows
    pdf_root = _get_pdf_root(db)
    work_pdf_dir = pdf_root / str(work_id)
    if work_pdf_dir.is_dir():
        orphaned_dir = pdf_root / "_orphaned" / str(work_id)
        for f in work_pdf_dir.iterdir():
            if f.is_file():
                _move_to_orphaned(f, orphaned_dir)
        # Remove the now-empty directory
        try:
            work_pdf_dir.rmdir()
        except OSError:
            pass
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
        "venue_id", "bibtex_key", "bibtex_entry",
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


# ---------------------------------------------------------------------------
# PDFs
# ---------------------------------------------------------------------------

def _register_and_extract_pdf(
    db: Session, work_id: int, pdf_root: Path, safe_name: str, dest: Path
) -> WorkPDF:
    """Create a WorkPDF record for a file already saved to disk and run text extraction.

    This is shared by the manual upload flow and the OA fetch flow.
    """
    from litexplorer.services.pdf import ExtractionError, extract_pdf_text

    existing_count = db.scalar(
        select(func.count()).select_from(WorkPDF).where(WorkPDF.work_id == work_id)
    )
    is_primary = existing_count == 0

    pdf = WorkPDF(work_id=work_id, filename=safe_name, is_primary=is_primary)
    db.add(pdf)
    db.commit()
    db.refresh(pdf)

    # Auto-extract text; failure is non-fatal
    txt_path = _txt_path_for(pdf_root, work_id, safe_name)
    try:
        text = extract_pdf_text(dest)
        txt_path.write_text(text, encoding="utf-8")
        pdf.extraction_status = "ready"
    except ExtractionError as exc:
        logger.warning("PDF text extraction failed for %s: %s", dest, exc)
        pdf.extraction_status = "failed"
    except Exception as exc:
        logger.warning("Unexpected error extracting text from %s: %s", dest, exc)
        pdf.extraction_status = "failed"
    db.commit()
    db.refresh(pdf)
    return pdf


@router.post("/{work_id}/pdfs", response_model=WorkPDFOut, status_code=201)
def upload_pdf(work_id: int, file: UploadFile, db: Session = Depends(get_db)):
    """Upload a PDF and attach it to a work."""
    _get_work(db, work_id)
    safe_name = _secure_filename(file.filename or "unnamed.pdf")

    pdf_root = _get_pdf_root(db)
    work_dir = pdf_root / str(work_id)
    work_dir.mkdir(parents=True, exist_ok=True)

    dest = work_dir / safe_name
    if dest.exists():
        raise HTTPException(status_code=409, detail=f"File '{safe_name}' already exists for this work")

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    return _register_and_extract_pdf(db, work_id, pdf_root, safe_name, dest)


@router.post("/{work_id}/pdfs/fetch", response_model=WorkPDFOut, status_code=201)
def fetch_pdf_oa(work_id: int, db: Session = Depends(get_db)):
    """Fetch a PDF from open-access sources (arXiv, Unpaywall) and attach it to a work."""
    from litexplorer.api.enrichment import _get_contact_email, _get_ssl_verify
    from litexplorer.external.pdf_fetch import PDFFetchError, fetch_pdf_for_work

    work = _get_work(db, work_id)

    if not work.arxiv_id and not work.doi:
        raise HTTPException(
            status_code=400,
            detail="Work has no arXiv ID or DOI — cannot fetch PDF automatically",
        )

    ssl_verify = _get_ssl_verify(db)
    email = _get_contact_email(db) or ""

    try:
        result = fetch_pdf_for_work(None, work, verify=ssl_verify, email=email)
    except PDFFetchError as exc:
        raise HTTPException(status_code=502, detail=f"PDF download failed: {exc}")

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No open-access PDF found. "
                "The paper may be paywalled. You can upload a PDF manually."
            ),
        )

    pdf_bytes, suggested_filename = result
    safe_name = _secure_filename(suggested_filename)

    pdf_root = _get_pdf_root(db)
    work_dir = pdf_root / str(work_id)
    work_dir.mkdir(parents=True, exist_ok=True)

    dest = work_dir / safe_name
    # Avoid collision with an already-existing file
    if dest.exists():
        stem = Path(safe_name).stem
        safe_name = f"{stem}_fetched.pdf"
        dest = work_dir / safe_name

    dest.write_bytes(pdf_bytes)
    return _register_and_extract_pdf(db, work_id, pdf_root, safe_name, dest)


@router.get("/{work_id}/pdfs", response_model=list[WorkPDFOut])
def list_pdfs(work_id: int, db: Session = Depends(get_db)):
    """List all PDFs attached to a work."""
    _get_work(db, work_id)
    return db.scalars(
        select(WorkPDF).where(WorkPDF.work_id == work_id).order_by(WorkPDF.id)
    ).all()


@router.get("/{work_id}/pdfs/{pdf_id}/file")
def serve_pdf(work_id: int, pdf_id: int, db: Session = Depends(get_db)):
    """Serve a PDF file for download/viewing."""
    pdf = db.scalars(
        select(WorkPDF).where(WorkPDF.id == pdf_id, WorkPDF.work_id == work_id)
    ).one_or_none()
    if not pdf:
        raise HTTPException(status_code=404, detail="PDF not found")

    pdf_root = _get_pdf_root(db)
    file_path = pdf_root / str(work_id) / pdf.filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="PDF file not found on disk")

    return FileResponse(file_path, media_type="application/pdf")


@router.patch("/{work_id}/pdfs/{pdf_id}/set-primary", response_model=WorkPDFOut)
def set_pdf_primary(work_id: int, pdf_id: int, db: Session = Depends(get_db)):
    """Set a PDF as the primary for this work."""
    pdf = db.scalars(
        select(WorkPDF).where(WorkPDF.id == pdf_id, WorkPDF.work_id == work_id)
    ).one_or_none()
    if not pdf:
        raise HTTPException(status_code=404, detail="PDF not found")

    # Unset all others
    others = db.scalars(
        select(WorkPDF).where(WorkPDF.work_id == work_id, WorkPDF.id != pdf_id)
    ).all()
    for other in others:
        other.is_primary = False
    pdf.is_primary = True
    db.commit()
    db.refresh(pdf)
    return pdf


@router.delete("/{work_id}/pdfs/{pdf_id}", status_code=204)
def delete_pdf(work_id: int, pdf_id: int, db: Session = Depends(get_db)):
    """Detach a PDF — moves the file to _orphaned/ and deletes the DB row."""
    pdf = db.scalars(
        select(WorkPDF).where(WorkPDF.id == pdf_id, WorkPDF.work_id == work_id)
    ).one_or_none()
    if not pdf:
        raise HTTPException(status_code=404, detail="PDF not found")

    pdf_root = _get_pdf_root(db)
    orphaned_dir = pdf_root / "_orphaned" / str(work_id)
    file_path = pdf_root / str(work_id) / pdf.filename
    if file_path.is_file():
        _move_to_orphaned(file_path, orphaned_dir)
    txt_path = _txt_path_for(pdf_root, work_id, pdf.filename)
    if txt_path.is_file():
        _move_to_orphaned(txt_path, orphaned_dir)

    was_primary = pdf.is_primary
    db.delete(pdf)
    db.flush()

    # If deleted PDF was primary, reassign to the lowest-id remaining PDF
    if was_primary:
        next_pdf = db.scalars(
            select(WorkPDF).where(WorkPDF.work_id == work_id).order_by(WorkPDF.id).limit(1)
        ).one_or_none()
        if next_pdf:
            next_pdf.is_primary = True

    db.commit()


@router.post("/{work_id}/pdfs/{pdf_id}/extract-text")
def extract_pdf_text_endpoint(work_id: int, pdf_id: int, db: Session = Depends(get_db)):
    """Extract (or re-extract) text from a PDF. Returns char_count on success."""
    from litexplorer.services.pdf import ExtractionError, extract_pdf_text

    pdf = db.scalars(
        select(WorkPDF).where(WorkPDF.id == pdf_id, WorkPDF.work_id == work_id)
    ).one_or_none()
    if not pdf:
        raise HTTPException(status_code=404, detail="PDF not found")

    pdf_root = _get_pdf_root(db)
    file_path = pdf_root / str(work_id) / pdf.filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="PDF file not found on disk")

    txt_path = _txt_path_for(pdf_root, work_id, pdf.filename)
    try:
        text = extract_pdf_text(file_path)
        txt_path.write_text(text, encoding="utf-8")
        pdf.extraction_status = "ready"
        db.commit()
        return {"status": "ready", "char_count": len(text)}
    except ExtractionError as exc:
        pdf.extraction_status = "failed"
        db.commit()
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/{work_id}/pdfs/{pdf_id}/text")
def get_pdf_text(work_id: int, pdf_id: int, db: Session = Depends(get_db)):
    """Serve the extracted text for a PDF as text/plain."""
    from fastapi.responses import PlainTextResponse

    pdf = db.scalars(
        select(WorkPDF).where(WorkPDF.id == pdf_id, WorkPDF.work_id == work_id)
    ).one_or_none()
    if not pdf:
        raise HTTPException(status_code=404, detail="PDF not found")

    if pdf.extraction_status == "pending":
        raise HTTPException(status_code=404, detail="No extracted text found — run extraction first")
    if pdf.extraction_status == "failed":
        raise HTTPException(status_code=422, detail="Extraction failed or PDF has no text layer")

    # Status is "ready" — serve the .txt file
    pdf_root = _get_pdf_root(db)
    txt_path = _txt_path_for(pdf_root, work_id, pdf.filename)
    if not txt_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Text file missing from disk — please re-extract",
        )

    return PlainTextResponse(txt_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# DOI aliases
# ---------------------------------------------------------------------------


@router.get("/{work_id}/doi-aliases")
def list_doi_aliases(work_id: int, db: Session = Depends(get_db)) -> list[str]:
    """Return the list of secondary DOIs for a work."""
    work = _get_work(db, work_id)
    return [a.doi for a in work.doi_aliases]


@router.post("/{work_id}/doi-aliases", status_code=201)
def add_doi_alias(
    work_id: int,
    body: dict,
    db: Session = Depends(get_db),
) -> list[str]:
    """Add a secondary DOI to a work. Body: {"doi": "10.1234/..."}"""
    work = _get_work(db, work_id)
    doi = (body.get("doi") or "").strip().lower()
    if not doi:
        raise HTTPException(status_code=422, detail="doi is required")
    # Avoid adding the same DOI as the primary or a duplicate alias
    if doi == (work.doi or "").lower():
        raise HTTPException(status_code=409, detail="DOI is already the primary DOI for this work")
    if any(a.doi.lower() == doi for a in work.doi_aliases):
        raise HTTPException(status_code=409, detail="DOI already in alias list")
    work.doi_aliases.append(WorkDOI(work_id=work_id, doi=doi))
    db.commit()
    return [a.doi for a in work.doi_aliases]


@router.delete("/{work_id}/doi-aliases")
def remove_doi_alias(
    work_id: int,
    body: dict,
    db: Session = Depends(get_db),
) -> list[str]:
    """Remove a secondary DOI from a work. Body: {"doi": "10.1234/..."}"""
    work = _get_work(db, work_id)
    doi = (body.get("doi") or "").strip().lower()
    alias = next((a for a in work.doi_aliases if a.doi.lower() == doi), None)
    if alias is None:
        raise HTTPException(status_code=404, detail="DOI alias not found")
    db.delete(alias)
    db.commit()
    return [a.doi for a in work.doi_aliases]


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


@router.get("/{work_id}/notes", response_model=list[WorkNoteOut])
def list_notes(
    work_id: int,
    project_id: int | None = None,
    db: Session = Depends(get_db),
):
    """List notes for a work.

    If project_id is provided, returns general notes (project_id=NULL) AND
    notes for that specific project.  If omitted, returns only general notes.
    """
    _get_work(db, work_id)

    stmt = select(WorkNote).where(WorkNote.work_id == work_id)
    if project_id is not None:
        stmt = stmt.where(
            (WorkNote.project_id == None) | (WorkNote.project_id == project_id)  # noqa: E711
        )
    else:
        stmt = stmt.where(WorkNote.project_id == None)  # noqa: E711

    stmt = stmt.order_by(WorkNote.created_at)
    notes = db.scalars(stmt).all()
    return [WorkNoteOut.model_validate(n) for n in notes]


@router.post("/{work_id}/notes", response_model=WorkNoteOut, status_code=201)
def create_note(
    work_id: int,
    body: WorkNoteCreate,
    db: Session = Depends(get_db),
):
    """Create a user note on a work."""
    _get_work(db, work_id)

    note = WorkNote(
        work_id=work_id,
        project_id=body.project_id,
        content=body.content,
        note_type=body.note_type,
        provenance="user",
        model_id=None,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return WorkNoteOut.model_validate(note)


@router.patch("/{work_id}/notes/{note_id}", response_model=WorkNoteOut)
def update_note(
    work_id: int,
    note_id: int,
    body: WorkNoteUpdate,
    db: Session = Depends(get_db),
):
    """Update a note. AI-provenance notes become 'ai_reviewed' on any edit."""
    note = db.get(WorkNote, note_id)
    if not note or note.work_id != work_id:
        raise HTTPException(status_code=404, detail="Note not found")

    if body.content is not None:
        note.content = body.content
    if body.note_type is not None:
        note.note_type = body.note_type
    if body.is_outdated is not None:
        note.is_outdated = body.is_outdated

    # Explicit provenance override (e.g. "Accept" button sets "ai_reviewed")
    if body.provenance is not None:
        note.provenance = body.provenance
    elif body.content is not None and note.provenance == "ai":
        # Editing AI content implies review
        note.provenance = "ai_reviewed"

    db.commit()
    db.refresh(note)
    return WorkNoteOut.model_validate(note)


@router.delete("/{work_id}/notes/{note_id}", status_code=204)
def delete_note(
    work_id: int,
    note_id: int,
    db: Session = Depends(get_db),
):
    """Delete a note."""
    note = db.get(WorkNote, note_id)
    if not note or note.work_id != work_id:
        raise HTTPException(status_code=404, detail="Note not found")

    db.delete(note)
    db.commit()
