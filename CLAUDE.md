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
- **Work notes**: Per-work notes stored in the `WorkNote` table. Notes can be scoped to a project (`project_id` set) or general (`project_id` null). Each note has `content`, `note_type` (optional label), `provenance` ("user", "ai", "ai_reviewed", or "ai_proposal"), `model_id`, and `is_outdated` flag. Editing an AI-generated note changes provenance to "ai_reviewed". The `"ai_proposal"` provenance is used for LLM suggestions on cells that already have user/ai_reviewed values — these are shown as pending proposals the user can accept, edit, or dismiss.
- **Secondary DOIs (WorkDOI)**: A work may have multiple valid DOIs. The primary DOI lives on `Work.doi`; additional DOIs are stored in the `WorkDOI` table (`work_dois`, CASCADE delete). `doi_aliases: list[str]` is included in `WorkOut` via a Pydantic `field_validator(mode='before')`.
- The library stores a **venue tier list**: a user-maintained mapping of venues to tiers (1 = top venue, 2 = regular, 3 = ignore). Tiers are global per venue (not per-field). Venues can be associated with one or more research fields (e.g., "AI/ML", "Computer Networks") via a many-to-many relationship, but the tier is a single global value.
- **Venue aliases** handle year-to-year name variation (e.g., the same conference has different OpenAlex names across years). Aliases are manually reorderable; the first alias (by sort_order) is the **preferred alias** used for display throughout the UI.
- **Venue normalization** runs at startup: strips "Proceedings of the..." prefixes, calendar years, and ordinal edition numbers. Detects and merges duplicate venues after normalization, preserving old names as aliases.
- The library is the single source of truth for all paper metadata.
- **Library sanitization** tools: duplicate detection (by DOI, bibtex_key, or title+year), work merge (repoints citations, topic list memberships, authors, locations), and bulk deletion.
- **Extraction schemas**: User-defined structured extraction tables for LLM-assisted literature review. Each `ExtractionSchema` (title, optional description, optional `project_id`) contains ordered `ExtractionColumn` records (name, LLM prompt, optional description, optional `allowed_values` list, `sort_order`). Results are stored as `WorkNote` rows (two per column: answer + reasoning, both `provenance="ai"`). A `null project_id` means the schema is global (not project-specific). Defined in `models/extraction.py`, migrated in `_migrate_schema()`.
  - **`ai_proposal` provenance (opt-in)**: When "Re-evaluate edited cells" is checked in the Extract & Review action bar, re-running extraction also processes cells whose provenance is `"user"` or `"ai_reviewed"`. Instead of overwriting those cells, the LLM result is stored as a parallel `WorkNote` with `provenance="ai_proposal"`. The UI shows a proposal badge on affected cells; the user can Accept (overwrites the existing note, sets provenance to `"ai_reviewed"`), Edit (opens editor pre-filled with proposal), or Dismiss (deletes the proposal note). Stale `"ai_proposal"` notes are deleted before re-creating on each extraction run, just like `"ai"` notes.
  - **Manual cell fill**: Each unfilled cell shows two icons — a sparkle/wand icon (triggers LLM extraction for the entire row, existing behavior) and a pencil icon (opens an inline editor for that single cell: dropdown for constrained columns, text input for free-form). Manual fills set `provenance="user"`.

### 2. Project layer (per project)
- A project contains one or more **topic lists**. Each topic list is a named, color-coded set of "selected" papers (seeds). These represent sub-fields or themes the researcher is investigating.
- The project stores which papers from the library belong to which topic list.
- The project stores **ignored works**: papers explicitly marked as uninteresting, excluded from the timeline.
- Multiple projects can coexist and share the same library.
- **Extraction table tabs**: Extraction schemas associated with a project can be surfaced as tabs on the project detail page. A permanent "Tables" tab (next to Timeline, Topic Lists, Notes) lists all schemas for the project and allows opening any table. Additionally, individual schemas can be "promoted" to their own top-level tab (toggle in the Tables tab, off by default) — promoted tabs show the table name and render the Extract & Review view directly. Promotion state is stored in `localStorage` per project (`litexplorer:project:{id}:promotedSchemas`), not in the database — it is a per-browser preference. Promoted tabs are read-only with respect to paper selection; changing which papers are included in the extraction requires navigating to the full schema editor (a link is provided).

---

## External data sources

Four data sources are actively integrated:

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

- **Seed papers** (papers in any topic list): filled **squares**, color-coded by topic list. Multi-topic-list papers use vertical color stripes.
- **Backward neighbors** (references of seeds): **circles** in muted gray
- **Forward neighbors** (papers citing seeds): **diamonds** in muted gray

Dot size scales by `sqrt(connectivity)` where connectivity = 1 + number of seed connections. Within each year column, dots are jittered horizontally using a deterministic Knuth hash to avoid overlap while maintaining stable positions.

### Y-axis: Citation count with sliding window
Citation count on a `log(1+x)` scale. A "Count citations" slider controls the time window: **All** (all-time `citation_count`) or **Of last Ny** (1–10 years, sums `citations_by_year`). Computed client-side via `computeCitationCount()` — no re-fetch on slider change. Works without `citations_by_year` fall back to all-time count. Ticks at powers of 10; label: "Citations".

### Paper inclusion logic
- Papers from tier-3 (ignored) venues are always excluded
- **Candidate filter** dropdown: All (show all candidates), Top venues (tier-1 only), None (seeds only)
- No score threshold — all candidates passing the venue/direction filters are shown

### Interaction
- **Click a dot**: side panel with metadata, abstract, venue, citation count, connections. Overlapping dots cycle on repeated click.
- **K-hop connections**: segmented control (1–3). Direct edges = solid indigo; farther = dashed; intermediate nodes get amber outline.
- **Add/remove from topic list**: side panel buttons; adding triggers auto-enrichment.
- **Mark uninteresting**: for neighbor papers, moves them to the project's ignored list.
- **Citation list markers**: SVG markers match timeline shapes (colored squares for seeds, grey circles for backward refs, grey diamonds for forward cites). Visible-in-timeline entries are clickable; clicking navigates to that paper. Selection change fires an indigo ripple (D3, 650 ms, via `prevSelectedWorkIdRef`).
- **Collapsible sections**: Abstract, Locations, Actions, References, Cited-by. Fold state persists across paper selections within the page.

### Legend
The legend is rendered as an **HTML `div` with `flex-wrap`** positioned above the D3 SVG chart (not inside the SVG). It always renders from the full list of project topic lists, regardless of which are currently active. This means the legend remains visible even when all topic lists are toggled off, allowing the user to reactivate them. Inactive items are dimmed to 40% opacity. The flex-wrap layout ensures legend items flow onto multiple lines when the available width (chart container width, respecting the side panel) is insufficient for a single row.

### Timeline controls
Citation window slider · References/Cited-by checkboxes · Candidates dropdown · Hops (1–3) · Year range slider · "Showing N of M candidates" stat

### Per-client state persistence
Timeline settings stored in `localStorage` per project (`litexplorer:project:{id}:view`). Sidebar "Projects" link remembers last `/projects/*` path via useRef (session-only).

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
│   ├── chat.py           # ChatSession (work_id, project_id, title, is_auto), ChatMessage (session_id, role, content)
│   ├── cache.py          # ApiCache (permanent / timestamped)
│   └── settings.py       # Setting (key-value store)
├── schemas/              # Pydantic v2 request/response models
│   ├── works.py          # WorkOut (with semantic_scholar_id), WorkDetail, WorkCreate, BibtexImportResult,
│   │                     #   WorkPDFOut (with extraction_status: Literal[ready/failed/pending]), etc.
│   ├── venues.py         # VenueOut, VenueDetail, VenueAliasOut, etc.
│   ├── projects.py       # ProjectOut, ProjectDetail, TopicListOut, etc.
│   ├── enrichment.py     # EnrichDOIResult, CitationResult (+ raw_count), DOIResolutionResult,
│   │                     #   SemanticScholarEnrichResult, SearchImportRequest,
│   │                     #   SearchImportCandidate, SearchImportCandidatesResult,
│   │                     #   SearchImportConfirmRequest, DOIInfoResult
│   ├── timeline.py       # TimelineResponse, TimelineSeedWork (+ backward_citations_no_oa_data, has_pdfs),
│   │                     #   TimelineNeighborWork (seeds/neighbors include citations_by_year: list[dict] | None)
│   ├── fields.py         # FieldOut (includes venue_count: int)
│   ├── notes.py          # WorkNoteCreate, WorkNoteUpdate (+ provenance field), WorkNoteOut, ProjectNoteOut
│   │                     #   provenance values: "user", "ai", "ai_reviewed", "ai_proposal"
│   ├── extraction.py     # ExtractionSchemaCreate/Update/Out, ExtractionColumnCreate/Update/Out,
│   │                     #   ColumnReorderRequest, ExtractionColumnResult, ExtractionWorkResult (+ parsing_method: str),
│   │                     #   ExtractionBatchRequest (+ re_evaluate_edited: bool = False), ExtractionBatchResult,
│   │                     #   ExtractionCellResult (+ proposal: optional ai_proposal note), ExtractionResultsResponse
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
│   ├── llm.py            # /api/llm — model listing, POST /chat (session_id auto-saves turns)
│   ├── chat.py           # /api/chat — session CRUD: POST /sessions/auto, GET/DELETE /sessions/{id},
│   │                     #   POST /sessions/{id}/save, DELETE /sessions/{id}/messages, GET /sessions,
│   │                     #   PATCH /sessions/{id} (update context_id)
│   ├── extraction.py     # /api/extraction — schema/column CRUD, extraction execution,
│   │                     #   GET /schemas/{id}/results?work_ids=... (ExtractionResultsResponse)
│   │                     #   GET /schemas/{id}/export?format=csv|latex (CSV/LaTeX download)
│   │                     #   GET /schemas/{id}/preview-prompt?work_id=... → {system_text, user_message}
│   │                     #   GET /schemas/{id}/summary, POST /schemas/from-discussion,
│   │                     #   POST /schemas/{id}/columns/from-proposal
│   └── grobid.py         # /api/enrich/works/{id}/grobid, GET /api/grobid/status,
│                         #   POST /api/grobid/start (docker start grobid, subprocess)
├── services/
│   ├── enrichment.py     # EnrichmentService — import, citation fetching, venue normalization,
│   │                     #   DOI resolution, cache management, deduplication,
│   │                     #   _refresh_work_metadata(), import_by_semantic_scholar_id(),
│   │                     #   search_import_candidates() (Crossref-first with S2 fallback),
│   │                     #   enrich_from_semantic_scholar();
│   │                     #   fetch_backward_citations() returns tuple[list[Work], int] where
│   │                     #   int is raw_count (0 = OA has no reference list for this paper)
│   ├── pdf.py            # extract_pdf_text(), _detect_two_column(), _words_to_text()
│   │                     #   ExtractionError; two-column detection via x0 histogram heuristics
│   ├── extraction.py     # assemble_extraction_prompt(provider, model_id), parse_extraction_response() → tuple[dict, str],
│   │                     #   run_extraction_for_work() → tuple[list[dict], str] — builds LLM prompt (with JSON example,
│   │                     #   negative instructions, FORMAT CRITICAL for local models), parses response via 5 strategies,
│   │                     #   creates WorkNote rows; on failed parse creates single _parse_error note instead;
│   │                     #   skips ai_reviewed/user notes by default; when re_evaluate_edited=True, still runs LLM
│   │                     #   for those cells but writes results as provenance="ai_proposal" instead of overwriting;
│   │                     #   deletes stale ai AND ai_proposal notes before re-creating
│   ├── extraction_export.py  # export_as_csv(), export_as_latex() — booktabs LaTeX with rotatebox
│   │                         #   headers for constrained columns; single-pass regex LaTeX escaping
│   └── schema_discussion.py  # build_schema_discussion_prompt() — schema-design system prompt with
│                             #   column-proposal fenced-block spec + two few-shot examples;
│                             #   parse_column_proposals() — 5-strategy lenient parser (mirrors proposalParser.ts)
└── external/
    ├── base.py           # ExternalWork (with semantic_scholar_id, citations_by_year),
    │                     #   ExternalVenue, ExternalAuthor, ExternalLocation
    ├── openalex.py       # OpenAlexClient — DOI lookup, batch fetch, forward citations
    │                     #   parse_work() extracts counts_by_year from OpenAlex responses
    ├── crossref.py       # CrossrefClient — DOI lookup, fuzzy search
    ├── semantic_scholar.py # SemanticScholarClient — paper lookup by DOI/S2 ID,
    │                     #   get_references(), get_citations(), search_by_title()
    ├── llm_client.py     # ContextDocument, abstract LLMClient, AnthropicLLMClient, OpenAILLMClient,
    │                     #   make_llm_client(); _normalize_base_url() appends /v1 to bare Ollama URLs;
    │                     #   PDF vision (base64 document block) is Anthropic-only
    ├── pdf_fetch.py      # fetch_pdf_from_arxiv(), fetch_pdf_url_from_unpaywall(),
    │                     #   fetch_pdf_from_url(), fetch_pdf_for_work(), PDFFetchError
    └── grobid.py         # GrobidClient — PDF reference extraction via GROBID REST API

frontend/src/
├── App.tsx               # Routes: /projects, /projects/:id, /library, /venues, /settings
├── api.ts                # All fetch functions (works, venues, projects, enrichment, PDFs, notes, fields, etc.)
├── types.ts              # TypeScript interfaces matching backend schemas
│                         #   (includes CitationsByYearEntry, WorkNote, ProjectNote, WorkPDFOut,
│                         #    ExtractionCellResult, ExtractionResultsResponse, etc.)
├── queryClient.ts        # TanStack React Query client configuration
├── lib/
│   └── timelineFilter.ts # computeCitationCount(), filterNeighbors()
├── utils/
│   └── proposalParser.ts # parseProposals(message) → MessageSegment[]; 5-strategy parser (fenced block →
│                         #   fenced JSON → bare JSON → markdown table → numbered/bulleted bold list)
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
│   │                     #   useDeleteWorkPDF(), useExtractWorkPDFText(), useFetchWorkPDF()
│   ├── useSettings.ts
│   ├── useChatSessions.ts # useGetOrCreateAutoSession(), useListChatSessions(), useGetChatSession(),
│   │                      #   useSaveChatSession(), useDeleteChatSession(), useClearChatMessages()
│   └── useExtraction.ts  # useExtractionSchemas(), useExtractionSchema(),
│                         #   useCreateExtractionSchema(), useUpdateExtractionSchema(),
│                         #   useDeleteExtractionSchema(), useCreateExtractionColumn(),
│                         #   useUpdateExtractionColumn(), useDeleteExtractionColumn(),
│                         #   useReorderExtractionColumns(), useExtractionResults(),
│                         #   useRunBatchExtraction(), useRunSingleExtraction(),
│                         #   useAcceptExtractionNote(), useEditExtractionNote()
├── pages/
│   ├── ProjectsPage.tsx         # Project listing with create/delete
│   ├── ProjectDetailPage.tsx    # Timeline + Topic Lists + Notes + Tables tabs, localStorage persistence
│   │                            #   Tables tab: lists project schemas, open any table, promote/demote toggle
│   │                            #   Promoted schemas get own top-level tabs (localStorage: promotedSchemas)
│   │                            #   Promoted tabs show ExtractionRunView read-only (no paper selection changes)
│   │                            #   "Import Paper" button → ImportDialog with post-import topic list assignment
│   ├── ExtractionSchemasPage.tsx # Schema list / new-schema form / schema editor (Schema + Extract & Review tabs)
│   │                            #   New-schema form: "Design with AI" button → navigates to project discussion
│   │                            #     with "New schema" mode pre-selected
│   │                            #   Edit-schema view: "Refine with AI" button → navigates to project discussion
│   │                            #     with this schema pre-selected in the dropdown
│   │                            #   ExtractionRunView (paper selector: TL color bars, bulk-select checkboxes,
│   │                            #   sort-selected-to-top), ExtractionCell, ProvenanceBadge, ColumnFormModal
│   │                            #   ExtractionCell: unfilled cells show two icons — sparkle (LLM extract row)
│   │                            #     and pencil (manual fill for this cell). Cells with ai_proposal show
│   │                            #     proposal badge; click opens Accept/Edit/Dismiss UI.
│   │                            #   Action bar: "Re-evaluate edited cells" checkbox (opt-in for ai_proposal);
│   │                            #   "Show prompt" button → modal with paper text replaced by placeholders
│   ├── DiscussionPage.tsx       # Per-paper + per-project LLM chat; session restore on mount;
│   │                            #   Save/Load/New Chat toolbar; unified left panel with "Discussion focus"
│   │                            #   dropdown (General / existing schema / New schema) above PaperContextSelector;
│   │                            #   AssistantMessage: parses LLM replies via proposalParser in schema mode;
│   │                            #   NewSchemaDialog: promise-based flow for "New schema" accepts;
│   │                            #   "Show prompt" button → client-side preview with text/PDF placeholders;
│   │                            #   selection lock after first send; localStorage persistence for selection+mode;
│   │                            #   "View schema" button (visible when schema exists in DB, i.e., at least one
│   │                            #     column accepted for new schemas, always for existing) → navigates to
│   │                            #     ExtractionSchemasPage with that schema selected
│   ├── LibraryPage.tsx          # Work listing with search, pagination, venue filter
│   ├── VenuesPage.tsx           # Venues tab (sortable table) + Fields tab (CRUD with delete)
│   └── SettingsPage.tsx         # Database-stored settings editor + PDF folder browser
└── components/
    ├── AppShell.tsx            # Layout: Sidebar + Outlet
    ├── Sidebar.tsx             # Nav: Projects (remembers last path), Library, Venues, Settings
    ├── CitationTimeline.tsx    # D3 scatter plot with log1p citation count y-axis
    ├── WorkDetailPanel.tsx     # Side panel with collapsible sections, markers, actions, notes
    ├── TimelineControls.tsx    # Filter bar: citation window, direction, candidates, hops, year range
    ├── TimelineEnrichBar.tsx   # Enrichment progress for seed papers; onSelectWork prop;
    │                           #   shows "Try GROBID" hints for seeds with no OA data + PDF + GROBID available
    │                           #   (reads ['grobid','status'] from query cache, no new fetch)
    ├── ImportDialog.tsx        # 3-tab import: DOI list, BibTeX, Search by title;
    │                           #   optional post-import topic list assignment (projectTopicLists prop)
    ├── SearchImportCandidateDialog.tsx # Radio-picker for search-by-title candidates (source badge)
    ├── SanitizeDialog.tsx      # Library cleanup tools
    ├── DOIResolutionDialog.tsx # DOI candidate selection
    ├── TopicListCard.tsx       # Expandable topic list with works
    ├── TopicListFormDialog.tsx # Create/edit topic list (name + color)
    ├── ProjectFormDialog.tsx   # Create/edit project
    ├── WorkCard.tsx            # Library list item
    ├── VenueTierEditor.tsx     # Inline tier/field assignment
    ├── ColumnProposalCard.tsx  # Interactive card for LLM column proposals; state: pending/editing/saving/accepted/rejected;
    │                           #   onAccept: Promise<void> (async); UserCancelledError exported for silent cancel handling
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
├── test_extraction.py         # Schema/column CRUD, prompt assembly, response parsing, extract endpoints,
│                              #   ai_proposal provenance (re_evaluate_edited flag), manual cell fill (34+ tests)
├── test_extraction_export.py  # CSV/LaTeX export service unit tests + API endpoint tests (28 tests)
├── test_pdf_fetch.py          # OA PDF fetch: arXiv, Unpaywall, fetch_pdf_for_work, API endpoint (23 tests)
├── test_chat_sessions.py      # Chat session CRUD, auto-session uniqueness, save/load/clear, chat auto-persist (18 tests)
├── test_schema_discussion.py  # Schema discussion prompt, parse_column_proposals, endpoints (22 tests)
├── test_grobid_client.py      # GrobidClient unit tests: parse TEI XML, health check, error handling (23 tests)
├── test_grobid_enrichment.py  # GROBID enrichment endpoint + status endpoint (10 tests)
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
2. `_migrate_schema()` — add new columns/tables (doi_auto_resolved, sort_order, work_pdfs, citations_by_year, drop pdf_path, work_notes, sqlite_sequence tracking, extraction_status on work_pdfs, semantic_scholar_id on works, work_dois, extraction_schemas, extraction_columns, chat_sessions, chat_messages)
3. `_seed_default_fields()` — create "AI/ML" and "Computer Networks" fields
4. `_seed_default_settings()` — create `api_contact_email`, `pdf_storage_path`, `ssl_verify`, `s2_api_key`, `llm_provider`, `llm_api_key`, `llm_model_id`, `llm_base_url`, `llm_system_prompt_prefix`, `grobid_url` settings
5. `_normalize_existing_venue_names()` — strip prefixes, merge duplicate venues
6. `_backfill_citations_by_year()` — populate `citations_by_year` from cached OpenAlex responses (scans `work:doi:*`, `backward_citations:*`, and `forward_citations:*` cache entries)

---

## Multi-user model

The deployment context is a small team of trusted collaborators on a shared local server:
- No login or authentication
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
- **LLM calls must be async**: use FastAPI `BackgroundTasks` or streaming responses. A single extraction pass over many papers can take minutes. Do NOT call LLM APIs synchronously in the request handler.
- **PDF vision is Anthropic-only**: sending the PDF binary directly to the model is only supported when `llm_provider = "anthropic"`. All other providers (including local OpenAI-compatible servers) must use extracted `.txt` text.
- **Table isolation in tests**: `Base.metadata.create_all()` in `conftest.py` runs before `from litexplorer.app import app`. A test file that uses a model via the API but does NOT import that model at the top of the file will get "no such table" when run in isolation. Fix: add a bare import at the top (e.g. `from litexplorer.models.chat import ChatSession`) so it registers in `Base.metadata` before `create_all()`.

---

## Known issues / future work

- **Semantic Scholar rate limits**: S2 enforces 1 req/s regardless of whether an API key is used. Without a key the quota is shared globally across all users from the same IP, so on shared/university networks 429 errors are common. An API key (`s2_api_key` setting) authenticates requests (your own dedicated quota at 1 req/s) — apply at https://www.semanticscholar.org/product/api. The client throttles to 1 req/s in both cases (`_MIN_INTERVAL = 1.0`). The search-by-title endpoint returns HTTP 429 with a user-readable message when S2 rate-limits us.
- **S2 missing reference lists**: S2 can return forward citations (papers citing a given work) even when it has no reference list for that work. This happens when S2 doesn't have full-text access to the paper. The "Fetch references (S2)" button now shows "S2 has no reference list for this paper" in this case instead of a misleading "0 references" count.
- **S2 rate-limit backoff**: When a 429 occurs, S2 imposes a backoff of up to 1+ hour from the same IP. The only reliable mitigation is an API key.
- **Topic list legend**: The legend is rendered as an HTML flex-wrap div above the SVG, sourced from the full topic list array (not from visible data). It remains visible when all lists are toggled off. Inactive items dim to 40% opacity. This replaced an earlier D3-rendered legend that disappeared when all traces were deactivated.
- **Unpaywall requires contact email**: OA PDF fetch via Unpaywall is only attempted when `api_contact_email` is configured in Settings. Without it, only arXiv is tried (works that have `arxiv_id`). Works that have only a DOI but no `arxiv_id` and no configured email will return 404 from the fetch endpoint.
- **LLM local server auth**: `list_models()` now correctly omits the `Authorization` header when `api_key` is empty for local servers (was sending `Bearer local`, causing 401 on servers that validate auth). The `chat()` endpoint still uses the openai SDK which sends `Bearer local`; most local servers (Ollama, LM Studio) accept this, but servers with strict auth validation may reject it.
- **Column-proposal re-prompting (not implemented)**: when all five `parseProposals()` strategies fail and the LLM response contains no parseable proposal, the UI silently shows the message as plain text. A possible future enhancement is to detect this case client-side and automatically re-prompt the LLM asking it to reformat its answer as a fenced `column-proposal` block.
- **GROBID reference extraction**: Requires Docker (`docker run -d --name grobid -p 8070:8070 grobid/grobid:0.8.2-crf`). Title-only resolution relies on first-author + year matching (no venue); some references will fail to resolve. CRF-only image is lightweight and fast; for better accuracy use the `-full` image (needs GPU). Settings page has a "Start" button (calls `POST /api/grobid/start`) that appears after a failed "Test connection" check; on success waits 5 s and auto-re-tests.

---

## Running tests

```bash
python -m pytest tests/ -v          # all tests
cd frontend && npm run build        # TypeScript type check + production build (requires Node.js)
```

All tests should pass. There are no known pre-existing failures.
