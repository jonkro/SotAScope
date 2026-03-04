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
- **Work notes**: Per-work notes stored in the `WorkNote` table. Notes can be scoped to a project (`project_id` set) or general (`project_id` null). Each note has `content`, `note_type` (optional label), `provenance` ("user", "ai", or "ai_reviewed"), `model_id`, and `is_outdated` flag. Editing an AI-generated note changes provenance to "ai_reviewed".
- **Secondary DOIs (WorkDOI)**: A work may have multiple valid DOIs. The primary DOI lives on `Work.doi`; additional DOIs are stored in the `WorkDOI` table (`work_dois`, CASCADE delete). `doi_aliases: list[str]` is included in `WorkOut` via a Pydantic `field_validator(mode='before')`.
- The library stores a **venue tier list**: a user-maintained mapping of venues to tiers (1 = top venue, 2 = regular, 3 = ignore). Tiers are global per venue (not per-field). Venues can be associated with one or more research fields (e.g., "AI/ML", "Computer Networks") via a many-to-many relationship, but the tier is a single global value.
- **Venue aliases** handle year-to-year name variation (e.g., the same conference has different OpenAlex names across years). Aliases are manually reorderable; the first alias (by sort_order) is the **preferred alias** used for display throughout the UI.
- **Venue normalization** runs at startup: strips "Proceedings of the..." prefixes, calendar years, and ordinal edition numbers. Detects and merges duplicate venues after normalization, preserving old names as aliases.
- The library is the single source of truth for all paper metadata.
- **Library sanitization** tools: duplicate detection (by DOI, bibtex_key, or title+year), work merge (repoints citations, topic list memberships, authors, locations), and bulk deletion.

### 2. Project layer (per project)
- A project contains one or more **topic lists**. Each topic list is a named, color-coded set of "selected" papers (seeds). These represent sub-fields or themes the researcher is investigating.
- The project stores which papers from the library belong to which topic list.
- The project stores **ignored works**: papers explicitly marked as uninteresting, excluded from the timeline.
- Multiple projects can coexist and share the same library.

---

## External data sources

Three APIs are actively integrated:

| Source | Role |
|---|---|
| **OpenAlex** | Primary source: citation graph, forward citations, paper metadata, venue info, per-year citation counts |
| **Crossref** | DOI resolution (including fuzzy search), authoritative venue metadata (ISSN, publisher), search-by-title candidates |
| **Semantic Scholar** | Supplemental: on-demand citation enrichment, search-by-title fallback when Crossref returns no results |

### API authentication
All clients support a "polite pool" email for better rate limits. This email is configurable via:
1. A **database setting** (`api_contact_email`) editable from the Settings page (preferred)
2. Environment variable fallback (`LITEXPLORER_OPENALEX_API_KEY`, `LITEXPLORER_CROSSREF_MAILTO`)
If no email is configured, the clients still work but with lower rate limits.

Note: OpenAlex uses `mailto` query parameter for polite pool access, not Bearer token auth.

### SSL verification
All external HTTP clients respect the `ssl_verify` database setting (default `"true"`). Setting it to `"false"` disables SSL certificate verification — useful for corporate proxies with custom CAs. A global `httpx.ConnectError` exception handler returns 503 with an `SSL_CERTIFICATE_ERROR:` prefix for frontend detection.

### Caching policy
All API responses are cached in an `api_cache` table with source and query key.
- **Backward citations** (references listed in a paper): cached permanently once fetched
- **Forward citations** (papers citing a given work): cached with a timestamp. The UI surfaces a "last fetched" date and a manual refresh button. Auto-refresh is not performed.
- **Paper metadata**: cached permanently, with a manual refresh option via re-enrichment
- **DOI resolution results**: cached permanently

### Auto-enrichment
When a work is added to a topic list (becoming a seed), the system automatically:
1. Fetches backward citations (references) from OpenAlex
2. Fetches forward citations (citing papers) from OpenAlex
3. Enriches venue metadata from Crossref (if DOI available)

This runs in parallel and the UI shows a progress indicator during the process.

When forward citations are fetched, the seed work's own metadata (`citation_count`, `citations_by_year`) is refreshed from OpenAlex via `_refresh_work_metadata()`.

### DOI resolution
For works without a DOI (e.g., imported from BibTeX without one), the system can:
- **Auto-resolve**: Crossref fuzzy search with configurable thresholds (score >= 80, ratio to 2nd candidate >= 1.5). Auto-resolved DOIs are marked with `doi_auto_resolved = true`.
- **Manual confirmation**: If auto-resolution confidence is low, candidates are presented to the user for selection.
- Batch resolution is supported for multiple works at once.

---

## Core visualization: Citation Timeline

The main view of a project is a **timeline** (x-axis = publication year, y-axis = log-scaled citation count):

- **Seed papers** (papers in any topic list): shown as filled **squares**, color-coded by topic list. If a paper is in multiple topic lists, vertical color stripes are used.
- **Backward neighbors** (references of seed papers): shown as **circles** in muted gray
- **Forward neighbors** (papers citing seed papers): shown as **diamonds** (rotated squares) in muted gray

Dot size scales by `sqrt(connectivity)` where connectivity = 1 + number of seed connections. Within each year column, dots are jittered horizontally using a deterministic hash (Knuth multiplicative) to avoid overlap while maintaining stable positions.

### Y-axis: Citation count with sliding window
The y-axis shows citation count on a `log(1+x)` scale. A "Count citations" slider controls the time window:
- **All** (default): uses the all-time `citation_count` from OpenAlex
- **Of last Ny** (1–10 years): sums `cited_by_count` from `citations_by_year` entries within the window

This is computed client-side via `computeCitationCount()` — no API re-fetch on slider change. Works without `citations_by_year` data fall back to the all-time `citation_count` regardless of slider position.

Y-axis ticks show untransformed integers at powers of 10 (0, 1, 10, 100, 1000...). Label: "Citations".

### Paper inclusion logic
- Papers from tier-3 (ignored) venues are always excluded
- **Candidate filter** dropdown: All (show all candidates), Top venues (tier-1 only), None (seeds only)
- No score threshold — all candidates passing the venue/direction filters are shown

### Interaction
- **Click a dot**: show a side panel with paper metadata, abstract, venue, citation count, and its connections. When dots overlap, clicking cycles through them.
- **K-hop connections**: a segmented control (1, 2, 3) visualizes multi-hop graph connectivity from the selected paper. Direct edges are solid indigo lines; farther edges are dashed. Intermediate pathway nodes are highlighted with an amber outline.
- **Add to topic list**: buttons in the side panel let the user add the paper to any topic list (making it a new seed and triggering auto-enrichment)
- **Remove from topic list**: buttons to remove the paper from topic lists it belongs to
- **Mark uninteresting**: for neighbor papers, moves them to the project's ignored list
- **Citation list markers**: References and Cited-by lists in the side panel show SVG markers matching the timeline shapes (colored squares for seeds, grey circles for backward refs, grey diamonds for forward cites). Entries that are visible in the timeline render as clickable buttons (`cursor-pointer`, `hover:underline`). Clicking navigates to that paper (panel + timeline both update). On every selection change, a brief indigo ripple expands outward from the newly selected dot over 650 ms (D3 transition, fires only on genuine selection changes via `prevSelectedWorkIdRef`).
- **Collapsible sections**: Abstract, Locations, Actions, References, and Cited-by sections are collapsible. Fold state persists across paper selections within the same page.

### Timeline controls
- **Count citations**: sliding window slider (all / of last 10y down to 1y)
- **References / Cited by**: checkboxes to toggle direction visibility
- **Candidates**: dropdown (All / Top venues / None)
- **Hops**: segmented button (1, 2, 3)
- **From**: year range slider (when data spans multiple years)
- **Stats**: "Showing N of M candidates"

### Per-client state persistence
Timeline settings (citations window, direction, candidates, hops, start year, active tab) are stored in `localStorage` per project (`litexplorer:project:{id}:view`). Each browser client has independent settings. The sidebar "Projects" link remembers the last `/projects/*` path within the current session (via React ref, not localStorage).

---

## Phase 1 scope (implemented)

- Library layer: BibTeX import, DOI/arXiv keying, venue tier list, venue aliases with preferred alias
- Venue normalization and deduplication at startup
- Library sanitization: duplicate detection, work merge, deletion
- Project layer: topic lists with color coding, ignored works
- OpenAlex + Crossref integration with caching
- DOI auto-resolution via Crossref fuzzy search
- Auto-enrichment on seed addition
- Citation timeline visualization with citation count y-axis and sliding window
- Paper side panel with add/remove from topic list, mark uninteresting, citation browsing
- K-hop connection visualization (1-3 hops)
- Import: BibTeX file, list of DOIs, or **search by title** (Crossref with S2 fallback)
- Settings page for API contact email, PDF storage path, and SSL verification toggle (stored in database)
- Venue management UI: alias editing, reordering, tier assignment, field association
- Venues page with Venues tab (sortable table) and Fields tab (CRUD with deletion)
- PDF management: upload, serve inline, set primary, delete (moved to orphaned folder)
- **PDF text extraction**: auto-extracted on upload via `litexplorer/services/pdf.py` (pdfplumber); two-column layout detection via x0 histogram heuristics; status tracked in `WorkPDF.extraction_status` (`pending`/`ready`/`failed`); companion `.txt` stored at `{pdf_root}/{work_id}/{stem}.txt`; re-extract endpoint; "View text" / "Extract text" / "Re-extract" UI in WorkDetailPanel
- Work notes: per-work and project-scoped notes with labels, provenance tracking
- Project notes tab: aggregated view of all notes for a project, sortable by paper or label
- Filesystem browser for configuring PDF storage path
- Per-client timeline state persistence via localStorage
- **Topic list visibility toggle**: clicking a TL legend entry in the citation timeline hides/shows its seeds (and candidates connected only to that TL). Multi-TL seeds lose the deactivated color stripe. Toggle state persists in `localStorage` alongside other timeline settings. Implemented entirely client-side — no backend changes required.
- Deployment: `README.md`, `litexplorer.service` (optional systemd unit), `env.example`; pre-built frontend committed to `frontend/dist/` (no Node.js required to run)
- **Semantic Scholar integration**: `SemanticScholarClient` (`external/semantic_scholar.py`); `Work.semantic_scholar_id` column; on-demand enrichment endpoint `POST /api/enrich/works/{id}/semantic-scholar?direction={both|backward|forward}` (fetches refs/citations by direction, returns new/existing/raw counts); editable S2 ID field in WorkDetailPanel; deduplication by S2 ID as 4th fallback after DOI/openalex_id/arxiv_id; `s2_api_key` DB setting (authenticates requests; S2 enforces 1 req/s with or without key); `SemanticScholarEnrichResult` includes `raw_references` and `raw_citing` (items returned by S2 API before dedup, used by UI to distinguish "S2 has no data" from "already in library"); `enrich_from_semantic_scholar()` falls back to title-search when DOI and S2 ID lookups both fail: normalizes title via `_normalize_title_for_cmp()` (NFKD + Jaccard) and searches S2 by title, taking the first exact-normalized-title match
- **Multi-DOI support**: `WorkDOI` table stores secondary DOIs per work (CASCADE delete). `doi_aliases` returned in `WorkOut`. `GET/POST/DELETE /api/works/{id}/doi-aliases` endpoints. `GET /api/enrich/doi/info?doi=...` looks up title/year for a DOI without importing (OA cache → live OA → Crossref fallback). WorkDetailPanel shows editable primary DOI and secondary DOI list, both with real-time title similarity check (Jaccard ≥ 0.7 = green, < 0.7 = amber warning, not found = red).
- **SSL verify toggle**: `ssl_verify` DB setting (default `"true"`); passed as `verify=` to all httpx clients; checkbox in Settings with amber warning; `formatError()` in ImportDialog detects SSL errors; global 503 handler with `SSL_CERTIFICATE_ERROR:` prefix
- **WorkDetailPanel enrichment buttons**: 5 buttons in Actions section: "Fetch references (OA)", "Fetch citing papers (OA)", "Fetch references (S2)", "Fetch citing papers (S2)", "Enrich from Crossref"; works without a DOI show "Resolve DOI (CrossRef)" instead of Crossref button; each S2 button calls the endpoint with appropriate direction param; status messages distinguish S2 returning 0 items ("S2 has no reference list for this paper") from all-already-existing case
- **LLM provider configuration (Phase 2 pre-work)**: four DB settings (`llm_provider`, `llm_api_key`, `llm_model_id`, `llm_base_url`) seeded in `_seed_default_settings()`; `litexplorer/external/llm_client.py` — `ContextDocument` dataclass, abstract `LLMClient`, `AnthropicLLMClient`, `OpenAILLMClient`, `make_llm_client()` factory; `GET /api/llm/models` endpoint (reads DB settings, returns model list or soft error); `anthropic` and `openai` added as required dependencies in `pyproject.toml`; Settings page `/settings` has a dedicated **LLM Configuration** section: provider dropdown, API key (password, save on blur), base URL (save on blur, triggers model list re-fetch), model picker (loading spinner → populated dropdown or free-text fallback on error/empty), "Test connection" button, PDF vision note for Anthropic; `OpenAILLMClient.list_models()` uses httpx directly when `base_url` is set (stores `_real_api_key`; sends no auth header when api_key empty, sends `Bearer` only when a real key is provided) — avoids sending `Bearer local` to local servers that validate auth

- **Topic list UX**: When a work is added from the candidates list, the target topic list auto-expands (`forceExpand` prop on `TopicListCard`); added works are removed from the candidate list to prevent duplicates. `ProjectDetailPage` tracks `expandedListId` and filters `searchedWorks` against already-added members.

### Not yet implemented from Phase 1 spec
- (All Phase 1 items are now implemented)

## Phase 2 scope

### Implemented

- **LLM provider configuration**: `LLMClient` abstraction with `AnthropicLLMClient` and `OpenAILLMClient`; four DB settings (`llm_provider`, `llm_api_key`, `llm_model_id`, `llm_base_url`); `GET /api/llm/models` endpoint; Settings page UI with model picker, test connection, and PDF vision note
- **Structured extraction (backend)**: `ExtractionSchema` + `ExtractionColumn` models; `litexplorer/services/extraction.py` with `assemble_extraction_prompt()`, `parse_extraction_response()`, `run_extraction_for_work()`; full CRUD + extraction API at `/api/extraction`; new `llm_system_prompt_prefix` DB setting. Extraction results stored as `WorkNote` rows (`provenance="ai"`), two per column (answer + reasoning). 34 tests in `tests/test_extraction.py`.
- **Structured extraction frontend**: `ExtractionSchemasPage` at `/projects/:projectId/extraction`; schema list view → new-schema form → schema editor with two tabs: **Schema** (edit title/description/columns) and **Extract & Review** (run extraction + results table). `ColumnFormModal` with tag-style allowed-values input; up/down column reordering; `useExtraction.ts` hooks; "Extraction Tables" button in `ProjectDetailPage` header.
- **Structured extraction run & review UI**: `ExtractionRunView` component in `ExtractionSchemasPage`; seed paper selector (searchable checkboxes from `useTimeline`); Extract button with per-paper progress indicator; confirmation dialog when re-running on papers with existing notes; results table (rows = papers, columns = schema columns). `ExtractionCell` component: answer text + `ProvenanceBadge` (ai=blue, ai_reviewed=purple, user=green) + ✓ Accept button (ai notes only, sets provenance to `ai_reviewed`) + ✎ Edit (inline — dropdown for constrained columns, textarea for free-form) + ⓘ reasoning toggle + ⚡ single-paper extract for empty cells. Re-extraction skips `ai_reviewed`/`user` notes and deletes+replaces `ai` notes (no duplicates). New endpoint: `GET /api/extraction/schemas/{id}/results?work_ids=1,2,3` → `ExtractionResultsResponse({cells: ExtractionCellResult[]})`. `WorkNoteUpdate` now accepts `provenance` field; explicit provenance overrides auto-upgrade logic. New types: `ExtractionCellResult`, `ExtractionResultsResponse`. New hooks: `useExtractionResults`, `useRunSingleExtraction`, `useRunBatchExtraction`, `useAcceptExtractionNote`, `useEditExtractionNote`.

### Not yet started

- **Per-paper chat**: discuss a paper with an LLM to accelerate understanding
- **CSV export**: `GET /api/extraction/schemas/{id}/export?project_id=N` — export extraction results as CSV

### Phase 2 design notes

- **PDF text is ready for LLM use**: `WorkPDF.extraction_status='ready'` works have a companion `.txt` at `{pdf_root}/{work_id}/{stem}.txt`. Serve via `GET /api/works/{id}/pdfs/{id}/text`. This is the primary per-paper LLM input.
- **WorkNote table is LLM-ready**: `provenance` ("user"/"ai"/"ai_reviewed") and `model_id` fields already exist. Per-paper chat turns or LLM summaries can be stored as WorkNotes. `project_id` scoping allows associating results with a specific project.
- **`llm_base_url`**: optional. When set, overrides the provider's default cloud endpoint. Enables local inference servers (Ollama, vLLM, LM Studio, llama.cpp) that expose an OpenAI-compatible API (e.g., `http://localhost:11434/v1`). `llm_api_key` may be left blank when using a local endpoint.
- **PDF vision mode (Anthropic-only)**: sending the PDF binary directly to the model (vision input) is only supported when `llm_provider = "anthropic"`. When any other provider is configured — including local OpenAI-compatible servers — the "use PDF" toggle in the per-paper chat UI is disabled and the fallback is extracted `.txt` text.
- **Conversation history — stateless backend**: full conversation history is sent with every chat request; the backend does not persist conversation turns. Conversations are lost on page refresh. This is intentional for Phase 2.
- **Structured extraction — tables now exist**: `ExtractionSchema` (title, description, nullable project_id) + `ExtractionColumn` (name, prompt, description, allowed_values JSON, sort_order). Results stored as `WorkNote` rows (`provenance="ai"`, `note_type="{schema.title} / {column.name}"`). CSV export not yet implemented.
- **LLM calls must be async**: use FastAPI `BackgroundTasks` or streaming responses. A single extraction pass over many papers can take minutes. Do NOT call LLM APIs synchronously in the request handler.
- **Context window strategy**: typical extracted PDF text is 5k–40k tokens. Send abstract+title first; include full text only when available. For very long papers, consider truncating to the first N tokens or chunking by section.
- **Anthropic SDK**: `anthropic` Python package; `client.messages.create()` for standard calls, `client.messages.stream()` for streaming. Already a required dependency in `pyproject.toml`.

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
- **Deployment target**: a Linux server on a local network, accessed by a small team of trusted collaborators via their browsers. No public internet exposure is assumed.
- **Startup**: the app is launched via `uvicorn`. The FastAPI app serves the built frontend as static files (SPA fallback for client-side routing).
- **Data directory**: all persistent data (SQLite DB, PDFs, API cache) lives under a single configurable root directory (`~/.litexplorer` by default), set via the `LITEXPLORER_DATA_DIR` environment variable.
- **Dependency management**: conda environment for Python version isolation (`environment.yml` pins Python 3.11), `pyproject.toml` for all Python package dependencies. Install with `pip install -e .` in the conda environment.

---

## Project structure

```
litexplorer/
├── app.py                # FastAPI app with lifespan (startup migrations, backfills, SPA mount)
├── config.py             # Settings via Pydantic BaseSettings (LITEXPLORER_ env prefix)
├── database.py           # Engine, SessionLocal, init_db()
├── models/
│   ├── base.py           # SQLAlchemy DeclarativeBase
│   ├── library.py        # Work (with citations_by_year JSON), WorkLocation, Author, WorkAuthor,
│   │                     #   Venue, VenueAlias, Field (passive_deletes=True), VenueField,
│   │                     #   Citation, WorkPDF (extraction_status: pending/ready/failed), WorkNote, WorkDOI (secondary DOIs, CASCADE delete)
│   ├── project.py        # Project, TopicList, TopicListWork, ProjectIgnoredWork
│   ├── extraction.py     # ExtractionSchema (title, description, nullable project_id),
│   │                     #   ExtractionColumn (name, prompt, description, allowed_values JSON, sort_order)
│   ├── cache.py          # ApiCache (permanent / timestamped)
│   └── settings.py       # Setting (key-value store)
├── schemas/              # Pydantic v2 request/response models
│   ├── works.py          # WorkOut (with semantic_scholar_id), WorkDetail, WorkCreate, BibtexImportResult,
│   │                     #   WorkPDFOut (with extraction_status: Literal[ready/failed/pending]), etc.
│   ├── venues.py         # VenueOut, VenueDetail, VenueAliasOut, etc.
│   ├── projects.py       # ProjectOut, ProjectDetail, TopicListOut, etc.
│   ├── enrichment.py     # EnrichDOIResult, CitationResult, DOIResolutionResult,
│   │                     #   SemanticScholarEnrichResult, SearchImportRequest,
│   │                     #   SearchImportCandidate, SearchImportCandidatesResult,
│   │                     #   SearchImportConfirmRequest, DOIInfoResult
│   ├── timeline.py       # TimelineResponse, TimelineSeedWork, TimelineNeighborWork
│   │                     #   (seeds/neighbors include citations_by_year: list[dict] | None)
│   ├── fields.py         # FieldOut (includes venue_count: int)
│   ├── notes.py          # WorkNoteCreate, WorkNoteUpdate (+ provenance field), WorkNoteOut, ProjectNoteOut
│   ├── extraction.py     # ExtractionSchemaCreate/Update/Out, ExtractionColumnCreate/Update/Out,
│   │                     #   ColumnReorderRequest, ExtractionColumnResult, ExtractionWorkResult,
│   │                     #   ExtractionBatchRequest, ExtractionBatchResult,
│   │                     #   ExtractionCellResult, ExtractionResultsResponse
│   └── settings.py       # SettingOut, SettingUpdate
├── api/
│   ├── deps.py           # get_db dependency
│   ├── works.py          # /api/works — CRUD, BibTeX import, citations, merge, duplicates,
│   │                     #   PDF upload/serve/delete/extract-text/text, notes CRUD,
│   │                     #   DOI alias CRUD: GET/POST/DELETE /{id}/doi-aliases
│   ├── venues.py         # /api/venues — CRUD, aliases, field associations, sortable (sort_by, sort_dir)
│   ├── fields.py         # /api/fields — CRUD + DELETE /{field_id} (cascade deletes VenueField)
│   ├── projects.py       # /api/projects — CRUD, topic lists, ignored works
│   ├── enrichment.py     # /api/enrich — DOI import, citation fetching, Crossref, DOI resolution,
│   │                     #   S2 enrichment, search-import/candidates, search-import/confirm,
│   │                     #   doi/info (DOIInfoResult)
│   ├── timeline.py       # /api/projects/{id}/timeline — timeline data aggregation
│   ├── notes.py          # /api/projects/{id}/notes — project-scoped notes aggregation
│   ├── settings.py       # /api/settings — key-value settings CRUD
│   ├── filesystem.py     # /api/filesystem — directory browser + mkdir
│   └── extraction.py     # /api/extraction — schema/column CRUD, extraction execution,
│                         #   GET /schemas/{id}/results?work_ids=... (ExtractionResultsResponse)
├── services/
│   ├── enrichment.py     # EnrichmentService — import, citation fetching, venue normalization,
│   │                     #   DOI resolution, cache management, deduplication,
│   │                     #   _refresh_work_metadata(), import_by_semantic_scholar_id(),
│   │                     #   search_import_candidates() (Crossref-first with S2 fallback),
│   │                     #   enrich_from_semantic_scholar()
│   ├── pdf.py            # extract_pdf_text(), _detect_two_column(), _words_to_text()
│   │                     #   ExtractionError; two-column detection via x0 histogram heuristics
│   └── extraction.py     # assemble_extraction_prompt(), parse_extraction_response(),
│                         #   run_extraction_for_work() — builds LLM prompt, parses JSON response,
│                         #   creates WorkNote rows (answer + reasoning per column);
│                         #   skips ai_reviewed/user notes; deletes stale ai notes before re-creating
└── external/
    ├── base.py           # ExternalWork (with semantic_scholar_id, citations_by_year),
    │                     #   ExternalVenue, ExternalAuthor, ExternalLocation
    ├── openalex.py       # OpenAlexClient — DOI lookup, batch fetch, forward citations
    │                     #   parse_work() extracts counts_by_year from OpenAlex responses
    ├── crossref.py       # CrossrefClient — DOI lookup, fuzzy search
    └── semantic_scholar.py # SemanticScholarClient — paper lookup by DOI/S2 ID,
                          #   get_references(), get_citations(), search_by_title()

frontend/src/
├── App.tsx               # Routes: /projects, /projects/:id, /library, /venues, /settings
├── api.ts                # All fetch functions (works, venues, projects, enrichment, PDFs, notes, fields, etc.)
├── types.ts              # TypeScript interfaces matching backend schemas
│                         #   (includes CitationsByYearEntry, WorkNote, ProjectNote, WorkPDFOut,
│                         #    ExtractionCellResult, ExtractionResultsResponse, etc.)
├── queryClient.ts        # TanStack React Query client configuration
├── lib/
│   └── timelineFilter.ts # computeCitationCount(), filterNeighbors()
├── hooks/                # React Query hooks for each domain
│   ├── useWorks.ts
│   ├── useVenues.ts      # accepts sort_by, sort_dir params
│   ├── useProjects.ts
│   ├── useTimeline.ts
│   ├── useEnrichment.ts
│   ├── useFields.ts      # includes useDeleteField()
│   ├── useVenueTiers.ts
│   ├── useWorkNotes.ts   # useWorkNotes(), useProjectNotes(), useCreateWorkNote(), etc.
│   ├── useWorkPDFs.ts    # useWorkPDFs(), useUploadWorkPDF(), useSetWorkPDFPrimary(),
│   │                     #   useDeleteWorkPDF(), useExtractWorkPDFText()
│   ├── useSettings.ts
│   └── useExtraction.ts  # useExtractionSchemas(), useExtractionSchema(),
│                         #   useCreateExtractionSchema(), useUpdateExtractionSchema(),
│                         #   useDeleteExtractionSchema(), useCreateExtractionColumn(),
│                         #   useUpdateExtractionColumn(), useDeleteExtractionColumn(),
│                         #   useReorderExtractionColumns(), useExtractionResults(),
│                         #   useRunBatchExtraction(), useRunSingleExtraction(),
│                         #   useAcceptExtractionNote(), useEditExtractionNote()
├── pages/
│   ├── ProjectsPage.tsx         # Project listing with create/delete
│   ├── ProjectDetailPage.tsx    # Timeline + Topic Lists + Notes tabs, localStorage persistence
│   │                            #   "Extraction Tables" button → /projects/:id/extraction
│   ├── ExtractionSchemasPage.tsx # Schema list / new-schema form / schema editor (Schema + Extract & Review tabs)
│   │                            #   ExtractionRunView, ExtractionCell, ProvenanceBadge, ColumnFormModal
│   ├── LibraryPage.tsx          # Work listing with search, pagination, venue filter
│   ├── VenuesPage.tsx           # Venues tab (sortable table) + Fields tab (CRUD with delete)
│   └── SettingsPage.tsx         # Database-stored settings editor + PDF folder browser
└── components/
    ├── AppShell.tsx            # Layout: Sidebar + Outlet
    ├── Sidebar.tsx             # Nav: Projects (remembers last path), Library, Venues, Settings
    ├── CitationTimeline.tsx    # D3 scatter plot with log1p citation count y-axis
    ├── WorkDetailPanel.tsx     # Side panel with collapsible sections, markers, actions, notes
    ├── TimelineControls.tsx    # Filter bar: citation window, direction, candidates, hops, year range
    ├── TimelineEnrichBar.tsx   # Enrichment progress for seed papers
    ├── ImportDialog.tsx        # 3-tab import: DOI list, BibTeX, Search by title
    ├── SearchImportCandidateDialog.tsx # Radio-picker for search-by-title candidates (source badge)
    ├── SanitizeDialog.tsx      # Library cleanup tools
    ├── DOIResolutionDialog.tsx # DOI candidate selection
    ├── TopicListCard.tsx       # Expandable topic list with works
    ├── TopicListFormDialog.tsx # Create/edit topic list (name + color)
    ├── ProjectFormDialog.tsx   # Create/edit project
    ├── WorkCard.tsx            # Library list item
    ├── VenueTierEditor.tsx     # Inline tier/field assignment
    ├── ConfirmDialog.tsx       # Generic confirmation modal
    ├── PageHeader.tsx, SearchInput.tsx, Pagination.tsx, EmptyState.tsx,
    ├── Badge.tsx, ColorPicker.tsx
    └── ...

tests/
├── conftest.py                # db_session + client fixtures (StaticPool, in-memory SQLite)
├── test_library_api.py        # Work CRUD, BibTeX import, citations, merge, field deletion, venue CRUD
├── test_enrichment_api.py     # Enrichment endpoints with mocked clients
├── test_enrichment_service.py # EnrichmentService unit tests
├── test_enrichment_crossref.py# Crossref enrichment tests
├── test_crossref_client.py    # Crossref client unit tests
├── test_project_api.py        # Project/topic list CRUD
├── test_timeline_api.py       # Timeline endpoint + citations_by_year tests
├── test_openalex_client.py    # OpenAlex client (parse_work, client methods)
├── test_pdf_api.py            # PDF upload, serve, set primary, delete
├── test_notes_api.py          # Note CRUD operations
├── test_filesystem_api.py     # Directory browsing and mkdir
├── test_extract.py            # PDF text extraction: two-column detection, ordering, ExtractionError
├── test_settings_api.py       # ssl_verify setting: read, update, _get_ssl_verify() helper
├── test_semantic_scholar_enrichment.py  # S2 enrichment endpoint (refs/citations, dedup, error cases)
├── test_search_import.py      # Search-by-title candidates + confirm endpoints (12 tests)
├── test_extraction.py         # Schema/column CRUD, prompt assembly, response parsing, extract endpoints (34 tests)
└── fixtures/
    ├── openalex_responses.py  # Sample API response fixtures
    ├── generate_fixtures.py   # One-off script to regenerate synthetic PDF fixtures (fpdf2 + matplotlib)
    └── pdfs/
        ├── two_column.pdf     # Synthetic two-column fixture (committed)
        └── single_column.pdf  # Synthetic single-column fixture (committed)
```

---

## Startup lifecycle (app.py lifespan)

1. `init_db()` — create engine and tables
2. `_migrate_schema()` — add new columns/tables (doi_auto_resolved, sort_order, work_pdfs, citations_by_year, drop pdf_path, work_notes, sqlite_sequence tracking, extraction_status on work_pdfs, semantic_scholar_id on works, work_dois, extraction_schemas, extraction_columns)
3. `_seed_default_fields()` — create "AI/ML" and "Computer Networks" fields
4. `_seed_default_settings()` — create `api_contact_email`, `pdf_storage_path`, `ssl_verify`, `s2_api_key`, `llm_provider`, `llm_api_key`, `llm_model_id`, `llm_base_url`, `llm_system_prompt_prefix` settings
5. `_normalize_existing_venue_names()` — strip prefixes, merge duplicate venues
6. `_backfill_citations_by_year()` — populate `citations_by_year` from cached OpenAlex responses (scans `work:doi:*`, `backward_citations:*`, and `forward_citations:*` cache entries)

---

## Multi-user model (Phase 1 simplified)

The deployment context is a small team of trusted collaborators on a shared local server. In Phase 1:
- There is no login or authentication
- All users share the same **library layer** (papers, PDFs, venue tier list)
- Each **project** has an owner (stored in the DB) but is visible and editable by all users — trust is assumed
- Concurrent writes are handled by SQLite's WAL mode, which is sufficient for a small team
- Timeline settings (citation window, filters, etc.) are per-browser-client via localStorage

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

---

## Design deviations from original spec

- **Y-axis changed from importance score to citation count**: The original spec defined an importance score formula `(citation_count / age) * decay` with threshold and decay controls. This was replaced with a direct citation count y-axis using `log(1+x)` scale, with a "Count citations of last N years" sliding window. The threshold and decay sliders were removed.
- **No score-based filtering**: The original spec had a scored tier where candidates were shown above a user-adjustable threshold. This was removed — all candidates passing venue/direction filters are shown.
- **PDF management fully implemented**: Originally deferred to Phase 2, PDF upload/serve/delete is now fully functional with the `WorkPDF` table (replacing the removed `pdf_path` column on Work).
- **Work notes system added**: Not in original spec — added to support per-paper annotations with provenance tracking (user vs AI-generated).
- **Fields tab on Venues page**: Field creation moved from the Venues page header to a dedicated Fields tab, with field deletion support.
- **Venue table sorting**: Venues table headers are clickable to sort ascending/descending by any column.
- **PDF text extraction added**: auto-extraction on upload, re-extract endpoint, companion `.txt` file colocated with the PDF. Two-column layout handled by x0-histogram heuristics (gutter gap + right-column margin spike). Deleted PDFs move both the `.pdf` and `.txt` to `_orphaned/`.
- **Pre-built frontend committed**: `frontend/dist/` is committed to the repo so users only need conda + pip to run the app. Node.js is only needed to rebuild after frontend source changes.
- **Deployment docs added**: `README.md`, `litexplorer.service` (optional systemd), `env.example` (template for machine-specific env vars).
- **SSL verification toggle added**: `ssl_verify` DB setting (default `"true"`), threaded to all httpx clients. Checkbox in Settings page. SSL errors return 503 with detectable `SSL_CERTIFICATE_ERROR:` prefix.
- **Semantic Scholar integration added**: Full `SemanticScholarClient` implementation (was previously reserved as an enum value only). `Work.semantic_scholar_id` column (VARCHAR(128), nullable). On-demand enrichment endpoint. WorkDetailPanel shows editable S2 ID field and "Fetch from Semantic Scholar" button.
- **Search-by-title import added**: "Search by Title" tab in ImportDialog. Backend searches Crossref first, falls back to S2 if Crossref returns nothing. `SearchImportCandidateDialog` shows radio-pick candidates with source badges (Crossref = green, Semantic Scholar = purple).
- **S2 direction param**: `POST /api/enrich/works/{id}/semantic-scholar` accepts `?direction=backward|forward|both` (default `both`). WorkDetailPanel has separate "Fetch references (S2)" and "Fetch citing papers (S2)" buttons.
- **Topic list visibility toggle added**: Clicking a topic list entry in the citation timeline legend toggles it inactive (40% opacity, pointer cursor). `inactiveTopicListIds: Set<number>` state in `ProjectDetailPage`, serialized as `number[]` in localStorage. Derived memos: `activeTopicListIds`, `activeSeedIds`, `filteredSeeds`, `filteredSeedCitations`. `filteredNeighbors` excludes neighbors whose connections are all to inactive seeds. `CitationTimeline` receives `activeTopicListIds` + `onToggleTopicList` props; legend items carry optional `topicListId`. Storing **inactive** IDs (not active) is intentional: empty set = all active, so new topic lists are automatically visible.
- **Multi-DOI support added**: A work can have multiple valid DOIs. Primary DOI stays on `Work.doi`; secondary DOIs use the new `WorkDOI` table. `doi_aliases` included in `WorkOut`. WorkDetailPanel allows editing the primary DOI and managing secondary DOIs, with a Jaccard title-similarity check against the DOI's resolved title to warn about mismatches.
- **Structured extraction added**: `ExtractionSchema` + `ExtractionColumn` models, full CRUD API at `/api/extraction`, and `litexplorer/services/extraction.py`. Results stored as `WorkNote` rows (provenance tracking built-in). Frontend `ExtractionSchemasPage` provides schema management and an Extract & Review tab for running extraction against project seed papers and reviewing/accepting/editing results. CSV export not yet implemented.

---

## Known issues / future work

- **Semantic Scholar rate limits**: S2 enforces 1 req/s regardless of whether an API key is used. Without a key the quota is shared globally across all users from the same IP, so on shared/university networks 429 errors are common. An API key (`s2_api_key` setting) authenticates requests (your own dedicated quota at 1 req/s) — apply at https://www.semanticscholar.org/product/api. The client throttles to 1 req/s in both cases (`_MIN_INTERVAL = 1.0`). The search-by-title endpoint returns HTTP 429 with a user-readable message when S2 rate-limits us.
- **S2 missing reference lists**: S2 can return forward citations (papers citing a given work) even when it has no reference list for that work. This happens when S2 doesn't have full-text access to the paper. The "Fetch references (S2)" button now shows "S2 has no reference list for this paper" in this case instead of a misleading "0 references" count.
- **S2 rate-limit backoff**: When a 429 occurs, S2 imposes a backoff of up to 1+ hour from the same IP. The only reliable mitigation is an API key.
- **Topic list toggle: no explicit "off" badge**: inactive legend items dim to 40% opacity but there is no explicit strikethrough or badge. If a clearer visual indicator is desired, the legend rendering in `CitationTimeline.tsx` (the legend loop, currently around lines 510–580) is the right place to add it.
- **LLM Phase 2 remaining work**: structured extraction run UI is complete. Remaining: per-paper chat (store conversation turns as WorkNotes) and CSV export (`GET /api/extraction/schemas/{id}/export`). Design constraints: stateless backend (full history sent per request), PDF vision Anthropic-only, local server auth (most local servers accept `Bearer local` from the openai SDK).
- **LLM local server auth**: `list_models()` now correctly omits the `Authorization` header when `api_key` is empty for local servers (was sending `Bearer local`, causing 401 on servers that validate auth). The `chat()` endpoint still uses the openai SDK which sends `Bearer local`; most local servers (Ollama, LM Studio) accept this, but servers with strict auth validation may reject it.

---

## Running tests

```bash
python -m pytest tests/ -v          # all tests (256 tests)
cd frontend && npm run build        # TypeScript type check + production build (requires Node.js)
```

All tests should pass. There are no known pre-existing failures.
