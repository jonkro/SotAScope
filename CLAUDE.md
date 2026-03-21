# SotAScope — Project Specification

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
- **Auto-created "Main" topic list**: when a new project is created via `POST /api/projects`, a topic list named "Main" (color `#3b82f6`) is automatically created in the same request. This is handled in `api/projects.py` `create_project()` via a `db.flush()` + `TopicList` insert before the final commit.
- The project stores which papers belong to which topic list, and **ignored works** (excluded from timeline).
- Multiple projects can coexist and share the same library.
- **Project editing**: name, description, and owner are editable from the projects overview page (`ProjectsPage`) via an "Edit" button on each project card. Opens `ProjectFormDialog` in edit mode; calls `PATCH /api/projects/{id}`.
- **Extraction table tabs**: Schemas can be promoted to their own top-level tabs in the project view. Promotion state is stored in `localStorage` per project (`sotascope:project:{id}:promotedSchemas`), not in the database — it is a per-browser preference. Pin/unpin toggles are on each schema card in `ExtractionSchemasPage`; `ProjectDetailPage` reads the same key but only reads it (no toggle UI there). Promoted tabs are read-only with respect to paper selection.

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
2. Environment variable fallback (`SOTASCOPE_OPENALEX_API_KEY`, `SOTASCOPE_CROSSREF_MAILTO`)

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

### Search-by-title import
`POST /api/enrich/search-import/candidates` accepts `{title, authors?, year?}`. The `year` field is passed as a **proper filter parameter** to each API — it is NOT concatenated into the free-text query string:
- **Crossref**: `filter=from-pub-date:{year},until-pub-date:{year}` added to the `/works` request; `query.bibliographic` contains only title + authors.
- **Semantic Scholar** (fallback): `year={year}` query parameter added to `/paper/search`; the `query` param contains only title + authors.

The `ImportDialog` search tab has three separate fields (title, authors, year); the frontend sends them as separate JSON fields in the request body.

### DOI resolution
For works without a DOI, the system can auto-resolve via Crossref fuzzy search (score >= 80, ratio to 2nd candidate >= 1.5, marks `doi_auto_resolved = true`) or present candidates for manual confirmation. Batch resolution is supported.

---

## Core visualization: Citation Timeline

The main project view is a D3 scatter plot (x = publication year, y = log-scaled citation count). Seeds are squares colored by topic list, backward neighbors are circles, forward neighbors are diamonds. Implemented in `CitationTimeline.tsx`, controlled by `TimelineControls.tsx`, paper details in `WorkDetailPanel.tsx`.

**Citation window**: A slider controls the time window: **All** (all-time `citation_count`) or **Of last Ny** (1–10 years, sums `citations_by_year`). Computed client-side — no re-fetch on slider change. Works without `citations_by_year` (e.g., imported from Crossref or BibTeX only) fall back to all-time count regardless of slider position. Timeline settings persist in `localStorage` per project (`sotascope:project:{id}:view`). URL search params (`?tab=`, `?work=`, `?schema=`) encode view state for deep links and the **Share** button (copies current URL to clipboard). On initial mount, URL params override localStorage; after that, normal interaction and localStorage take over. `?tab=extract&schema={id}` activates a promoted schema tab. URL is kept in sync via `useSearchParams` with `replace: true`.

**Candidate filtering pipeline** (applied client-side in `ProjectDetailPage`, all steps are computed in `useMemo`):
1. `filterNeighbors()` — removes neighbors by direction toggles (show/hide backward/forward), ignored-venue set, and publication year presence.
2. Top-venues filter — when the "top venues" candidate filter is active, further restricts to neighbors whose `venue_id` is in the tier-1 set.
3. Active topic-list filter — when some topic lists are toggled off, removes neighbors whose `connected_seed_ids` only overlap with inactive lists.
4. `applyVisibilityThreshold(neighbors, 3000)` — to keep the D3 scatter plot performant, at most **3 000** neighbors are rendered. When the filtered set exceeds this limit, neighbors are sorted descending by **relevance score** and only the top 3 000 are passed to `CitationTimeline`. The relevance score is pre-computed by the backend (populated in `TimelineNeighborWork.relevance_score`) using `compute_relevance_score()` from `sotascope/services/scoring.py`.

**Relevance score formula** (shared via `sotascope/services/scoring.py`, used by both the timeline and the side-panel citation sort):
```
score = log(1 + citation_count) + max(0, (publication_year − 2000) / 2)
```
Papers before 2000 receive no recency bonus. The result is rounded to 4 decimal places.

**Candidates indicator** in `TimelineControls`:
- Normal (≤ 3 000 after all filters): `Showing K of N candidates` — K = dots in the plot, N = total union of all reference and cited-by lists from the backend (constant; only changes when citation data is updated).
- Threshold active (> 3 000): `Showing top K of N candidates (by relevance)` — same N, K capped at 3 000.

**Side-panel citation lists** (`WorkDetailPanel`): both the "References (backward)" and "Cited by (forward)" lists are paginated (50 per page, sorted by relevance by default; also supports `year_desc`, `year_asc`, `citations_desc`). The API endpoints (`GET /api/works/{id}/citations/{forward|backward}`) return `CitationListResponse { items, total_count }` — `total_count` reflects all Citation rows for that work regardless of page, and is used for the **staleness indicator** shown below the "Cited by" list when `total_count < work.citation_count * 0.8` (i.e. more than 20 % of citing papers have not been fetched yet).

---

## Design principles

- **Local first**: no accounts, no cloud storage, no telemetry. API calls are the only outbound traffic.
- **Data ownership**: all stored data (SQLite DB, PDFs, API cache) lives in a user-specified local directory
- **Resilience to API changes**: external API integrations are abstracted behind a clean internal interface (`ExternalWork`, `ExternalVenue`, etc.) so the data source can be swapped without touching the rest of the codebase
- **Incremental use**: the tool is useful from the first paper added, not only after a large import

---

## UI design system

Rules that apply to all page headers:

1. **One blue filled primary button per page** — the most common "add/create" action, always rightmost (before the share icon if present). All other buttons use outline style (transparent background, `border border-gray-300`).
2. **Red text only** for destructive actions (e.g., Delete on project cards). Never in the top menu bar.
3. **Dropdowns** styled identically to outline buttons with a small `▾` indicator. Built as the local `DropdownMenu` component in `ProjectDetailPage.tsx`; close on outside `mousedown`.
4. **Button sizing**: `py-1.5 px-3 text-sm` (≈ 32 px tall). The share icon button is `h-8 w-8` (square, same height).
5. **`ProjectDetailPage` header**: breadcrumb (`← Projects / {name}`) on the left using `PageHeader leftContent`; three action dropdowns (Project, Analyze, Export) + "Import works" primary + link icon on the right.
6. **AI-action buttons** use indigo outline style: `border border-indigo-300 text-indigo-700 rounded hover:bg-indigo-50`. Apply this to any button that triggers an LLM call: extraction runs ("Extract N papers →"), AI refinement ("Refine with AI"), and AI-oriented navigation dropdowns ("Analyze"). This distinguishes them from the blue primary button (structural/save actions) and plain outline buttons (navigation/filter/export actions). The `DropdownMenu` component in `ProjectDetailPage.tsx` accepts an `accent` prop to switch to this style.

### Onboarding hints

- **`OnboardingHint`** (`components/OnboardingHint.tsx`): tooltip-style overlay anchored to a DOM element via a ref. Renders into `document.body` via `createPortal`. Props: `anchorRef` (`{ current: HTMLElement | null }`), `text`, `storageKey`, `placement` (`top|bottom|left|right`, default `bottom`), optional `onDismiss` callback.
  - Standalone use: checks `localStorage.getItem(storageKey)` on mount; renders nothing if already dismissed. On dismiss, writes `true` to `localStorage` and calls `onDismiss` if provided.
  - A semi-transparent backdrop (`rgba(0,0,0,0.08)`) covers the viewport and dismisses the hint on click.
- **`OnboardingHintSequence`** (`components/OnboardingHint.tsx`): manages a list of `SequenceHint` configs and shows them one at a time. On mount it finds the first hint whose `storageKey` is not set in `localStorage` and shows it. When dismissed (the hint writes `localStorage` and calls `onDismiss`), the sequence advances to the next undismissed hint. Hints whose anchor element is not mounted are auto-skipped after 150 ms (without marking them dismissed). Each `SequenceHint` accepts an optional `onDismiss` callback called after the hint's storageKey is written. Renders nothing once all hints are dismissed.
- **`localStorage` key pattern**: `sotascope:onboarding:{scope}:{hint-id}`, e.g. `sotascope:onboarding:project-view:import-paper`.
- **`ProjectDetailPage` hints** (two separate sequences on first visit):
  - Project-view sequence:
    1. Anchored to "Import works" button → "Start by importing papers via DOI, arXiv ID, or title search."
    2. Anchored to "Topic Lists" tab → "Organize your papers into topic lists. We created 'Main' for you — rename it anytime." — **only shown for freshly-created projects** (`sotascope:project:{id}:isNew` key set by `ProjectsPage` on creation, cleared by this hint's `onDismiss`).
    3. Anchored to "Analyze" dropdown → "Use Analyze to explore citations, discuss papers with AI, or design extraction schemas."
  - Timeline sequence (shown only when `timeline.seeds.length > 0`):
    1. Anchored to the CitationTimeline SVG container → "Squares are your papers. Circles and diamonds are cited and citing papers."
    2. Anchored to the TimelineControls bar → "Toggle backward and forward citations to focus your view."
- **`ExtractionSchemasPage` hints** (list view, sequence):
  1. Anchored to "New Table Schema" button → "Define columns of information to extract from your papers." (storageKey: `sotascope:onboarding:extraction-schemas:new-schema`)
- **`ExtractionRunView` hints** (shown in schema editor extract/review tab, sequence):
  1. Anchored to "Extract N papers →" button → "Run AI extraction on all selected papers at once."
  2. Anchored to "Show prompt" button → "Copy this prompt to use with any external LLM."
  - Only rendered when `readOnlyPaperSelection=false`.
- **`WorkDetailPanel` hints** (side panel, sequence, shown on first time panel is opened):
  1. Anchored to PDFs section → "Attach a PDF or fetch one automatically." (placement: bottom)
  2. Anchored to "Discuss" button → "Discuss this paper with AI." (placement: bottom)
  3. Anchored to "Add to topic list" controls → "Add this paper to a topic list." (placement: top) — auto-skipped if no addable lists.
- **Side panel default-open sections**: `pdfs: true` and `actions: true` in `DEFAULT_FOLD_STATE` (already open by default; no behavior change).
- **BibTeX import**: The BibTeX tab has been **removed from `ImportDialog.tsx`** UI. The backend endpoint (`POST /api/works/import/bibtex`) still exists. Only the DOI/arXiv and Search by Title tabs remain.
- **"Import works" rename**: The primary action button in `ProjectDetailPage` header is now "Import works" (was "Import paper"). The dialog title was already "Import Works".

---

## Stack

- **Backend**: Python 3.11+ / FastAPI
- **Database**: SQLite via SQLAlchemy 2.0 ORM (WAL mode for concurrent access, foreign keys enforced)
- **Frontend**: React 18 + TypeScript, Vite build, TanStack React Query for data fetching
- **Visualization**: D3.js for the citation timeline
- **HTTP client**: httpx (for OpenAlex, Crossref, and Semantic Scholar API calls)
- **BibTeX parsing**: bibtexparser 1.4
- **Startup**: the app is launched via `uvicorn`. The FastAPI app serves the built frontend as static files (SPA fallback for client-side routing).
- **Data directory**: all persistent data lives under `~/.sotascope` by default, set via `SOTASCOPE_DATA_DIR`.
- **Dependency management**: conda environment (`environment.yml` pins Python 3.11), `pyproject.toml` for Python packages. Install with `pip install -e .`.

---

## Project structure

```
sotascope/
├── app.py                # FastAPI app with lifespan (startup migrations, backfills, SPA mount)
├── config.py             # Pydantic BaseSettings (SOTASCOPE_ env prefix)
├── database.py           # Engine, SessionLocal, init_db()
├── models/
│   ├── library.py        # Work (citations_by_year JSON), WorkLocation, Author, WorkAuthor,
│   │                     #   Venue, VenueAlias, Field (passive_deletes=True), VenueField,
│   │                     #   Citation (source: 'openalex'|'semantic_scholar'|'grobid'|'crossref'),
│   │                     #   WorkPDF (extraction_status: pending/ready/failed),
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
│   ├── timeline.py       # TimelineSeedWork (+ backward_citations_no_oa_data, oa_forward_no_data,
│   │                     #   s2_refs_fetched, s2_refs_no_data, s2_citing_fetched, s2_citing_no_data,
│   │                     #   grobid_fetched, has_pdfs booleans),
│   │                     # Three-state per-source enrichment model:
│   │                     #   (1) not fetched — no attempt made
│   │                     #   (2) fetched, has data — source returned references/citations
│   │                     #   (3) fetched, no data — source was queried but returned nothing
│   │                     # OA tracking: api_cache "backward_citations:{oa_id}" (permanent);
│   │                     #   "[]" = fetched empty. Forward: "forward_citations:{oa_id}" (timestamped).
│   │                     # S2 tracking: api_cache source="semantic_scholar",
│   │                     #   key="s2_enrich_refs:{work_id}" / "s2_enrich_citing:{work_id}" (permanent).
│   │                     #   Written by enrich_from_semantic_scholar() after each direction completes.
│   │                     # GROBID tracking: api_cache source="grobid",
│   │                     #   key="grobid_references:{work_id}" exists = extraction was run (set by
│   │                     #   enrich_from_grobid() when raw extraction completes, regardless of resolve).
│   │                     #   TimelineNeighborWork (citations_by_year: list[dict] | None)
│   ├── notes.py          # provenance values: "user" | "ai" | "ai_reviewed" | "ai_proposal" | "external_ai"
│   │                     #   "external_ai" = filled from externally-generated JSON; behaves like "ai"
│   │                     #   for re-extraction (overwritten on next run, NOT protected like user/ai_reviewed)
│   ├── extraction.py     # ExtractionBatchRequest (re_evaluate_edited: bool = False),
│   │                     #   ExtractionCellResult (+ proposal: optional ai_proposal note);
│   │                     #   extract endpoints return 202 {job_id, message} (not sync result);
│   │                     #   PasteExtractionRequest/Result for external JSON import
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
│   │                     #   POST /schemas/{id}/paste/{work_id} → PasteExtractionResult (synchronous);
│   │                     #     accepts {"columns": {...}} or flat {"Col Name": {...}}; provenance="external_ai";
│   │                     #     overwrites ai/external_ai, skips user/ai_reviewed cells
│   ├── chat.py           # /api/chat — session CRUD; PATCH /sessions/{id} (update context_id)
│   ├── llm.py            # /api/llm — model listing, POST /chat (auto-saves turns to session)
│   └── ...               # timeline, notes, settings, filesystem, grobid, projects, venues, fields
│                         #   projects.py: CRUD + topic lists + venue tiers + merge + export + import
│                         #   POST /api/projects/import (multipart .zip upload) → ImportResult (201)
│                         #   POST /api/projects/import/{temp_id}/resolve → ProjectDetail (200)
│                         #   POST /api/projects/import/{id}/resolve-aliases → {message} (200)
│                         #     writes accepted VenueAlias rows to global table (format_version=2 only)
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
│   ├── bulk_enrich_jobs.py # In-memory tracker for bulk S2/GROBID enrichment jobs (BulkEnrichJob);
│   │                     #   same pattern as extraction_jobs; status: running/completed/cancelled/rate_limited;
│   │                     #   cancel_job() sets cancel_requested flag (checked between each seed);
│   │                     #   endpoints: POST /api/enrich/bulk/semantic-scholar|grobid → 202 {job_id},
│   │                     #     GET /api/enrich/jobs/bulk/{job_id}, DELETE /api/enrich/jobs/bulk/{job_id}
│   │                     #   NOTE: paths use /bulk/ prefix (not /works/bulk/) to avoid routing conflict
│   │                     #   with /api/enrich/works/{work_id}/semantic-scholar
│   ├── extraction_export.py  # export_as_csv(), export_as_latex() — booktabs LaTeX
│   ├── schema_discussion.py  # build_schema_discussion_prompt() — includes two few-shot column-proposal
│   │                         #   examples for format compliance; parse_column_proposals() — 5-strategy parser
│   ├── project_export.py  # export_project() → io.BytesIO (ZIP: manifest.json + seeds.bib)
│   ├── project_import.py  # import_project() → (ImportResult, seed_ids); resolve_import() for collision
│   ├── project_merge.py   # merge_preview(), execute_merge() — non-destructive source→target copy
│   ├── scoring.py        # compute_relevance_score(citation_count, year) — shared formula used by
│   │                     #   the timeline API (TimelineNeighborWork.relevance_score) and the
│   │                     #   citation-list sort in api/works.py; formula: log(1+c) + max(0,(y-2000)/2)
│   └── work_lock.py      # _WorkLockRegistry singleton `work_lock`; stale locks auto-released after 600s
└── external/
    ├── base.py           # ExternalWork, ExternalVenue, ExternalAuthor, ExternalLocation
    ├── openalex.py       # OpenAlexClient — parse_work() extracts counts_by_year
    ├── crossref.py       # CrossrefClient — DOI lookup, fuzzy search
    ├── semantic_scholar.py # SemanticScholarClient — throttled to 1.1 req/s (_MIN_INTERVAL = 1.1)
    ├── llm_client.py     # AnthropicLLMClient, OpenAILLMClient, make_llm_client();
    │                     #   _normalize_base_url() appends /v1 to bare Ollama URLs;
    │                     #   PDF vision (base64 document block) is Anthropic-only
    ├── pdf_fetch.py      # fetch_pdf_for_work() — arXiv first, then Unpaywall
    └── grobid.py         # GrobidClient — PDF reference extraction via GROBID REST API

frontend/src/
├── api.ts                # All fetch functions
├── types.ts              # TypeScript interfaces matching backend schemas
├── lib/
│   └── timelineFilter.ts # computeCitationCount(), filterNeighbors(),
│                         #   applyVisibilityThreshold() — caps neighbors at 3 000 by relevance_score
├── utils/
│   └── proposalParser.ts # parseProposals() — 5-strategy parser: fenced block → fenced JSON →
│                         #   bare JSON → markdown table → numbered/bulleted bold list
├── hooks/                # React Query hooks per domain (useWorks, useVenues, useProjects,
│                         #   useTimeline, useEnrichment, useFields, useWorkNotes, useWorkPDFs,
│                         #   useSettings, useChatSessions, useExtraction, useLockStatus)
├── pages/
│   ├── ProjectDetailPage.tsx    # Timeline + Topic Lists + Notes + Venue Tiers tabs + pinned schema tabs
│   ├── ExtractionSchemasPage.tsx # Schema editor + ExtractionRunView; ?schema= URL param
│   ├── DiscussionPage.tsx       # LLM chat; context_type drives schema-design vs. paper mode;
│   │                            #   proposal parser produces ColumnProposalCards.
│   │                            #   Reads ?from=project|schemas|library for back-navigation:
│   │                            #     from=project → /projects/{id}
│   │                            #     from=schemas → /projects/{id}?tab=extract (+ &schema={id} if schemaId present)
│   │                            #     from=library → /library
│   │                            #   Library-mode entries (/works/{id}/discuss) pass ?projectId={id} so
│   │                            #   the back button can return to the correct project.
│   ├── LibraryPage.tsx
│   ├── VenuesPage.tsx
│   └── SettingsPage.tsx
└── components/
    ├── CitationTimeline.tsx    # D3 scatter plot
    ├── WorkDetailPanel.tsx     # Side panel
    ├── TimelineControls.tsx    # Filter bar
    ├── TimelineEnrichBar.tsx   # Collapsible enrichment info bar (Timeline tab only).
    │                           #   Collapsed: single summary line (✓ / ⚠) + "▾ Details" toggle.
    │                           #   Expanded: per-source rows for OpenAlex, Semantic Scholar, GROBID.
    │                           #   OA row: "Fetch all" uses existing sequential per-work 202 pattern.
    │                           #   S2 row: "Fetch all (~Ts)" starts a server-side bulk job via
    │                           #     POST /api/enrich/bulk/semantic-scholar → {job_id};
    │                           #     polls GET /api/enrich/jobs/bulk/{job_id} every 2s; shows
    │                           #     progress + Cancel button; on rate-limit shows warning (no retry).
    │                           #   GROBID row: shown only when grobidStatus.available (from QC cache);
    │                           #     "Extract all (~Ts)" uses POST /api/enrich/works/bulk/grobid.
    │                           #   Collapse state persisted in localStorage per project:
    │                           #     sotascope:project:{id}:enrichmentBarExpanded (default false)
    ├── ExtractionRunView.tsx   # Standalone; readOnlyPaperSelection prop for promoted tabs
    │                           #   Paper selection: ALL project seeds are selectable regardless of PDF.
    │                           #   Default first-load selection = seeds with extracted text only.
    │                           #   AI extraction (sparkle ✦) requires a PDF with extracted text;
    │                           #   papers without text show only the manual-fill pencil (✎) button.
    │                           #   "Extract X of N papers →" counts only selected papers WITH text.
    │                           #   PDF availability tracked via useQueries(['works', id, 'pdfs'])
    │                           #   so buttons update automatically when PDFs are uploaded.
    ├── ImportDialog.tsx        # 2-tab import (DOI / arXiv, Search by title); BibTeX tab removed from UI
    │                           #   auto-detects input type (10. prefix = DOI, else arXiv ID);
    │                           #   optional post-import topic list assignment via projectTopicLists prop
    ├── ColumnProposalCard.tsx  # LLM column proposal states; UserCancelledError exported for silent cancel
    ├── PageHeader.tsx          # Shared page header; accepts title (string) OR leftContent (ReactNode)
    │                           #   for custom left side (e.g. breadcrumbs). Children render on the right.
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
2. `_migrate_schema()` — add new columns/tables (doi_auto_resolved, sort_order, work_pdfs, citations_by_year, drop pdf_path, work_notes, extraction_status on work_pdfs, semantic_scholar_id on works, work_dois, extraction_schemas, extraction_columns, chat_sessions, chat_messages, source on citations backfilled to 'openalex')
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
- Forward citation queries can return hundreds of results for well-cited papers. All four candidate filter steps (direction, top-venues, topic-list, visibility threshold) are applied client-side before passing neighbors to `CitationTimeline`; the D3 component only receives the final ≤ 3 000 dots.
- BibTeX entry keys follow AuthorYearKeyword convention but the internal unique key is always DOI or arXiv ID.
- When transferring a UNIQUE field value between rows (e.g., during work merge), null out the field on the source row and `db.flush()` before setting it on the target — SQLAlchemy batches UPDATEs within a single flush with no guaranteed row ordering.
- TestClient + in-memory SQLite requires `StaticPool` + `check_same_thread=False`. Override `get_db` from `sotascope.api.deps` (not `sotascope.database`).
- `Field.venues` relationship uses `passive_deletes=True` because the DB has `ON DELETE CASCADE` on `VenueField.field_id`. Without this, SQLAlchemy tries to set `field_id = NULL` on eagerly-loaded relationships before delete, which fails because `field_id` is NOT NULL.
- The `citations_by_year` sliding window only works for works that have the data populated from OpenAlex. Works without it (e.g., imported from Crossref or BibTeX only) fall back to the all-time `citation_count` regardless of slider position. The startup backfill populates from cached responses; re-enriching a work will also populate it.
- `_update_work()` uses **keep-higher** logic for `citation_count`: `max(stored, incoming)` so that a higher value from one source (e.g. OpenAlex) is never overwritten by a lower S2 value. `citations_by_year` is still always overwritten (only populated by OpenAlex). After any forward-citation fetch, `_ensure_citation_count_floor()` additionally floors `citation_count` at the actual count of Citation rows in the DB.
- **LLM calls must be async**: use FastAPI `BackgroundTasks` or streaming responses. A single extraction pass over many papers can take minutes. Do NOT call LLM APIs synchronously in the request handler. The two extraction execution endpoints (`POST /schemas/{id}/extract/{work_id}` and `POST /schemas/{id}/extract`) return 202 immediately and run `_extraction_bg` as a BackgroundTask. Progress is tracked in `extraction_jobs` and polled via `GET /api/extraction/jobs/{job_id}`. Each work is locked via `work_lock` before the task starts and released per-work as it completes.
- **PDF vision is Anthropic-only**: sending the PDF binary directly to the model is only supported when `llm_provider = "anthropic"`. All other providers (including local OpenAI-compatible servers) must use extracted `.txt` text.
- **Table isolation in tests**: `Base.metadata.create_all()` in `conftest.py` runs before `from sotascope.app import app`. A test file that uses a model via the API but does NOT import that model at the top of the file will get "no such table" when run in isolation. Fix: add a bare import at the top (e.g. `from sotascope.models.chat import ChatSession`) so it registers in `Base.metadata` before `create_all()`.
- **Work lock registry** (`sotascope/services/work_lock.py`): module-level singleton `work_lock` tracks in-flight background operations per work ID. `acquire(work_id, task)` returns `False` if already locked; `release(work_id)` is always called in a `finally` block. Locks older than 10 minutes are treated as stale and auto-released on any access (guards against background task crashes). Uses `threading.Lock` — safe for single-worker uvicorn deployments.
- **Enrichment endpoints use BackgroundTasks and return 202**: the five slow enrichment endpoints (`/citations/backward`, `/citations/forward`, `/crossref`, `/semantic-scholar`, `/grobid`) return HTTP 202 immediately and run the actual network calls in a FastAPI `BackgroundTask`. Fast pre-validation (work existence, DOI presence, PDF existence, GROBID URL) is still done synchronously and returns 404/400 as before. HTTP 409 is returned if the work is already locked.
- **BackgroundTask DB sessions**: background functions must NOT use the request-scoped `db` session (FastAPI closes it after the response is sent). Each background function creates its own session via `SessionLocal()` from `sotascope.database` and closes it in a `finally` block alongside `work_lock.release()`. In tests, `SessionLocal()` connects to the real SQLite file (not the in-memory test DB), so background functions fail silently at the DB lookup stage — this is acceptable; API tests only assert the 202 status code. Service-layer behavior is covered by `test_enrichment_service.py`.
- **409 on destructive operations**: `DELETE /api/works/{id}` and `POST /api/works/{target_id}/merge/{source_id}` both check `work_lock.is_locked()` before proceeding and return 409 if a background task is in progress for any involved work ID.
- **Auto-enrichment on topic list addition**: `add_work_to_topic_list` in `api/projects.py` schedules `_auto_enrich_bg` (backward citations + forward citations + Crossref) as a BackgroundTask when a work is added to a topic list. If the work is already locked, enrichment is silently skipped (the addition still succeeds). `GET /api/works/lock-status` returns `{"locks": {"<work_id>": "<task>", …}}` for frontend polling.
- **Project venue tier resolution**: always use the helper function `resolve_venue_tier(project_id, venue_id, db)` rather than reading `Venue.tier` directly when in a project context. This ensures local overrides are respected.
- **Merge is non-destructive**: merging A into B copies content from A into B; A is left intact. Topic lists (unique name) → new TopicList row in B + copied TopicListWork rows. Topic lists (same name) → missing works copied into B's existing list. ProjectIgnoredWork, ProjectVenueTier, WorkNote (project_id) → new rows created in B. ExtractionSchema → deep copy (+ ExtractionColumn rows), apply rename/drop decision for conflicts; returns source_id→new_id mapping. ChatSession → deep copy (+ ChatMessage rows); context_id remapped via schema map; sessions whose schema was dropped get context_type reset to "papers".
- **Export uses stable IDs**: the export manifest references works by DOI or arXiv ID, venues by OpenAlex ID or ISSN, never by SQLite row IDs. This makes archives portable across SotAScope instances.
- **Import chat session context_id is not portable**: `ChatSession.context_id` stores a raw DB integer (schema ID). During import, extraction_schema sessions are reset to `context_type="papers"` and `context_id=None` because the original schema ID cannot be reliably remapped across instances. Work-specific sessions (`work_id`) are also reset to `work_id=None` because `work_id` is not included in the manifest.
- **Import bibtex_key uniqueness**: if a BibTeX key from the imported archive already exists in the library (on a different work), the import clears `bibtex_key` on the new work rather than failing. This preserves import correctness at the cost of the key not being set.
- **Import auto-enrichment not triggered on collision**: when `needs_project_decision=True` is returned, no auto-enrichment background tasks are scheduled. Enrichment is scheduled only after the user calls `resolve_import`. This prevents enrichment from running on a temp project that might be deleted.
- **Two-phase import pattern**: `POST /api/projects/import` → `ImportResult`. If `needs_project_decision=True`, follow up with `POST /api/projects/import/{temp_id}/resolve`. The temp project (`"$name - incoming"`) is a fully functional staging project during the collision-resolution phase. If `action="merge"`, `execute_merge` is called and the temp project is deleted after merge. If the user abandons the flow without resolving, the temp project remains in the DB — treat it as orphaned and delete manually if needed.

---

## Project export / import

### Export / Save (implemented)
- `GET /api/projects/{id}/export` → `.zip` (manifest.json + seeds.bib + optional files/)
- `GET /api/projects/{id}/export?include_files=true` → also includes PDFs and extracted `.txt` files under `files/{work_id}/` for all seeds that have uploaded PDFs. Archive built entirely in `io.BytesIO` — for very large projects a streaming approach would be needed.
- `GET /api/projects/{id}/export/bibtex?work_ids=...` → BibTeX text
- Manifest format_version: **2**. Works referenced by stable IDs (DOI → arXiv → OpenAlex — never SQLite row IDs). Only seeds exported; candidates re-discovered via auto-enrichment on import.
- `venue_tiers` section: effective tier (project-local override if present, else global `Venue.tier`) for every venue associated with any seed or citation-neighbour work in the project. Works with `venue_id=NULL` are skipped silently. Each entry also carries all `VenueAlias` strings for that venue (excluding the preferred alias, which is already stored in `venue_name`).
- `files` section: populated when `include_files=True`; each entry is `{work_id, doi, arxiv_id, filenames}`. `work_id` is the archive-side DB id (needed by the importer to locate files in the ZIP). Empty list `[]` when `include_files=False`.
- UI: "Save project (.zip)" in the Export dropdown; dialog has an "Include paper content (PDFs / extracted text)" checkbox (unchecked by default).
- Service: `sotascope/services/project_export.py` — `export_project(project_id, db, include_files=False) → io.BytesIO`

### Import (implemented)
- `POST /api/projects/import` (multipart `file` upload) → `ImportResult` (201)
  - Parses ZIP, validates format_version (errors on version > 2).
  - Matches works by DOI → arXiv → OpenAlex → title+year+first-author.  100% matches (title+year+same first author) → auto-match silently. Title+year without clear author agreement → ambiguous (flagged in `ImportResult.ambiguous_matches`, new work still created; user reviews via Library Sanitize later).
  - If project name collision: creates temp `"$name - incoming"` project, returns `needs_project_decision=True` + pre-computed `merge_preview`.
  - Otherwise: creates project directly, schedules auto-enrichment for all seed works.
  - **format_version=2**: processes `venue_tiers` via `_import_venue_tiers_v2()` — creates a `ProjectVenueTier` for every entry (importer's global tiers are never modified). Aliases not yet in the importer's `VenueAlias` table are collected as `PendingVenueAlias` and returned in `ImportResult.pending_venue_aliases` with `needs_alias_decision=True`. Auto-enrichment is NOT blocked by a pending alias decision.
  - **format_version=1**: falls back to old `venue_tier_overrides` handling; `needs_alias_decision` is always False.
- `POST /api/projects/import/{temp_id}/resolve` → `ProjectDetail` (200)
  - `action="merge"` + `target_project_id`: merges temp into existing, deletes temp, schedules enrichment.
  - `action="rename"` + `new_name`: renames temp project to new name, schedules enrichment.
- `POST /api/projects/import/{project_id}/resolve-aliases` → `{message}` (200)
  - Writes accepted `VenueAlias` rows to the global table; rejected decisions are ignored. Idempotent. No ownership validation (local-first tool assumption).
- PDF file import: `_import_pdf_files()` runs after `_import_works()`; reads all `files/` ZIP entries eagerly into memory before closing the archive, then copies each to `{pdf_root}/{local_work_id}/{filename}`, creates `WorkPDF` rows (skips if already exists for that work+filename), and sets `extraction_status="ready"` when a companion `.txt` is present. Unresolvable file entries (no matching work) are skipped with a warning.
- Service: `sotascope/services/project_import.py` — `import_project()`, `resolve_import()`, `_import_venue_tiers_v2()`, `_import_pdf_files()`
- Schemas: `sotascope/schemas/project_import.py` — `ImportResult`, `ImportResolveRequest`, `AmbiguousMatch`, `PendingVenueAlias`, `AliasDecision`, `ResolveAliasesRequest`
- Frontend: `ProjectImportDialog.tsx` — file upload → results → (optional) collision UI → (optional) alias confirmation UI → done. Alias step shown only for format_version=2 imports with new aliases; skippable.
- Tests: `tests/test_project_import.py` (42 tests)

### Project merge (implemented)
- Non-destructive: content from source copied into target; source remains intact.
- `GET /api/projects/{target_id}/merge-preview/{source_id}` → `MergePreview`
- `POST /api/projects/{target_id}/merge/{source_id}` (body: `MergeDecisions`) → `ProjectDetail`
- Service: `sotascope/services/project_merge.py` — `merge_preview()`, `execute_merge()`
- Schema decisions: `rename` (deep copy with new title) or `drop` (skip). Venue tier conflicts: user picks per-venue or "always best tier" bulk option.

### Per-project venue tiers (implemented)
- `ProjectVenueTier` table: project_id + venue_id + tier (unique). Absence = inherit global tier.
- `GET/PUT/DELETE /api/projects/{id}/venue-tiers/{venue_id}`
- Service: `sotascope/services/venue_tiers.py` — `resolve_venue_tier()`, `bulk_resolve_venue_tiers()`
- Frontend: "Venue Tiers" tab in `ProjectDetailPage`; shows "(global)" vs "(local)" with reset button.

---

## Known issues / future work

- **Semantic Scholar rate limits**: S2 enforces 1 req/s regardless of whether an API key is used. Without a key the quota is shared globally across all users from the same IP, so on shared/university networks 429 errors are common. An API key (`s2_api_key` setting) authenticates requests (your own dedicated quota at 1 req/s) — apply at https://www.semanticscholar.org/product/api. The client throttles to 1.1 req/s in both cases (`_MIN_INTERVAL = 1.1`, set slightly above 1.0 to avoid boundary 429s). The rate limiter is **process-global** and **shared** — bulk S2 fetches and single-paper fetches both go through the same `_throttle()` function and are serialised correctly. The search-by-title endpoint returns HTTP 429 with a user-readable message when S2 rate-limits us.
- **S2 missing reference lists**: S2 can return forward citations (papers citing a given work) even when it has no reference list for that work. This happens when S2 doesn't have full-text access to the paper. The "Fetch references (S2)" button shows "S2 has no reference list for this paper" in this case instead of a misleading "0 references" count.
- **S2 rate-limit backoff**: When a 429 occurs, S2 imposes a backoff of up to 1+ hour from the same IP. The only reliable mitigation is an API key.
- **Topic list legend**: The legend is rendered as an HTML flex-wrap div above the SVG, sourced from the full topic list array (not from visible data). It remains visible when all lists are toggled off. Inactive items dim to 40% opacity. This replaced an earlier D3-rendered legend that disappeared when all traces were deactivated.
- **Unpaywall requires contact email**: OA PDF fetch via Unpaywall is only attempted when `api_contact_email` is configured in Settings. Without it, only arXiv is tried (works that have `arxiv_id`). Works that have only a DOI but no `arxiv_id` and no configured email will return 404 from the fetch endpoint.
- **LLM local server auth**: `list_models()` correctly omits the `Authorization` header when `api_key` is empty for local servers (was sending `Bearer local`, causing 401 on servers that validate auth). The `chat()` endpoint still uses the openai SDK which sends `Bearer local`; most local servers (Ollama, LM Studio) accept this, but servers with strict auth validation may reject it.
- **Column-proposal re-prompting (not implemented)**: when all five `parseProposals()` strategies fail and the LLM response contains no parseable proposal, the UI silently shows the message as plain text. A possible future enhancement is to detect this case client-side and automatically re-prompt the LLM asking it to reformat its answer as a fenced `column-proposal` block.
- **GROBID reference extraction**: Requires Docker (`docker run -d --name grobid -p 8070:8070 grobid/grobid:0.8.2-crf`). CRF-only image is lightweight and fast; for better accuracy use the `-full` image (needs GPU). Settings page has a "Start" button (calls `POST /api/grobid/start`) that appears after a failed "Test connection" check; on success waits 5 s and auto-re-tests. **Re-run cleanup**: at the start of each GROBID enrichment run, all `Citation` records where `citing_work_id = seed` AND `source = "grobid"` are deleted. Any cited work that is now orphaned (no external IDs, not in any topic list, no remaining citation records) is also deleted. This prevents duplicate unresolved stubs from accumulating on re-runs while preserving works that were later manually enriched or added to topic lists. Reference resolution follows a 4-step chain per extracted reference:
  1. DOI present in GROBID output → existing DOI import path
  2. arXiv ID present → arXiv import path (OpenAlex → S2 fallback, as above)
  3. Neither → S2 title search with verification: match first-author surname AND year (±1). If S2 provides a DOI, validate it against Crossref (title/author comparison) — discard the DOI if it resolves to a different work, but keep the S2 CorpusId.
  4. No match → store as unresolved reference with GROBID metadata (title, authors, year, raw_url from `<ptr target>`, venue_display_name from `<monogr>` when `<analytic>` is present), shown in the UI with an "unresolved" badge for manual resolution later. Venue name is matched/created in the Venue table for unresolved references.
- **S2 CorpusId storage**: `Work.semantic_scholar_id` stores the numeric **CorpusId** (e.g. "123456789"), not the 40-char SHA `paperId`. CorpusIds are stable persistent identifiers; `paperId` SHAs can change when S2 reprocesses a paper. The `_to_s2_paper_id()` helper in `external/semantic_scholar.py` auto-prepends `CorpusId:` for numeric strings when constructing API URLs. **Legacy data**: works imported before this change may have a 40-char SHA in `semantic_scholar_id`. These still work at the API level (`get_paper_by_id` passes them unchanged, and the `/paper/{SHA}` endpoint is valid), but they lack the stability guarantee of CorpusIds. No automatic migration is provided.
- **S2 CorpusId as last-resort identifier**: papers imported via `semantic_scholar_id` only (no DOI, arXiv ID, or OpenAlex ID) cannot be enriched through OpenAlex. Citation graph data for these relies entirely on S2's API, subject to rate limits (see above).

---

## Running tests

```bash
python -m pytest tests/ -v          # all tests
cd frontend && npm run build        # TypeScript type check + production build (requires Node.js)
```

All tests should pass. There are no known pre-existing failures.
