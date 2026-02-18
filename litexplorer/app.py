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
    _seed_default_fields()
    _normalize_existing_venue_names()
    yield


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

app.include_router(works_router)
app.include_router(authors_router)
app.include_router(venues_router)
app.include_router(fields_router)
app.include_router(projects_router)
app.include_router(enrichment_router)
app.include_router(timeline_router)

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
