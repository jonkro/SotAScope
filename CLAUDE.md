# LitExplorer — Project Specification

## What this project is

A local-first research literature dashboard for discovering and mapping the state of the art around a new research project. All data stays local. There is no cloud dependency except for API calls to literature databases (which are cached where appropriate).

---

## Architecture overview

Two distinct layers:

### 1. Library layer (shared across projects)
- A local database with BibTeX import support, conceptually similar to JabRef
- Each work is uniquely identified by its **DOI** where available, with **arXiv ID** as fallback key. Works may also carry an **OpenAlex ID** for citation graph integration.
- A work may have multiple "locations" (typed as `venue` or `preprint`). The venue version is treated as primary. arXiv links are stored as preprint locations.
- **PDF management**: PDFs are stored in a configurable local folder (`pdf_storage_path` setting, default `{data_dir}/pdfs/`). Each work can have multiple PDFs via the `WorkPDF` table; the first uploaded is auto-set as primary. Files are organized as `{pdf_root}/{work_id}/{filename}`. Deleted PDFs are moved to `{pdf_root}/_orphaned/` rather than permanently removed. PDFs are served inline (not as downloads) via `FileResponse` with `Content-Disposition: inline`.
- **Work notes**: Per-work notes stored in the `WorkNote` table. Notes can be scoped to a project (`project_id` set) or general (`project_id` null). Each note has `content`, `note_type`, `provenance` ("user", "ai", "ai_reviewed", or "ai_proposal"), `model_id`, and `is_outdated` flag. Editing an AI-generated note changes provenance to "ai_reviewed". The `"ai_proposal"` provenance is used for LLM suggestions on cells that already have user/ai_reviewed values — stored as a parallel `WorkNote` rather than overwriting. Stale `"ai_proposal"` notes are deleted before re-creating on each extraction run.
- **Secondary DOIs (WorkDOI)**: A work may have multiple valid DOIs. The primary DOI lives on `Work.doi`; additional DOIs are stored in the `WorkDOI` table (`work_dois`, CASCADE delete). `doi_aliases: list[str]` is included in `WorkOut` via a Pydantic `field_validator(mode='before')`.
- The library stores a **venue tier list**: a user-maintained mapping of venues to tiers (1 = top venue, 2 = regular, 3 = ignore). Tiers are global per venue (not per-field). Venues can be associated with one or more research fields via a many-to-many relationship.
- **Venue aliases** handle year-to-year name variation. Aliases are manually reorderable; the first alias (by `sort_order`) is the **preferred alias** used for display throughout the UI.
- **Venue normalization** runs at startup: strips "Proceedings of the..." prefixes, calendar years, and ordinal edition numbers. Detects and merges duplicate venues after normalization, preserving old names as aliases.
- The library is the single source of truth for all paper metadata.
- **Library sanitization** tools: duplicate detection (by DOI, bibtex_key, or title+year), work merge (repoints citations, topic list memberships, authors, locations, PDFs, notes), and bulk deletion.
- **Extraction schemas**: User-defined structured extraction tables for LLM-assisted literature review. Each `ExtractionSchema` (title, optional description, optional `project_id`) contains ordered `ExtractionColumn` records (name, LLM prompt, optional description, optional `allowed_values` list, `sort_order`). Results are stored as `WorkNote` rows (two per column: answer + reasoning, both `provenance="ai"`). A `null project_id` means the schema is global (not project-specific). When `re_evaluate_edited=True`, cells with `"user"` or `"ai_reviewed"` provenance are re-run and the new result stored as `"ai_proposal"` instead of overwriting.

### 2. Project layer (per project)
- A project contains one or more **topic lists**. Each topic list is a named, color-coded set of "selected" papers (seeds).
- The project stores which papers belong to which topic list, and **ignored works** (excluded from timeline).
- Multiple projects can coexist and share the same library.
- **Extraction table tabs**: Schemas can be promoted to their own top-level tabs in the project view. Promotion state is stored in `localStorage` per project (`litexplorer:project:{id}:promotedSchemas`), not in the database — it is a per-browser preference. Promoted tabs are read-only with respect to paper selection.

---

## External data sources

| Source | Role |
|---|---|
| **OpenAlex** | Primary source: citation graph, forward citations, paper metadata, venue info, per-year citation counts |
| **Crossref** | DOI resolution (including fuzzy search), authoritative venue metadata (ISSN, publisher), search-by-title candidates |
| **Semantic Scholar** | Supplemental: on-demand citation enrichment, search-by-title fallback when Crossref returns no results |
| **GROBID** | Local PDF reference extraction (Docker). Fallback when OA/S2 have no reference list. |

### API authentication
All clients support a "polite pool" email for better rate limits. This email is configurable via:
1. A **database setting** (`api_contact_email`) editable from the Settings page (preferred)
2. Environment variable fallback (`LITEXPLORER_OPENALEX_API_KEY`, `LITEXPLORER_CROSSREF_MAILTO`)

Note: OpenAlex uses `mailto` query parameter for polite pool access, not Bearer token auth.

### SSL verification
All external HTTP clients respect the `ssl_verify` database setting (default `"true"`). Setting it to `"false"` disables SSL certificate verification — useful for corporate proxies with custom CAs. A global `httpx.ConnectError` exception handler returns 503 with an `SSL_CERTIFICATE_ERROR:` prefix for frontend detection.

### Caching policy
All API responses are cached in an `api_cache` table with source and query key.
- **Backward citations**: cached permanently once fetched
- **Forward citations**: cached with a timestamp; manual refresh available. Auto-refresh is not performed.
- **Paper metadata** and **DOI resolution results**: cached permanently, with manual refresh options.

### Auto-enrichment
When a work is added to a topic list (becoming a seed), the system automatically fetches backward citations, forward citations, and Crossref venue metadata as a BackgroundTask. Forward citation fetch also refreshes the seed work's own metadata (`citation_count`, `citations_by_year`) via `_refresh_work_metadata()`.

### DOI / arXiv import
The DOI import endpoint (`/api/enrich/doi`) also accepts arXiv IDs. Detection heuristic: input starting with `10.` is a DOI; anything else is treated as an arXiv ID. Old-style arXiv IDs contain slashes (e.g., `hep-th/0601001`) so "/" is NOT a reliable DOI indicator — the `10.` prefix is the only unambiguous signal.

For arXiv ID import, the resolution chain is:
1. OpenAlex lookup by arXiv ID (filter `ids.arxiv`)
2. If OpenAlex has no match: Semantic Scholar lookup (`GET /paper/ARXIV:{id}`)
3. Store with `arxiv_id` as the primary key; DOI and openalex_id populated if the source provides them.

### DOI resolution
For works without a DOI, the system can auto-resolve via Crossref fuzzy search (score >= 80, ratio to 2nd candidate >= 1.5, marks `doi_auto_resolved = true`) or present candidates for manual confirmation. Batch resolution is supported.

---

## Core visualization: Citation Timeline

The main project view is a D3 scatter plot (x = publication year, y = log-scaled citation count). Seeds are squares colored by topic list, backward neighbors are circles, forward neighbors are diamonds. Implemented in `CitationTimeline.tsx`, controlled by `TimelineControls.tsx`, paper details in `WorkDetailPanel.tsx`.

**Citation window**: A slider controls the time window: **All** (all-time `citation_count`) or **Of last Ny** (1–10 years, sums `citations_by_year`). Computed client-side — no re-fetch on slider change. Works without `citations_by_year` (e.g., imported from Crossref or BibTeX only) fall back to all-time count regardless of slider position. Timeline settings persist in `localStorage` per project (`litexplorer:project:{id}:view`).

---

## Design principles

- **Local first**: no accounts, no cloud storage, no telemetry. API calls are the only outbound traffic.
- **Data ownership**: all stored data (SQLite DB, PDFs, API cache) lives in a user-specified local directory
- **Resilience to API changes**: external API integrations are abstracted behind a clean internal interface (`ExternalWork`, `ExternalVenue`, etc.) so the data source can be swapped without touching the rest of the codebase
- **Incremental use**: the tool is useful from the first paper added, not only after a large import

---

## Stack

- **Backend**: Python 3.11+ / FastAPI
- **Database**: SQLite via SQLAlchemy 2.0 ORM (WAL mode for concurrent access, foreign keys enforced)
- **Frontend**: React 18 + TypeScript, Vite build, TanStack React Query for data fetching
- **Visualization**: D3.js for the citation timeline
- **HTTP client**: httpx (for OpenAlex, Crossref, and Semantic Scholar API calls)
- **BibTeX parsing**: bibtexparser 1.4
- **Startup**: the app is launched via `uvicorn`. The FastAPI app serves the built frontend as static files (SPA fallback for client-side routing).
- **Data directory**: all persistent data lives under `~/.litexplorer` by default, set via `LITEXPLORER_DATA_DIR`.
- **Dependency management**: conda environment (`environment.yml` pins Python 3.11), `pyproject.toml` for Python packages. Install with `pip install -e .`.

---

## Project structure

```
litexplorer/
├── app.py                # FastAPI app with lifespan (startup migrations, backfills, SPA mount)
├── config.py             # Pydantic BaseSettings (LITEXPLORER_ env prefix)
├── database.py           # Engine, SessionLocal, init_db()
├── models/
│   ├── library.py        # Work (citations_by_year JSON), WorkLocation, Author, WorkAuthor,
│   │                     #   Venue, VenueAlias, Field (passive_deletes=True), VenueField,
│   │                     #   Citation, WorkPDF (extraction_status: pending/ready/failed),
│   │                     #   WorkNote, WorkDOI (secondary DOIs, CASCADE delete)
│   ├── project.py        # Project, TopicList, TopicListWork, ProjectIgnoredWork
│   ├── extraction.py     # ExtractionSchema (nullable project_id = global scope),
│   │                     #   ExtractionColumn (allowed_values JSON, sort_order)
│   ├── chat.py           # ChatSession (work_id, project_id, context_type, context_id, is_auto),
│   │                     #   ChatMessage (session_id, role, content)
│   ├── cache.py          # ApiCache (permanent / timestamped)
│   └── settings.py       # Setting (key-value store)
├── schemas/              # Pydantic v2 request/response models (all have from_attributes=True)
│   ├── enrichment.py     # CitationResult (raw_count: int — 0 means OA has no reference list),
│   │                     #   SearchImportRequest/Candidate/CandidatesResult, DOIInfoResult
│   ├── timeline.py       # TimelineSeedWork (+ backward_citations_no_oa_data: bool, has_pdfs: bool),
│   │                     #   TimelineNeighborWork (citations_by_year: list[dict] | None)
│   ├── notes.py          # provenance values: "user" | "ai" | "ai_reviewed" | "ai_proposal"
│   ├── extraction.py     # ExtractionBatchRequest (re_evaluate_edited: bool = False),
│   │                     #   ExtractionCellResult (+ proposal: optional ai_proposal note);
│   │                     #   extract endpoints return 202 {job_id, message} (not sync result)
│   └── ...               # Other schema files are straightforward
├── api/
│   ├── works.py          # /api/works — CRUD, BibTeX import, citations, merge, duplicates,
│   │                     #   PDF upload/serve/delete, notes CRUD, DOI alias CRUD
│   ├── enrichment.py     # /api/enrich — all enrichment endpoints (202 BackgroundTask pattern)
│   ├── extraction.py     # /api/extraction — schema/column CRUD;
│   │                     #   POST /schemas/{id}/extract[/{work_id}] → 202 {job_id} (BackgroundTask, work_lock)
│   │                     #   GET /jobs/{job_id} → per-work status + progress (reads extraction_jobs registry)
│   │                     #   GET /schemas/{id}/export?format=csv|latex
│   │                     #   GET /schemas/{id}/preview-prompt, GET /schemas/{id}/summary
│   │                     #   POST /schemas/from-discussion, POST /schemas/{id}/columns/from-proposal
│   ├── chat.py           # /api/chat — session CRUD; PATCH /sessions/{id} (update context_id)
│   ├── llm.py            # /api/llm — model listing, POST /chat (auto-saves turns to session)
│   └── ...               # timeline, notes, settings, filesystem, grobid, projects, venues, fields
├── services/
│   ├── enrichment.py     # EnrichmentService — import, citations, venue normalization, DOI resolution;
│   │                     #   fetch_backward_citations() → tuple[list[Work], int] where int=raw_count
│   │                     #   (0 = OA has no reference list for this paper)
│   ├── pdf.py            # extract_pdf_text(), two-column detection via x0 histogram heuristics
│   ├── extraction.py     # assemble_extraction_prompt(), parse_extraction_response() → tuple[dict, str];
│   │                     #   run_extraction_for_work() — 5-strategy response parsing, creates WorkNote rows;
│   │                     #   re_evaluate_edited=True → writes "ai_proposal" instead of overwriting
│   ├── extraction_jobs.py # In-memory job tracker (lost on restart): _ExtractionJobRegistry singleton;
│   │                     #   auto-prunes jobs older than 1 hour; thread-safe (threading.Lock)
│   ├── extraction_export.py  # export_as_csv(), export_as_latex() — booktabs LaTeX
│   ├── schema_discussion.py  # build_schema_discussion_prompt() — includes two few-shot column-proposal
│   │                         #   examples for format compliance; parse_column_proposals() — 5-strategy parser
│   └── work_lock.py      # _WorkLockRegistry singleton `work_lock`; stale locks auto-released after 600s
└── external/
    ├── base.py           # ExternalWork, ExternalVenue, ExternalAuthor, ExternalLocation
    ├── openalex.py       # OpenAlexClient — parse_work() extracts counts_by_year
    ├── crossref.py       # CrossrefClient — DOI lookup, fuzzy search
    ├── semantic_scholar.py # SemanticScholarClient — throttled to 1 req/s (_MIN_INTERVAL = 1.0)
    ├── llm_client.py     # AnthropicLLMClient, OpenAILLMClient, make_llm_client();
    │                     #   _normalize_base_url() appends /v1 to bare Ollama URLs;
    │                     #   PDF vision (base64 document block) is Anthropic-only
    ├── pdf_fetch.py      # fetch_pdf_for_work() — arXiv first, then Unpaywall
    └── grobid.py         # GrobidClient — PDF reference extraction via GROBID REST API

frontend/src/
├── api.ts                # All fetch functions
├── types.ts              # TypeScript interfaces matching backend schemas
├── lib/
│   └── timelineFilter.ts # computeCitationCount(), filterNeighbors()
├── utils/
│   └── proposalParser.ts # parseProposals() — 5-strategy parser: fenced block → fenced JSON →
│                         #   bare JSON → markdown table → numbered/bulleted bold list
├── hooks/                # React Query hooks per domain (useWorks, useVenues, useProjects,
│                         #   useTimeline, useEnrichment, useFields, useWorkNotes, useWorkPDFs,
│                         #   useSettings, useChatSessions, useExtraction, useLockStatus)
├── pages/
│   ├── ProjectDetailPage.tsx    # Timeline + Topic Lists + Notes + Tables tabs
│   ├── ExtractionSchemasPage.tsx # Schema editor + ExtractionRunView; ?schema= URL param
│   ├── DiscussionPage.tsx       # LLM chat; context_type drives schema-design vs. paper mode;
│   │                            #   proposal parser produces ColumnProposalCards
│   ├── LibraryPage.tsx
│   ├── VenuesPage.tsx
│   └── SettingsPage.tsx
└── components/
    ├── CitationTimeline.tsx    # D3 scatter plot
    ├── WorkDetailPanel.tsx     # Side panel
    ├── TimelineControls.tsx    # Filter bar
    ├── TimelineEnrichBar.tsx   # Enrichment progress; reads GROBID status from query cache
    │                           #   (no new fetch); accepts onSelectWork prop
    ├── ExtractionRunView.tsx   # Standalone; readOnlyPaperSelection prop for promoted tabs
    ├── ImportDialog.tsx        # 3-tab import (DOI / arXiv, BibTeX, Search by title); first tab
    │                           #   auto-detects input type (10. prefix = DOI, else arXiv ID);
    │                           #   optional post-import topic list assignment via projectTopicLists prop
    ├── ColumnProposalCard.tsx  # LLM column proposal states; UserCancelledError exported for silent cancel
    └── ...

tests/
├── conftest.py                # StaticPool + in-memory SQLite fixtures
├── test_extraction.py         # 62 tests — schema/column CRUD, prompt, parsing, ai_proposal, work lock
├── test_extraction_export.py  # 28 tests
├── test_extraction_jobs.py    # 23 tests
├── test_schema_discussion.py  # 43 tests
├── test_chat_sessions.py      # 18 tests
├── test_grobid_client.py      # 23 tests
└── ...                        # Other test files cover library, enrichment, timeline, PDF, notes, etc.
```

---

## Startup lifecycle (app.py lifespan)

1. `init_db()` — create engine and tables
2. `_migrate_schema()` — add new columns/tables (doi_auto_resolved, sort_order, work_pdfs, citations_by_year, drop pdf_path, work_notes, extraction_status on work_pdfs, semantic_scholar_id on works, work_dois, extraction_schemas, extraction_columns, chat_sessions, chat_messages)
3. `_seed_default_fields()` — create "AI/ML" and "Computer Networks" fields
4. `_seed_default_settings()` — create `api_contact_email`, `pdf_storage_path`, `ssl_verify`, `s2_api_key`, `llm_provider`, `llm_api_key`, `llm_model_id`, `llm_base_url`, `llm_system_prompt_prefix`, `grobid_url` settings
5. `_normalize_existing_venue_names()` — strip prefixes, merge duplicate venues
6. `_backfill_citations_by_year()` — populate `citations_by_year` from cached OpenAlex responses

---

## Multi-user model

The deployment context is a small team of trusted collaborators on a shared local server:
- No login or authentication
- All users share the same **library layer** (papers, PDFs, venue tier list)
- Each **project** has an owner (stored in the DB) but is visible and editable by all users — trust is assumed
- Concurrent writes are handled by SQLite's WAL mode, which is sufficient for a small team
- Timeline settings are per-browser-client via localStorage

Authentication and per-user access control are explicitly deferred to a future phase.

---

## Implementation notes and known pitfalls

- Venue normalization in CS is messy. OpenAlex venue names are inconsistent across years for the same conference. The VenueAlias table handles this, with automatic normalization at startup and manual curation in the Venues UI.
- The same work may appear under different DOIs (rare but real) or without a DOI (arXiv-only papers). The dedup logic checks DOI first, then openalex_id, then arxiv_id, then semantic_scholar_id (4th fallback).
- Forward citation queries can return hundreds of results for well-cited papers. The candidate filter is applied before rendering, not after.
- BibTeX entry keys follow AuthorYearKeyword convention but the internal unique key is always DOI or arXiv ID.
- When transferring a UNIQUE field value between rows (e.g., during work merge), null out the field on the source row and `db.flush()` before setting it on the target — SQLAlchemy batches UPDATEs within a single flush with no guaranteed row ordering.
- TestClient + in-memory SQLite requires `StaticPool` + `check_same_thread=False`. Override `get_db` from `litexplorer.api.deps` (not `litexplorer.database`).
- `Field.venues` relationship uses `passive_deletes=True` because the DB has `ON DELETE CASCADE` on `VenueField.field_id`. Without this, SQLAlchemy tries to set `field_id = NULL` on eagerly-loaded relationships before delete, which fails because `field_id` is NOT NULL.
- The `citations_by_year` sliding window only works for works that have the data populated from OpenAlex. Works without it (e.g., imported from Crossref or BibTeX only) fall back to the all-time `citation_count` regardless of slider position. The startup backfill populates from cached responses; re-enriching a work will also populate it.
- `_update_work()` always overwrites `citation_count` and `citations_by_year` (they change over time), unlike other fields which use update-without-overwrite.
- **LLM calls must be async**: use FastAPI `BackgroundTasks` or streaming responses. A single extraction pass over many papers can take minutes. Do NOT call LLM APIs synchronously in the request handler. The two extraction execution endpoints (`POST /schemas/{id}/extract/{work_id}` and `POST /schemas/{id}/extract`) return 202 immediately and run `_extraction_bg` as a BackgroundTask. Progress is tracked in `extraction_jobs` and polled via `GET /api/extraction/jobs/{job_id}`. Each work is locked via `work_lock` before the task starts and released per-work as it completes.
- **PDF vision is Anthropic-only**: sending the PDF binary directly to the model is only supported when `llm_provider = "anthropic"`. All other providers (including local OpenAI-compatible servers) must use extracted `.txt` text.
- **Table isolation in tests**: `Base.metadata.create_all()` in `conftest.py` runs before `from litexplorer.app import app`. A test file that uses a model via the API but does NOT import that model at the top of the file will get "no such table" when run in isolation. Fix: add a bare import at the top (e.g. `from litexplorer.models.chat import ChatSession`) so it registers in `Base.metadata` before `create_all()`.
- **Work lock registry** (`litexplorer/services/work_lock.py`): module-level singleton `work_lock` tracks in-flight background operations per work ID. `acquire(work_id, task)` returns `False` if already locked; `release(work_id)` is always called in a `finally` block. Locks older than 10 minutes are treated as stale and auto-released on any access (guards against background task crashes). Uses `threading.Lock` — safe for single-worker uvicorn deployments.
- **Enrichment endpoints use BackgroundTasks and return 202**: the five slow enrichment endpoints (`/citations/backward`, `/citations/forward`, `/crossref`, `/semantic-scholar`, `/grobid`) return HTTP 202 immediately and run the actual network calls in a FastAPI `BackgroundTask`. Fast pre-validation (work existence, DOI presence, PDF existence, GROBID URL) is still done synchronously and returns 404/400 as before. HTTP 409 is returned if the work is already locked.
- **BackgroundTask DB sessions**: background functions must NOT use the request-scoped `db` session (FastAPI closes it after the response is sent). Each background function creates its own session via `SessionLocal()` from `litexplorer.database` and closes it in a `finally` block alongside `work_lock.release()`. In tests, `SessionLocal()` connects to the real SQLite file (not the in-memory test DB), so background functions fail silently at the DB lookup stage — this is acceptable; API tests only assert the 202 status code. Service-layer behavior is covered by `test_enrichment_service.py`.
- **409 on destructive operations**: `DELETE /api/works/{id}` and `POST /api/works/{target_id}/merge/{source_id}` both check `work_lock.is_locked()` before proceeding and return 409 if a background task is in progress for any involved work ID.
- **Auto-enrichment on topic list addition**: `add_work_to_topic_list` in `api/projects.py` schedules `_auto_enrich_bg` (backward citations + forward citations + Crossref) as a BackgroundTask when a work is added to a topic list. If the work is already locked, enrichment is silently skipped (the addition still succeeds). `GET /api/works/lock-status` returns `{"locks": {"<work_id>": "<task>", …}}` for frontend polling.

---

## Known issues / future work

- **Semantic Scholar rate limits**: S2 enforces 1 req/s regardless of whether an API key is used. Without a key the quota is shared globally across all users from the same IP, so on shared/university networks 429 errors are common. An API key (`s2_api_key` setting) authenticates requests (your own dedicated quota at 1 req/s) — apply at https://www.semanticscholar.org/product/api. The client throttles to 1 req/s in both cases (`_MIN_INTERVAL = 1.0`). The search-by-title endpoint returns HTTP 429 with a user-readable message when S2 rate-limits us.
- **S2 missing reference lists**: S2 can return forward citations (papers citing a given work) even when it has no reference list for that work. This happens when S2 doesn't have full-text access to the paper. The "Fetch references (S2)" button shows "S2 has no reference list for this paper" in this case instead of a misleading "0 references" count.
- **S2 rate-limit backoff**: When a 429 occurs, S2 imposes a backoff of up to 1+ hour from the same IP. The only reliable mitigation is an API key.
- **Topic list legend**: The legend is rendered as an HTML flex-wrap div above the SVG, sourced from the full topic list array (not from visible data). It remains visible when all lists are toggled off. Inactive items dim to 40% opacity. This replaced an earlier D3-rendered legend that disappeared when all traces were deactivated.
- **Unpaywall requires contact email**: OA PDF fetch via Unpaywall is only attempted when `api_contact_email` is configured in Settings. Without it, only arXiv is tried (works that have `arxiv_id`). Works that have only a DOI but no `arxiv_id` and no configured email will return 404 from the fetch endpoint.
- **LLM local server auth**: `list_models()` correctly omits the `Authorization` header when `api_key` is empty for local servers (was sending `Bearer local`, causing 401 on servers that validate auth). The `chat()` endpoint still uses the openai SDK which sends `Bearer local`; most local servers (Ollama, LM Studio) accept this, but servers with strict auth validation may reject it.
- **Column-proposal re-prompting (not implemented)**: when all five `parseProposals()` strategies fail and the LLM response contains no parseable proposal, the UI silently shows the message as plain text. A possible future enhancement is to detect this case client-side and automatically re-prompt the LLM asking it to reformat its answer as a fenced `column-proposal` block.
- **GROBID reference extraction**: Requires Docker (`docker run -d --name grobid -p 8070:8070 grobid/grobid:0.8.2-crf`). CRF-only image is lightweight and fast; for better accuracy use the `-full` image (needs GPU). Settings page has a "Start" button (calls `POST /api/grobid/start`) that appears after a failed "Test connection" check; on success waits 5 s and auto-re-tests. Reference resolution follows a 4-step chain per extracted reference:
  1. DOI present in GROBID output → existing DOI import path
  2. arXiv ID present → arXiv import path (OpenAlex → S2 fallback, as above)
  3. Neither → S2 title search with verification: match first-author surname AND year (±1). If S2 provides a DOI, validate it against Crossref (title/author comparison) — discard the DOI if it resolves to a different work, but keep the S2 CorpusId.
  4. No match → store as unresolved reference with GROBID metadata (title, authors, year, raw_url), shown in the UI with an "unresolved" badge for manual resolution later.
- **S2 CorpusId as last-resort identifier**: papers imported via `semantic_scholar_id` only (no DOI, arXiv ID, or OpenAlex ID) cannot be enriched through OpenAlex. Citation graph data for these relies entirely on S2's API, subject to rate limits (see above).

---

## Running tests

```bash
python -m pytest tests/ -v          # all tests
cd frontend && npm run build        # TypeScript type check + production build (requires Node.js)
```

All tests should pass. There are no known pre-existing failures.
