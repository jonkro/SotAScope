"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from sotascope.database import SessionLocal, init_db

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
    _backfill_venue_from_oa_cache()
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

        # Create chat_sessions table if it doesn't exist
        if "chat_sessions" not in existing_tables:
            db.execute(text(
                "CREATE TABLE chat_sessions ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  work_id INTEGER REFERENCES works(id) ON DELETE CASCADE,"
                "  project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,"
                "  context_type VARCHAR(32) NOT NULL DEFAULT 'papers',"
                "  context_id INTEGER,"
                "  title VARCHAR(256),"
                "  is_auto BOOLEAN DEFAULT 1,"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                ")"
            ))
            db.commit()
        else:
            # Make work_id nullable on existing databases (SQLite requires table rebuild).
            col_rows = db.execute(text("PRAGMA table_info(chat_sessions)")).fetchall()
            work_id_col = next((r for r in col_rows if r[1] == "work_id"), None)
            if work_id_col is not None and work_id_col[3] == 1:  # notnull=1
                db.execute(text("PRAGMA foreign_keys = OFF"))
                db.execute(text(
                    "CREATE TABLE chat_sessions_tmp ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  work_id INTEGER REFERENCES works(id) ON DELETE CASCADE,"
                    "  project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,"
                    "  context_type VARCHAR(32) NOT NULL DEFAULT 'papers',"
                    "  context_id INTEGER,"
                    "  title VARCHAR(256),"
                    "  is_auto BOOLEAN DEFAULT 1,"
                    "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                    "  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                    ")"
                ))
                db.execute(text(
                    "INSERT INTO chat_sessions_tmp "
                    "SELECT id, work_id, project_id, 'papers', NULL, title, is_auto, created_at, updated_at "
                    "FROM chat_sessions"
                ))
                db.execute(text("DROP TABLE chat_sessions"))
                db.execute(text("ALTER TABLE chat_sessions_tmp RENAME TO chat_sessions"))
                db.execute(text("PRAGMA foreign_keys = ON"))
                db.commit()
            else:
                # Add context_type / context_id columns if they were introduced later.
                col_names = {r[1] for r in col_rows}
                if "context_type" not in col_names:
                    db.execute(text(
                        "ALTER TABLE chat_sessions ADD COLUMN "
                        "context_type VARCHAR(32) NOT NULL DEFAULT 'papers'"
                    ))
                    db.commit()
                if "context_id" not in col_names:
                    db.execute(text(
                        "ALTER TABLE chat_sessions ADD COLUMN context_id INTEGER"
                    ))
                    db.commit()

        # Create project_venue_tiers table if it doesn't exist
        if "project_venue_tiers" not in existing_tables:
            db.execute(text(
                "CREATE TABLE project_venue_tiers ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,"
                "  venue_id INTEGER NOT NULL REFERENCES venues(id) ON DELETE CASCADE,"
                "  tier INTEGER NOT NULL,"
                "  UNIQUE(project_id, venue_id)"
                ")"
            ))
            db.commit()

        # Add is_promoted column to extraction_schemas if it doesn't exist
        if "extraction_schemas" in existing_tables:
            es_cols = {c["name"] for c in inspector.get_columns("extraction_schemas")}
            if "is_promoted" not in es_cols:
                db.execute(text(
                    "ALTER TABLE extraction_schemas ADD COLUMN is_promoted BOOLEAN NOT NULL DEFAULT 0"
                ))
                db.commit()

        # Add selected_work_ids column to extraction_schemas if it doesn't exist
        if "extraction_schemas" in existing_tables:
            es_cols = {c["name"] for c in inspector.get_columns("extraction_schemas")}
            if "selected_work_ids" not in es_cols:
                db.execute(text(
                    "ALTER TABLE extraction_schemas ADD COLUMN selected_work_ids JSON"
                ))
                db.commit()

        # Create chat_messages table if it doesn't exist
        if "chat_messages" not in existing_tables:
            db.execute(text(
                "CREATE TABLE chat_messages ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,"
                "  role VARCHAR(16) NOT NULL,"
                "  content TEXT NOT NULL,"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                ")"
            ))
            db.commit()

    finally:
        db.close()


def _seed_default_fields() -> None:
    """Create default research fields if they don't exist yet."""
    from sotascope.models.library import Field
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
    from sotascope.models.settings import Setting
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
        (
            "grobid_url",
            "",
            "Base URL of a locally-running GROBID instance for PDF reference extraction "
            "(e.g. http://localhost:8070). Leave empty to disable GROBID integration. "
            "Run GROBID via Docker: docker run --rm -p 8070:8070 lfoppiano/grobid:0.8.1",
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
    from sotascope.models.library import Venue, VenueAlias, Work
    from sotascope.services.enrichment import normalize_venue_name

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

    from sotascope.models.cache import ApiCache
    from sotascope.models.library import Work

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


def _backfill_venue_from_oa_cache() -> None:
    """One-time backfill: populate venue_id for works that have an OpenAlex ID but
    no venue.

    Phase 1 (synchronous) — cache scan: reads existing cached OA responses; no
    network calls.  Completes before the app starts serving requests.

    Phase 2 (background thread) — fresh batch fetch: for any works still missing
    venue after Phase 1, calls the OA API in batches of 50 and updates the cache.
    Runs in the background so it never delays startup.  This handles the common
    case where the original cached response was stored before OA had venue data
    for a paper (e.g. a conference preprint imported shortly after submission).

    The done flag is written after Phase 1 so repeat startups are instant even
    while Phase 2 is still running.
    """
    import json as _json
    import logging as _logging
    from sqlalchemy import select as _select

    from sqlalchemy import or_ as _or

    from sotascope.external.openalex import parse_work as _parse_work
    from sotascope.models.cache import ApiCache
    from sotascope.models.library import Venue, Work
    from sotascope.models.settings import Setting
    from sotascope.services.enrichment import EnrichmentService

    _logger = _logging.getLogger(__name__)
    _DONE_KEY = "backfill_venue_from_oa_cache_v2_done"

    db = SessionLocal()
    try:
        done = db.execute(
            _select(Setting).where(Setting.key == _DONE_KEY)
        ).scalar_one_or_none()
        if done and done.value == "true":
            return

        # Include works with no venue AND works whose current venue is a provisional
        # repository (arXiv, bioRxiv, etc.) — they may now have a real venue in cache.
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
            db.add(Setting(key=_DONE_KEY, value="true"))
            db.commit()
            return

        by_openalex: dict = {}
        by_doi: dict = {}
        for w in works_needing:
            if w.openalex_id:
                by_openalex[w.openalex_id] = w
            if w.doi:
                by_doi[w.doi.lower()] = w

        if not by_openalex and not by_doi:
            db.add(Setting(key=_DONE_KEY, value="true"))
            db.commit()
            return

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

        # --- Phase 1: read existing cache, no network calls ---
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
            _logger.info("Venue backfill phase 1: set venue_id for %d works", filled)
        db.commit()

        # Mark done now so repeat startups skip immediately regardless of Phase 2.
        db.add(Setting(key=_DONE_KEY, value="true"))
        db.commit()

        # --- Phase 2: background thread, batched fresh OA fetch ---
        # Collect OpenAlex IDs for works still missing a real venue after Phase 1.
        # Includes works with null venue AND works whose venue is still a provisional
        # repository (cache had no non-repository source for them).
        def _still_needs_upgrade(w: Work) -> bool:
            if w.venue_id is None:
                return True
            v = db.get(Venue, w.venue_id)
            return v is not None and v.venue_type == 'repository'

        still_missing_ids = [
            w.openalex_id for w in works_needing
            if w.openalex_id and _still_needs_upgrade(w)
        ]
        if still_missing_ids:
            _logger.info(
                "Venue backfill phase 2: %d works still missing venue — "
                "fetching in background",
                len(still_missing_ids),
            )
            import threading as _threading
            t = _threading.Thread(
                target=_venue_backfill_phase2,
                args=(still_missing_ids,),
                daemon=True,
                name="venue-backfill-phase2",
            )
            t.start()
    finally:
        db.close()


def _venue_backfill_phase2(openalex_ids: list) -> None:
    """Background thread: batch-fetch fresh OA data for works missing venue.

    Uses the batch endpoint (50 IDs per request) so N works require only N/50
    API calls.  Each response updates the work:openalex: cache entry so the
    fresh venue data is available to future re-imports too.
    """
    import json as _json
    import logging as _logging
    from sqlalchemy import select as _select

    from sqlalchemy import or_ as _or

    from sotascope.external.openalex import OpenAlexClient as _OAClient
    from sotascope.external.openalex import parse_work as _parse_work
    from sotascope.models.cache import ApiCache
    from sotascope.models.library import Venue, Work
    from sotascope.services.enrichment import EnrichmentService

    _logger = _logging.getLogger(__name__)
    _BATCH = 50

    db = SessionLocal()
    try:
        from sotascope.api.settings import get_setting_value as _gsv
        email = _gsv(db, "api_contact_email")
        ssl_val = _gsv(db, "ssl_verify")
        ssl_verify = (ssl_val or "true").lower() != "false"
        oa_client = _OAClient(api_key=email, verify=ssl_verify)
        service = EnrichmentService(db=db, client=oa_client)

        filled = 0
        for i in range(0, len(openalex_ids), _BATCH):
            chunk = openalex_ids[i: i + _BATCH]
            try:
                raw_list = oa_client.get_works_by_ids_raw(chunk)
            except Exception as exc:
                _logger.warning("Venue backfill phase 2 batch %d failed: %s", i, exc)
                continue

            for raw in raw_list:
                if not isinstance(raw, dict):
                    continue
                oa_id = (raw.get("id") or "").replace("https://openalex.org/", "") or None
                if not oa_id:
                    continue
                work = db.execute(
                    _select(Work)
                    .outerjoin(Venue, Work.venue_id == Venue.id)
                    .where(
                        Work.openalex_id == oa_id,
                        _or(
                            Work.venue_id.is_(None),
                            Venue.venue_type == 'repository',
                        ),
                    )
                ).scalar_one_or_none()
                if work is None:
                    continue
                ext_work = _parse_work(raw)
                if not ext_work.venue or ext_work.venue.venue_type == 'repository':
                    continue
                venue = service._resolve_venue(ext_work)
                if not venue:
                    continue
                work.venue_id = venue.id
                # Refresh the cache entry so future operations see the venue
                cache_key = f"work:openalex:{oa_id}"
                existing = db.execute(
                    _select(ApiCache).where(
                        ApiCache.source == "openalex",
                        ApiCache.query_key == cache_key,
                    )
                ).scalar_one_or_none()
                if existing:
                    existing.response_json = _json.dumps(raw)
                else:
                    db.add(ApiCache(
                        source="openalex",
                        query_key=cache_key,
                        response_json=_json.dumps(raw),
                        cache_type="permanent",
                    ))
                filled += 1

            db.commit()

        oa_client.close()
        if filled:
            _logger.info("Venue backfill phase 2: set venue_id for %d works", filled)
    except Exception as exc:
        _logger.warning("Venue backfill phase 2 failed: %s", exc)
    finally:
        db.close()


app = FastAPI(title="SotAScope", version="0.1.0", lifespan=lifespan)


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
from sotascope.api.works import authors_router, router as works_router  # noqa: E402
from sotascope.api.venues import router as venues_router  # noqa: E402
from sotascope.api.fields import router as fields_router  # noqa: E402
from sotascope.api.projects import router as projects_router  # noqa: E402
from sotascope.api.enrichment import router as enrichment_router  # noqa: E402
from sotascope.api.timeline import router as timeline_router  # noqa: E402
from sotascope.api.settings import router as settings_router  # noqa: E402
from sotascope.api.filesystem import router as filesystem_router  # noqa: E402
from sotascope.api.notes import project_notes_router  # noqa: E402
from sotascope.api.llm import router as llm_router  # noqa: E402
from sotascope.api.extraction import router as extraction_router  # noqa: E402
from sotascope.api.chat import router as chat_router  # noqa: E402
from sotascope.api.grobid import router as grobid_router  # noqa: E402

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
app.include_router(chat_router)
app.include_router(grobid_router)

# Serve built frontend (only when frontend/dist exists)
if _frontend_dist.is_dir():
    @app.exception_handler(StarletteHTTPException)
    async def _spa_not_found(request, exc):
        """Serve index.html for 404s on non-API paths (SPA client-side routing)."""
        if exc.status_code == 404 and not request.url.path.startswith("/api/"):
            return FileResponse(_frontend_dist / "index.html")
        return await http_exception_handler(request, exc)

    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
