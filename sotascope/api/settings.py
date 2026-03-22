"""Settings API router — read and update application settings."""

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sotascope.api.deps import get_db
from sotascope.config import settings as app_settings
from sotascope.models.settings import Setting
from sotascope.schemas.settings import SettingOut, SettingUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


def get_setting_value(db: Session, key: str) -> str | None:
    """Read a setting value from the DB. Returns None if missing or empty."""
    row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if row is None or not row.value:
        return None
    return row.value


@router.get("", response_model=list[SettingOut])
def list_settings(db: Session = Depends(get_db)):
    """Return all application settings."""
    rows = db.scalars(select(Setting).order_by(Setting.key)).all()
    return [SettingOut.model_validate(r) for r in rows]


class PDFMigrateRequest(BaseModel):
    new_path: str


class PDFMigrateResponse(BaseModel):
    old_path: str
    new_path: str
    files_moved: int
    directories_moved: int
    errors: list[str]


@router.post("/pdf_storage_path/migrate", response_model=PDFMigrateResponse)
def migrate_pdf_storage(body: PDFMigrateRequest, db: Session = Depends(get_db)):
    """Move all PDFs from the current storage path to a new location and update the setting."""
    # Resolve old path
    old_val = get_setting_value(db, "pdf_storage_path")
    old_path = Path(old_val) if old_val else app_settings.pdf_dir
    old_path = old_path.resolve()

    # Resolve new path (empty string = revert to config default)
    new_path = Path(body.new_path).resolve() if body.new_path else app_settings.pdf_dir.resolve()

    # No-op if same path
    if old_path == new_path:
        return PDFMigrateResponse(
            old_path=str(old_path),
            new_path=str(new_path),
            files_moved=0,
            directories_moved=0,
            errors=[],
        )

    # Create new path if needed
    new_path.mkdir(parents=True, exist_ok=True)

    files_moved = 0
    directories_moved = 0
    errors: list[str] = []

    if old_path.exists() and old_path.is_dir():
        for entry in list(old_path.iterdir()):
            dest = new_path / entry.name
            try:
                if entry.is_dir():
                    if dest.exists() and dest.is_dir():
                        # Move individual files to avoid nesting
                        for child in list(entry.iterdir()):
                            child_dest = dest / child.name
                            shutil.move(str(child), str(child_dest))
                            if child.is_file():
                                files_moved += 1
                            else:
                                directories_moved += 1
                        # Remove the now-empty source directory
                        entry.rmdir()
                    else:
                        shutil.move(str(entry), str(dest))
                        directories_moved += 1
                else:
                    shutil.move(str(entry), str(dest))
                    files_moved += 1
            except Exception as exc:
                errors.append(f"{entry.name}: {exc}")

    # Update the DB setting
    new_val = str(new_path) if new_path != app_settings.pdf_dir.resolve() else ""
    row = db.execute(select(Setting).where(Setting.key == "pdf_storage_path")).scalar_one_or_none()
    if row is None:
        db.add(Setting(key="pdf_storage_path", value=new_val))
    else:
        row.value = new_val
    db.commit()

    return PDFMigrateResponse(
        old_path=str(old_path),
        new_path=str(new_path),
        files_moved=files_moved,
        directories_moved=directories_moved,
        errors=errors,
    )


class BackfillVenuesResponse(BaseModel):
    updated: int
    message: str


@router.post("/backfill-venues", response_model=BackfillVenuesResponse)
def backfill_venues(db: Session = Depends(get_db)):
    """Scan cached OpenAlex API responses and populate venue_id for works that lack it.

    Also upgrades works whose current venue is a provisional repository (arXiv, bioRxiv,
    etc.) if the cache contains a real venue (journal or conference) for them.
    Reads only already-cached OA data — no external API calls are made.
    Safe to call multiple times (idempotent).
    """
    import json as _json

    from sqlalchemy import or_ as _or
    from sqlalchemy import select as _select

    from sotascope.external.openalex import parse_work as _parse_work
    from sotascope.models.cache import ApiCache
    from sotascope.models.library import Venue, Work
    from sotascope.services.enrichment import EnrichmentService

    # Include works with no venue AND works whose current venue is a provisional
    # repository (arXiv, bioRxiv, etc.) — they may have a real venue in cache.
    works_needing = db.scalars(
        _select(Work)
        .outerjoin(Venue, Work.venue_id == Venue.id)
        .where(
            _or(
                Work.venue_id.is_(None),
                Venue.venue_type == 'repository',
            )
        )
    ).all()

    if not works_needing:
        return BackfillVenuesResponse(
            updated=0,
            message="All works already have real venues — nothing to do.",
        )

    by_openalex: dict = {}
    by_doi: dict = {}
    for w in works_needing:
        if w.openalex_id:
            by_openalex[w.openalex_id] = w
        if w.doi:
            by_doi[w.doi.lower()] = w

    if not by_openalex and not by_doi:
        return BackfillVenuesResponse(
            updated=0,
            message="All works already have real venues — nothing to do.",
        )

    service = EnrichmentService(db=db, client=None)  # type: ignore[arg-type]
    filled = 0

    def _try_apply_venue(raw: dict) -> None:
        nonlocal filled
        if not isinstance(raw, dict):
            return
        oa_id = (raw.get("id") or "").replace("https://openalex.org/", "") or None
        work = by_openalex.get(oa_id) if oa_id else None
        if work is None:
            doi = (raw.get("doi") or "").replace("https://doi.org/", "").lower()
            work = by_doi.get(doi) if doi else None
        if work is None:
            return
        # Skip if work already has a real (non-provisional) venue
        if work.venue_id is not None:
            current_venue = db.get(Venue, work.venue_id)
            if current_venue and current_venue.venue_type != 'repository':
                return
        ext_work = _parse_work(raw)
        if not ext_work.venue or ext_work.venue.venue_type == 'repository':
            return  # New venue is also provisional — don't bother
        venue = service._resolve_venue(ext_work)
        if venue:
            work.venue_id = venue.id
            filled += 1

    for prefix in ("work:doi:%", "work:arxiv:%", "work:openalex:%"):
        for cached_json in db.execute(
            _select(ApiCache.response_json).where(
                ApiCache.source == "openalex",
                ApiCache.query_key.like(prefix),
            )
        ).scalars():
            try:
                _try_apply_venue(_json.loads(cached_json))
            except (ValueError, KeyError, TypeError):
                continue

    for cached_json in db.execute(
        _select(ApiCache.response_json).where(
            ApiCache.source == "openalex",
            ApiCache.query_key.like("backward_citations:%")
            | ApiCache.query_key.like("forward_citations:%"),
        )
    ).scalars():
        try:
            raw_list = _json.loads(cached_json)
            if not isinstance(raw_list, list):
                continue
            for raw in raw_list:
                _try_apply_venue(raw)
        except (ValueError, KeyError, TypeError):
            continue

    if filled:
        db.commit()
        noun = "work" if filled == 1 else "works"
        return BackfillVenuesResponse(
            updated=filled,
            message=f"Updated {filled} {noun} with venue data.",
        )

    return BackfillVenuesResponse(
        updated=0,
        message="All works already have real venues — nothing to do.",
    )


@router.patch("/{key}", response_model=SettingOut)
def update_setting(key: str, body: SettingUpdate, db: Session = Depends(get_db)):
    """Update a setting's value."""
    row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
    row.value = body.value
    db.commit()
    db.refresh(row)
    return SettingOut.model_validate(row)
