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
    _backfill_citations_by_year()
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

        # Add citations_by_year JSON column to works
        work_cols = {c["name"] for c in inspector.get_columns("works")}
        if "citations_by_year" not in work_cols:
            db.execute(text("ALTER TABLE works ADD COLUMN citations_by_year JSON"))
            db.commit()
            work_cols.add("citations_by_year")

        # Drop legacy pdf_path column from works (SQLite 3.35+)
        if "pdf_path" in work_cols:
            db.execute(text("ALTER TABLE works DROP COLUMN pdf_path"))
            db.commit()

        # Create work_notes table if it doesn't exist
        if "work_notes" not in existing_tables:
            db.execute(text(
                "CREATE TABLE work_notes ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,"
                "  project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,"
                "  content TEXT NOT NULL,"
                "  note_type VARCHAR(64),"
                "  provenance VARCHAR(32) NOT NULL DEFAULT 'user',"
                "  model_id VARCHAR(128),"
                "  is_outdated BOOLEAN DEFAULT 0,"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                ")"
            ))
            db.commit()

        # Add extraction_status column to work_pdfs
        pdf_cols = {c["name"] for c in inspector.get_columns("work_pdfs")}
        if "extraction_status" not in pdf_cols:
            db.execute(text(
                "ALTER TABLE work_pdfs ADD COLUMN extraction_status VARCHAR(16) NOT NULL DEFAULT 'pending'"
            ))
            db.commit()

        # Add semantic_scholar_id column to works (no unique constraint)
        if "semantic_scholar_id" not in columns:
            db.execute(text("ALTER TABLE works ADD COLUMN semantic_scholar_id VARCHAR(128)"))
            db.commit()

        # Add source column to citations (was added with S2 integration; backfill with 'openalex')
        citation_cols = {c["name"] for c in inspector.get_columns("citations")}
        if "source" not in citation_cols:
            db.execute(text("ALTER TABLE citations ADD COLUMN source VARCHAR(32)"))
            db.execute(text("UPDATE citations SET source = 'openalex' WHERE source IS NULL"))
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
        # Create work_dois table if it doesn't exist
        if "work_dois" not in existing_tables:
            db.execute(text(
                "CREATE TABLE work_dois ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,"
                "  doi VARCHAR(255) NOT NULL"
                ")"
            ))
            db.commit()

        # Create extraction_schemas table if it doesn't exist
        if "extraction_schemas" not in existing_tables:
            db.execute(text(
                "CREATE TABLE extraction_schemas ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,"
                "  title VARCHAR(256) NOT NULL,"
                "  description TEXT,"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                ")"
            ))
            db.commit()

        # Create extraction_columns table if it doesn't exist
        if "extraction_columns" not in existing_tables:
            db.execute(text(
                "CREATE TABLE extraction_columns ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  schema_id INTEGER NOT NULL REFERENCES extraction_schemas(id) ON DELETE CASCADE,"
                "  name VARCHAR(256) NOT NULL,"
                "  prompt TEXT NOT NULL,"
                "  description TEXT,"
                "  allowed_values JSON,"
                "  sort_order INTEGER DEFAULT 0,"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                ")"
            ))
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
        (
            "ssl_verify",
            "true",
            "Verify SSL certificates when calling external APIs (OpenAlex, Crossref). "
            "Set to 'false' only if you are behind a corporate proxy that intercepts HTTPS traffic. "
            "The preferred fix is to install your corporate CA certificate into the system trust store.",
        ),
        (
            "s2_api_key",
            "",
            "Semantic Scholar API key (optional). Increases rate limits from 1 req/s to 10 req/s. "
            "Obtain a key at https://www.semanticscholar.org/product/api",
        ),
        (
            "llm_provider",
            "",
            "LLM provider for paper chat and structured extraction. "
            "Supported values: 'anthropic', 'openai'. Leave empty to disable LLM features.",
        ),
        (
            "llm_api_key",
            "",
            "API key for the configured LLM provider. "
            "May be left blank when using a local inference server (e.g. Ollama).",
        ),
        (
            "llm_model_id",
            "",
            "Model ID to use for LLM requests. "
            "Select from the available models listed by your provider.",
        ),
        (
            "llm_base_url",
            "",
            "Optional base URL to override the provider's default cloud endpoint. "
            "Use this to point to a local inference server such as Ollama "
            "(e.g. http://localhost:11434/v1). Leave empty to use the provider's cloud API.",
        ),
        (
            "llm_system_prompt_prefix",
            "",
            "Optional text prepended to the system prompt for all LLM extraction requests. "
            "Use this to add domain-specific instructions or context for every extraction.",
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


def _backfill_citations_by_year() -> None:
    """One-time backfill: populate citations_by_year from cached OpenAlex responses."""
    import json as _json
    import logging
    from sqlalchemy import select

    from litexplorer.models.cache import ApiCache
    from litexplorer.models.library import Work

    logger = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        # Find works needing backfill
        works_needing = db.scalars(
            select(Work).where(Work.citations_by_year.is_(None))
        ).all()
        if not works_needing:
            return

        # Build lookup maps
        by_openalex = {w.openalex_id: w for w in works_needing if w.openalex_id}
        by_doi = {w.doi.lower(): w for w in works_needing if w.doi}

        if not by_openalex and not by_doi:
            return

        # 1. Scan work:doi: cache entries (individual work lookups)
        doi_caches = db.execute(
            select(ApiCache.response_json).where(
                ApiCache.source == "openalex",
                ApiCache.query_key.like("work:doi:%"),
            )
        ).scalars().all()

        filled = 0
        for cached_json in doi_caches:
            try:
                raw = _json.loads(cached_json)
                doi = (raw.get("doi") or "").replace("https://doi.org/", "").lower()
                work = by_doi.get(doi) if doi else None
                if work and _apply_counts_by_year(work, raw):
                    filled += 1
            except (ValueError, KeyError):
                continue

        # 2. Scan backward/forward citation cache entries (lists of works)
        citation_caches = db.execute(
            select(ApiCache.response_json).where(
                ApiCache.source == "openalex",
                ApiCache.query_key.like("backward_citations:%")
                | ApiCache.query_key.like("forward_citations:%"),
            )
        ).scalars().all()

        for cached_json in citation_caches:
            try:
                raw_list = _json.loads(cached_json)
                if not isinstance(raw_list, list):
                    continue
                for raw in raw_list:
                    if not isinstance(raw, dict):
                        continue
                    oa_id = raw.get("id")
                    work = by_openalex.get(oa_id) if oa_id else None
                    if work is None:
                        doi = (raw.get("doi") or "").replace("https://doi.org/", "").lower()
                        work = by_doi.get(doi) if doi else None
                    if work and work.citations_by_year is None and _apply_counts_by_year(work, raw):
                        filled += 1
            except (ValueError, KeyError):
                continue

        if filled:
            logger.info("Backfilled citations_by_year for %d works from cache", filled)
            db.commit()
    finally:
        db.close()


def _apply_counts_by_year(work, raw: dict) -> bool:
    """Extract counts_by_year from a raw OpenAlex dict and apply to a Work."""
    counts = raw.get("counts_by_year")
    if not counts or not isinstance(counts, list):
        return False
    cby = [
        {"year": e["year"], "cited_by_count": e["cited_by_count"]}
        for e in counts
        if isinstance(e, dict) and "year" in e and "cited_by_count" in e
    ]
    if cby:
        work.citations_by_year = cby
        return True
    return False


app = FastAPI(title="LitExplorer", version="0.1.0", lifespan=lifespan)


import ssl as _ssl  # noqa: E402

import httpx as _httpx  # noqa: E402
from fastapi import Request as _Request  # noqa: E402
from fastapi.responses import JSONResponse as _JSONResponse  # noqa: E402


@app.exception_handler(_httpx.ConnectError)
async def _httpx_connect_error_handler(request: _Request, exc: _httpx.ConnectError):
    """Return a 503 with a clear message for SSL or connection failures."""
    cause = exc.__cause__ or exc.__context__
    msg = str(exc)
    is_ssl = isinstance(cause, (_ssl.SSLError,)) or any(
        kw in msg.upper() for kw in ("SSL", "CERTIFICATE_VERIFY_FAILED", "CERTIFICATE")
    )
    if is_ssl:
        return _JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "SSL_CERTIFICATE_ERROR: SSL certificate verification failed when calling "
                    "an external API. This is often caused by a corporate proxy that intercepts "
                    f"HTTPS traffic. Details: {msg}"
                )
            },
        )
    return _JSONResponse(
        status_code=503,
        content={"detail": f"Connection error when calling external API: {msg}"},
    )


# Import and include routers after app creation to avoid circular imports.
from litexplorer.api.works import authors_router, router as works_router  # noqa: E402
from litexplorer.api.venues import router as venues_router  # noqa: E402
from litexplorer.api.fields import router as fields_router  # noqa: E402
from litexplorer.api.projects import router as projects_router  # noqa: E402
from litexplorer.api.enrichment import router as enrichment_router  # noqa: E402
from litexplorer.api.timeline import router as timeline_router  # noqa: E402
from litexplorer.api.settings import router as settings_router  # noqa: E402
from litexplorer.api.filesystem import router as filesystem_router  # noqa: E402
from litexplorer.api.notes import project_notes_router  # noqa: E402
from litexplorer.api.llm import router as llm_router  # noqa: E402
from litexplorer.api.extraction import router as extraction_router  # noqa: E402

app.include_router(works_router)
app.include_router(authors_router)
app.include_router(venues_router)
app.include_router(fields_router)
app.include_router(projects_router)
app.include_router(enrichment_router)
app.include_router(timeline_router)
app.include_router(settings_router)
app.include_router(filesystem_router)
app.include_router(project_notes_router)
app.include_router(llm_router)
app.include_router(extraction_router)

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
