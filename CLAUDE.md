# LitExplorer — Project Specification

## What this project is

A local-first research literature dashboard for discovering and mapping the state of the art around a new research project. All data stays local. There is no cloud dependency except for API calls to literature databases (which are cached where appropriate).

---

## Architecture overview

Two distinct layers:

### 1. Library layer (shared across projects)
- A local BibTeX-based database, conceptually similar to JabRef
- Each work is uniquely identified by its **DOI** where available
- A work may have multiple "locations": the canonical venue version (conference/journal) and a preprint (e.g., arXiv). Both are stored, but the venue version is always treated as primary. The arXiv version is surfaced as a secondary link.
- PDFs can be attached to a work and are stored in a dedicated local folder (configurable path)
- The library also stores a **venue tier list**: a user-maintained list of top venues per field, used across all projects. Initially two fields: computer networks and AI/ML. This list maps venue names/identifiers to a tier (1 = top venue, 2 = known but not top, unrecognized = default). DBLP venue identifiers should be used for normalization where possible.
- The library is the single source of truth for all paper metadata.

### 2. Project layer (per project)
- A project contains one or more **topic lists**. Each topic list is a named, color-coded set of "selected" papers. These represent sub-fields or themes the researcher is investigating.
- The project stores which papers from the library belong to which topic list.
- The project stores LLM extraction results and custom question schemas (see Phase 2).
- Multiple projects can coexist and share the same library.

---

## External data sources

Three APIs are used, with clearly separated roles:

| Source | Role |
|---|---|
| **OpenAlex** | Primary source: citation graph, forward citations, paper metadata, venue info, concept/topic tags |
| **Crossref** | DOI resolution and authoritative venue metadata |
| **Semantic Scholar** | Optional secondary source for influence scores (used to enrich importance metric) |

### Caching policy
- **Backward citations** (references listed in a paper): static, cache permanently once fetched
- **Forward citations** (papers citing a given work): dynamic, cache with a timestamp and surface a "last updated" indicator + manual refresh button in the UI. Do not auto-refresh silently.
- **Paper metadata**: cache permanently, with a manual refresh option

---

## Core visualization: Citation Timeline

The main view of a project is a **timeline** (x-axis = publication year):

- **Seed papers** (papers in any topic list): shown as filled dots, color-coded by topic list. If a paper is in multiple topic lists, show it with a split color or a distinct "overlap" style.
- **Backward neighbors** (references of seed papers): shown as smaller dots in a muted version of the referencing paper's color
- **Forward neighbors** (papers citing seed papers): shown similarly

### Paper inclusion logic (two-tier system)
1. **Unconditional tier**: all papers from venues in the user's tier-1 venue list are always shown, regardless of citation count or recency. This ensures complete coverage of top-venue activity.
2. **Scored tier**: all other papers are ranked by an importance score and shown above a user-adjustable threshold. Score formula (tunable): `(citations_in_sliding_window / age_in_years) * recency_decay`, where recency_decay down-weights papers older than N years (default: 5). The sliding window and decay parameters are exposed as controls in the UI.

### Interaction
- **Click a dot**: show a side panel with paper metadata, abstract, venue, citation count, and its connections (which seed papers reference it or are referenced by it)
- **Add to topic list**: a button in the side panel lets the user add the paper to any topic list (and thus make it a new seed, expanding the graph)
- **Overlap highlighting**: selecting a paper highlights all its connections in the graph

---

## Phase 1 scope (build this first)

- Library layer: BibTeX storage, DOI keying, PDF attachment, venue tier list
- Project layer: topic lists, color coding
- OpenAlex + Crossref integration
- Citation timeline visualization with the two-tier inclusion logic
- Paper side panel with add-to-list action
- Import: accept a list of DOIs or a BibTeX file as starting input

## Phase 2 scope (build after Phase 1 is stable)

- LLM integration: user provides an API key for a provider (Anthropic, OpenAI, etc.)
  - a) Per-paper chat: discuss a paper to accelerate understanding
  - b) Structured extraction: user defines a custom schema of questions (e.g., "method used", "datasets used", "evaluation metric"). The LLM answers each question per paper, succinctly. Results are stored locally and can be exported as a table (CSV or similar). This is designed to support systematic literature review tables.

---

## Design principles

- **Local first**: no accounts, no cloud storage, no telemetry. API calls are the only outbound traffic.
- **Data ownership**: all stored data (BibTeX, PDFs, cache, project state) lives in a user-specified local directory
- **Resilience to API changes**: abstract all three API integrations behind a clean internal interface so the data source can be swapped or supplemented without touching the rest of the codebase
- **Incremental use**: the tool should be useful from the first paper added, not only after a large import

---

## Implementation notes and known pitfalls

- Venue normalization in CS is messy. OpenAlex venue names are inconsistent across years for the same conference. Build a small manual mapping table early and treat it as a first-class editable artifact.
- The same work may appear under different DOIs (rare but real) or with and without a DOI (arXiv-only papers). Handle the no-DOI case gracefully — use arXiv ID as fallback key.
- Forward citation queries can return hundreds of results for well-cited papers. Always apply the two-tier filter before rendering, not after.
- BibTeX entry keys should be human-readable (AuthorYearKeyword convention) but the internal unique key is always the DOI or arXiv ID.

---

## Stack

- **Backend**: Python + FastAPI
- **Database**: SQLite via SQLAlchemy ORM. The schema must be designed for multi-user from the start (every record scoped to a user or project), but **do not implement authentication in Phase 1** — assume a single trusted user. This makes adding auth later straightforward without schema migration pain.
- **Frontend**: React, served statically by the FastAPI backend (no separate frontend server)
- **Visualization**: D3.js for the citation timeline
- **Deployment target**: a Linux server on a local network, accessed by a small team of trusted collaborators via their browsers. No public internet exposure is assumed. No Electron, no desktop packaging.
- **Startup**: the app is launched via `uvicorn` (or a simple shell script wrapping it). A `systemd` unit file should be provided for running it as a persistent service on the Linux server.
- **Data directory**: all persistent data (SQLite DB, BibTeX files, PDF attachments, API cache) lives under a single configurable root directory, set via an environment variable or config file. This makes backup and migration trivial.
- **Dependency management**: conda environment for environment isolation, pyproject.toml for project dependencies. Do not generate requirements.txt. Install the project in editable mode with pip install -e . inside the conda environment. Provide a conda environment YAML (environment.yml) with just the Python version pinned; all Python package dependencies are declared in pyproject.toml

---

## Multi-user model (Phase 1 simplified)

The deployment context is a small team of trusted collaborators on a shared local server. In Phase 1:
- There is no login or authentication
- All users share the same **library layer** (papers, PDFs, venue tier list)
- Each **project** has an owner (stored in the DB) but is visible and editable by all users — trust is assumed
- Concurrent writes are handled by SQLite's WAL mode, which is sufficient for a small team

Authentication and per-user access control are explicitly deferred to a future phase and should not be designed in beyond having the owner field present in the schema.
