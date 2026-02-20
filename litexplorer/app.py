"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from litexplorer.database import SessionLocal, init_db

_frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"

_DEFAULT_FIELDS = ["AI/ML", "Computer Networks"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _migrate_schema()
    _seed_default_fields()
    _seed_default_settings()
    _normalize_existing_venue_names()
    yield


def _migrate_schema() -> None:
    """Run lightweight schema migrations for new columns."""
    from sqlalchemy import inspect as sa_inspect, text

    db = SessionLocal()
    try:
        inspector = sa_inspect(db.bind)
        columns = {c["name"] for c in inspector.get_columns("works")}
        if "doi_auto_resolved" not in columns:
            db.execute(text("ALTER TABLE works ADD COLUMN doi_auto_resolved BOOLEAN"))
            db.commit()

        alias_cols = {c["name"] for c in inspector.get_columns("venue_aliases")}
        if "sort_order" not in alias_cols:
            db.execute(text("ALTER TABLE venue_aliases ADD COLUMN sort_order INTEGER DEFAULT 0"))
            db.execute(text("UPDATE venue_aliases SET sort_order = id"))
            db.commit()

        # Create work_pdfs table if it doesn't exist
        existing_tables = set(inspector.get_table_names())
        if "work_pdfs" not in existing_tables:
            db.execute(text(
                "CREATE TABLE work_pdfs ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,"
                "  filename VARCHAR(512) NOT NULL,"
                "  is_primary BOOLEAN DEFAULT 0,"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                ")"
            ))
            db.commit()

        # Drop legacy pdf_path column from works (SQLite 3.35+)
        work_cols = {c["name"] for c in inspector.get_columns("works")}
        if "pdf_path" in work_cols:
            db.execute(text("ALTER TABLE works DROP COLUMN pdf_path"))
            db.commit()

        # Enable AUTOINCREMENT tracking for existing works table
        has_seq = db.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'")
        ).one_or_none()
        if has_seq:
            row = db.execute(
                text("SELECT seq FROM sqlite_sequence WHERE name='works'")
            ).one_or_none()
            if row is None:
                max_id = db.execute(text("SELECT COALESCE(MAX(id), 0) FROM works")).scalar()
                db.execute(text("INSERT INTO sqlite_sequence (name, seq) VALUES ('works', :seq)"),
                           {"seq": max_id})
                db.commit()
    finally:
        db.close()


def _seed_default_fields() -> None:
    """Create default research fields if they don't exist yet."""
    from litexplorer.models.library import Field
    from sqlalchemy import select

    db = SessionLocal()
    try:
        for name in _DEFAULT_FIELDS:
            exists = db.scalars(select(Field).where(Field.name == name)).one_or_none()
            if not exists:
                db.add(Field(name=name))
        db.commit()
    finally:
        db.close()


def _seed_default_settings() -> None:
    """Create default settings rows if they don't exist yet."""
    from litexplorer.models.settings import Setting
    from sqlalchemy import select

    _DEFAULTS = [
        (
            "api_contact_email",
            "",
            "Email address used for polite-pool access to OpenAlex and Crossref APIs",
        ),
        (
            "pdf_storage_path",
            "",
            "Absolute path for PDF storage. Defaults to {data_dir}/pdfs/ if empty.",
        ),
    ]

    db = SessionLocal()
    try:
        for key, value, description in _DEFAULTS:
            exists = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
            if not exists:
                db.add(Setting(key=key, value=value, description=description))
        db.commit()
    finally:
        db.close()


def _normalize_existing_venue_names() -> None:
    """One-time migration: normalize venue names (strip 'Proceedings' prefixes,
    calendar years, ordinal numbers) and merge duplicates that result."""
    import logging
    from sqlalchemy import select, update
    from litexplorer.models.library import Venue, VenueAlias, Work
    from litexplorer.services.enrichment import normalize_venue_name

    logger = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        venues = db.scalars(select(Venue)).all()

        # Group venues by their normalized name to detect merges needed
        norm_groups: dict[str, list[Venue]] = {}
        for v in venues:
            norm = normalize_venue_name(v.name)
            norm_groups.setdefault(norm, []).append(v)

        for norm_name, group in norm_groups.items():
            if len(group) == 1:
                v = group[0]
                if v.name != norm_name:
                    # Store old name as alias, update canonical name
                    existing_alias = db.execute(
                        select(VenueAlias).where(
                            VenueAlias.venue_id == v.id, VenueAlias.alias == v.name
                        )
                    ).scalar_one_or_none()
                    if not existing_alias:
                        db.add(VenueAlias(venue_id=v.id, alias=v.name))
                    v.name = norm_name
                    logger.info("Normalized venue '%s' → '%s'", group[0].name, norm_name)
            else:
                # Multiple venues share the same normalized name — merge into the first
                primary = group[0]
                old_primary_name = primary.name
                if primary.name != norm_name:
                    existing_alias = db.execute(
                        select(VenueAlias).where(
                            VenueAlias.venue_id == primary.id, VenueAlias.alias == primary.name
                        )
                    ).scalar_one_or_none()
                    if not existing_alias:
                        db.add(VenueAlias(venue_id=primary.id, alias=primary.name))
                    primary.name = norm_name

                for dup in group[1:]:
                    logger.info(
                        "Merging venue '%s' (id=%d) into '%s' (id=%d)",
                        dup.name, dup.id, norm_name, primary.id,
                    )
                    # Store dup's name as alias on primary
                    existing_alias = db.execute(
                        select(VenueAlias).where(
                            VenueAlias.venue_id == primary.id, VenueAlias.alias == dup.name
                        )
                    ).scalar_one_or_none()
                    if not existing_alias:
                        db.add(VenueAlias(venue_id=primary.id, alias=dup.name))

                    # Move dup's aliases to primary
                    dup_aliases = db.scalars(
                        select(VenueAlias).where(VenueAlias.venue_id == dup.id)
                    ).all()
                    for da in dup_aliases:
                        existing = db.execute(
                            select(VenueAlias).where(
                                VenueAlias.venue_id == primary.id, VenueAlias.alias == da.alias
                            )
                        ).scalar_one_or_none()
                        if not existing:
                            da.venue_id = primary.id
                        else:
                            db.delete(da)

                    # Fill missing metadata on primary from dup
                    if primary.openalex_id is None and dup.openalex_id:
                        primary.openalex_id = dup.openalex_id
                    if primary.issn is None and dup.issn:
                        primary.issn = dup.issn
                    if primary.publisher is None and dup.publisher:
                        primary.publisher = dup.publisher
                    if primary.venue_type is None and dup.venue_type:
                        primary.venue_type = dup.venue_type
                    # Keep the better (lower) tier
                    if dup.tier < primary.tier:
                        primary.tier = dup.tier

                    # Re-point all works from dup to primary
                    db.execute(
                        update(Work).where(Work.venue_id == dup.id).values(venue_id=primary.id)
                    )

                    # Delete the duplicate venue
                    db.delete(dup)

                if old_primary_name != norm_name:
                    logger.info("Normalized venue '%s' → '%s'", old_primary_name, norm_name)

        db.commit()
    finally:
        db.close()


app = FastAPI(title="LitExplorer", version="0.1.0", lifespan=lifespan)

# Import and include routers after app creation to avoid circular imports.
from litexplorer.api.works import authors_router, router as works_router  # noqa: E402
from litexplorer.api.venues import router as venues_router  # noqa: E402
from litexplorer.api.fields import router as fields_router  # noqa: E402
from litexplorer.api.projects import router as projects_router  # noqa: E402
from litexplorer.api.enrichment import router as enrichment_router  # noqa: E402
from litexplorer.api.timeline import router as timeline_router  # noqa: E402
from litexplorer.api.settings import router as settings_router  # noqa: E402
from litexplorer.api.filesystem import router as filesystem_router  # noqa: E402

app.include_router(works_router)
app.include_router(authors_router)
app.include_router(venues_router)
app.include_router(fields_router)
app.include_router(projects_router)
app.include_router(enrichment_router)
app.include_router(timeline_router)
app.include_router(settings_router)
app.include_router(filesystem_router)

# Serve built frontend (only when frontend/dist exists)
if _frontend_dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_frontend_dist / "assets"), name="static")

    @app.get("/{path:path}")
    async def _spa_fallback(path: str):
        """Serve index.html for all non-API routes (SPA client-side routing)."""
        file = _frontend_dist / path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(_frontend_dist / "index.html")
