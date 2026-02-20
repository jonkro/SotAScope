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
- PDFs can be attached to a work via a configurable local folder path (schema supports it; upload UI is deferred to phase 2 of the project).
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

Two APIs are actively integrated:

| Source | Role |
|---|---|
| **OpenAlex** | Primary source: citation graph, forward citations, paper metadata, venue info |
| **Crossref** | DOI resolution (including fuzzy search), authoritative venue metadata (ISSN, publisher) |

Semantic Scholar is referenced in the schema (as a possible `source` value on Citation and ApiCache) but **has no client implementation yet**. It is reserved for future enrichment of influence scores.

### API authentication
Both clients support a "polite pool" email for better rate limits. This email is configurable via:
1. A **database setting** (`api_contact_email`) editable from the Settings page (preferred)
2. Environment variable fallback (`LITEXPLORER_OPENALEX_API_KEY`, `LITEXPLORER_CROSSREF_MAILTO`)
If no email is configured, the clients still work but with lower rate limits.

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

### DOI resolution
For works without a DOI (e.g., imported from BibTeX without one), the system can:
- **Auto-resolve**: Crossref fuzzy search with configurable thresholds (score >= 80, ratio to 2nd candidate >= 1.5). Auto-resolved DOIs are marked with `doi_auto_resolved = true`.
- **Manual confirmation**: If auto-resolution confidence is low, candidates are presented to the user for selection.
- Batch resolution is supported for multiple works at once.

---

## Core visualization: Citation Timeline

The main view of a project is a **timeline** (x-axis = publication year, y-axis = log-scaled importance score):

- **Seed papers** (papers in any topic list): shown as filled **squares**, color-coded by topic list. If a paper is in multiple topic lists, vertical color stripes are used.
- **Backward neighbors** (references of seed papers): shown as **circles** in muted gray
- **Forward neighbors** (papers citing seed papers): shown as **diamonds** (rotated squares) in muted gray

Dot size scales by `sqrt(connectivity)` where connectivity = 1 + number of seed connections. Within each year column, dots are jittered horizontally using a deterministic hash (Knuth multiplicative) to avoid overlap while maintaining stable positions.

### Paper inclusion logic (two-tier system)
1. **Unconditional tier**: papers from tier-1 venues are always included, regardless of score
2. **Ignored tier**: papers from tier-3 (ignored) venues are always excluded
3. **Scored tier**: all other papers are ranked by importance score and shown above a user-adjustable threshold

**Importance score formula**: `(citation_count / age) * decay`
- `age = max(currentYear - publicationYear, 1)`
- `decay = age > decayStartYears ? decayStartYears / age : 1.0`
- `decayStartYears` is user-adjustable (default: 5 years)

Note: the score uses total citation count, not a sliding-window count. The threshold and decay parameters are exposed as controls in the UI.

### Candidate filter
A dropdown control allows filtering visible candidates:
- **All**: show all candidates passing the score threshold
- **Top venues**: show only candidates from tier-1 venues
- **None**: hide all candidates, show only seeds

### Interaction
- **Click a dot**: show a side panel with paper metadata, abstract, venue, citation count, and its connections. When dots overlap, clicking cycles through them.
- **K-hop connections**: a segmented control (1, 2, 3) visualizes multi-hop graph connectivity from the selected paper. Direct edges are solid indigo lines; farther edges are dashed. Intermediate pathway nodes are highlighted with an amber outline.
- **Add to topic list**: buttons in the side panel let the user add the paper to any topic list (making it a new seed and triggering auto-enrichment)
- **Remove from topic list**: buttons to remove the paper from topic lists it belongs to
- **Mark uninteresting**: for neighbor papers, moves them to the project's ignored list
- **Citation list markers**: References and Cited-by lists in the side panel show SVG markers matching the timeline shapes (colored squares for seeds, grey circles for backward refs, grey diamonds for forward cites). Entries are clickable to navigate to that paper in the timeline.
- **Collapsible sections**: Abstract, Locations, Actions, References, and Cited-by sections are collapsible. Fold state persists across paper selections within the same page.

---

## Phase 1 scope (implemented)

- Library layer: BibTeX import, DOI/arXiv keying, venue tier list, venue aliases with preferred alias
- Venue normalization and deduplication at startup
- Library sanitization: duplicate detection, work merge, deletion
- Project layer: topic lists with color coding, ignored works
- OpenAlex + Crossref integration with caching
- DOI auto-resolution via Crossref fuzzy search
- Auto-enrichment on seed addition
- Citation timeline visualization with two-tier inclusion logic
- Paper side panel with add/remove from topic list, mark uninteresting, citation browsing
- K-hop connection visualization (1-3 hops)
- Import: BibTeX file or list of DOIs
- Settings page for API contact email (stored in database)
- Venue management UI: alias editing, reordering, tier assignment, field association

### Not yet implemented from Phase 1 spec
- PDF upload UI (schema supports `pdf_path` but no upload mechanism)
- systemd unit file for deployment
- Semantic Scholar integration

## Phase 2 scope (not started)

- LLM integration: user provides an API key for a provider (Anthropic, OpenAI, etc.)
  - a) Per-paper chat: discuss a paper to accelerate understanding
  - b) Structured extraction: user defines a custom schema of questions (e.g., "method used", "datasets used", "evaluation metric"). The LLM answers each question per paper, succinctly. Results are stored locally and can be exported as a table (CSV or similar). This is designed to support systematic literature review tables.

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
- **HTTP client**: httpx (for OpenAlex and Crossref API calls)
- **BibTeX parsing**: bibtexparser 1.4
- **Deployment target**: a Linux server on a local network, accessed by a small team of trusted collaborators via their browsers. No public internet exposure is assumed.
- **Startup**: the app is launched via `uvicorn`. The FastAPI app serves the built frontend as static files (SPA fallback for client-side routing).
- **Data directory**: all persistent data (SQLite DB, PDFs, API cache) lives under a single configurable root directory (`~/.litexplorer` by default), set via the `LITEXPLORER_DATA_DIR` environment variable.
- **Dependency management**: conda environment for Python version isolation (`environment.yml` pins Python 3.11), `pyproject.toml` for all Python package dependencies. Install with `pip install -e .` in the conda environment.

---

## Project structure

```
litexplorer/
├── app.py                # FastAPI app with lifespan (startup migrations, seeding, SPA mount)
├── config.py             # Settings via Pydantic BaseSettings (LITEXPLORER_ env prefix)
├── database.py           # Engine, SessionLocal, init_db()
├── models/
│   ├── base.py           # SQLAlchemy DeclarativeBase
│   ├── library.py        # Work, WorkLocation, Author, WorkAuthor, Venue, VenueAlias,
│   │                     #   Field, VenueField, Citation
│   ├── project.py        # Project, TopicList, TopicListWork, ProjectIgnoredWork
│   ├── cache.py          # ApiCache (permanent / timestamped)
│   └── settings.py       # Setting (key-value store)
├── schemas/              # Pydantic v2 request/response models
│   ├── works.py          # WorkOut, WorkDetail, WorkCreate, BibtexImportResult, etc.
│   ├── venues.py         # VenueOut, VenueDetail, VenueAliasOut, etc.
│   ├── projects.py       # ProjectOut, ProjectDetail, TopicListOut, etc.
│   ├── enrichment.py     # EnrichDOIResult, CitationResult, DOIResolutionResult, etc.
│   ├── timeline.py       # TimelineResponse, TimelineSeedWork, TimelineNeighborWork
│   ├── fields.py         # FieldOut
│   └── settings.py       # SettingOut, SettingUpdate
├── api/
│   ├── deps.py           # get_db dependency
│   ├── works.py          # /api/works — CRUD, BibTeX import, citations, merge, duplicates
│   ├── venues.py         # /api/venues — CRUD, aliases, field associations
│   ├── fields.py         # /api/fields — CRUD
│   ├── projects.py       # /api/projects — CRUD, topic lists, ignored works
│   ├── enrichment.py     # /api/enrich — DOI import, citation fetching, Crossref, DOI resolution
│   ├── timeline.py       # /api/projects/{id}/timeline — timeline data aggregation
│   └── settings.py       # /api/settings — key-value settings CRUD
├── services/
│   └── enrichment.py     # EnrichmentService — import, citation fetching, venue normalization,
│                         #   DOI resolution, cache management, deduplication
└── external/
    ├── base.py           # ExternalWork, ExternalVenue, ExternalAuthor, ExternalLocation
    ├── openalex.py       # OpenAlexClient — DOI lookup, batch fetch, forward citations
    └── crossref.py       # CrossrefClient — DOI lookup, fuzzy search

frontend/src/
├── App.tsx               # Routes: /projects, /projects/:id, /library, /venues, /settings
├── api.ts                # All fetch functions (works, venues, projects, enrichment, etc.)
├── types.ts              # TypeScript interfaces matching backend schemas
├── queryClient.ts        # TanStack React Query client configuration
├── lib/
│   └── timelineFilter.ts # computeImportanceScore(), filterNeighbors()
├── hooks/                # React Query hooks for each domain
│   ├── useWorks.ts
│   ├── useVenues.ts
│   ├── useProjects.ts
│   ├── useTimeline.ts
│   ├── useEnrichment.ts
│   ├── useFields.ts
│   ├── useVenueTiers.ts
│   └── useSettings.ts
├── pages/
│   ├── ProjectsPage.tsx       # Project listing with create/delete
│   ├── ProjectDetailPage.tsx  # Timeline + Topic Lists tabs, all timeline state management
│   ├── LibraryPage.tsx        # Work listing with search, pagination, venue filter
│   ├── VenuesPage.tsx         # Venue management: aliases, tiers, fields
│   └── SettingsPage.tsx       # Database-stored settings editor
└── components/
    ├── AppShell.tsx            # Layout: Sidebar + Outlet
    ├── Sidebar.tsx             # Nav: Projects, Library, Venues, Settings
    ├── CitationTimeline.tsx    # D3 scatter plot with full interaction model
    ├── WorkDetailPanel.tsx     # Side panel with collapsible sections, markers, actions
    ├── TimelineControls.tsx    # Filter bar: threshold, decay, direction, candidates, hops, year
    ├── TimelineEnrichBar.tsx   # Enrichment progress for seed papers
    ├── ImportDialog.tsx        # DOI list or BibTeX import with resolution
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
├── test_library_api.py        # Work CRUD, BibTeX import, citations, merge
├── test_enrichment_api.py     # Enrichment endpoints with mocked clients
├── test_enrichment_service.py # EnrichmentService unit tests
├── test_project_api.py        # Project/topic list CRUD
├── test_timeline_api.py       # Timeline endpoint
├── test_venue_api.py          # Venue CRUD, aliases, tiers
├── test_openalex_client.py    # OpenAlex client (requires network)
└── fixtures/
    └── openalex_responses.py  # Sample API response fixtures
```

---

## Multi-user model (Phase 1 simplified)

The deployment context is a small team of trusted collaborators on a shared local server. In Phase 1:
- There is no login or authentication
- All users share the same **library layer** (papers, PDFs, venue tier list)
- Each **project** has an owner (stored in the DB) but is visible and editable by all users — trust is assumed
- Concurrent writes are handled by SQLite's WAL mode, which is sufficient for a small team

Authentication and per-user access control are explicitly deferred to a future phase.

---

## Implementation notes and known pitfalls

- Venue normalization in CS is messy. OpenAlex venue names are inconsistent across years for the same conference. The VenueAlias table handles this, with automatic normalization at startup and manual curation in the Venues UI.
- The same work may appear under different DOIs (rare but real) or without a DOI (arXiv-only papers). The dedup logic checks DOI first, then arXiv ID, then OpenAlex ID.
- Forward citation queries can return hundreds of results for well-cited papers. The two-tier filter is applied before rendering, not after.
- BibTeX entry keys follow AuthorYearKeyword convention but the internal unique key is always DOI or arXiv ID.
- When transferring a UNIQUE field value between rows (e.g., during work merge), null out the field on the source row and `db.flush()` before setting it on the target — SQLAlchemy batches UPDATEs within a single flush with no guaranteed row ordering.
- TestClient + in-memory SQLite requires `StaticPool` + `check_same_thread=False`. Override `get_db` from `litexplorer.api.deps` (not `litexplorer.database`).
