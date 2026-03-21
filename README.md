# SotAScope

A local-first research literature dashboard for mapping the state of the art around a research topic.

You build a library of papers (BibTeX import, DOI / arXiv ID lookup, or search-by-title), group them into topic lists, and SotAScope fetches the citation graph from OpenAlex, Crossref, and Semantic Scholar. The main view is a **citation timeline**: seeds (papers you selected) are squares, backward-citation neighbors are circles, forward-citation neighbors are diamonds, all plotted on a log-citation-count y-axis. A sliding window lets you count only citations from the last N years. Topic lists in the legend are clickable to toggle their visibility — hiding seeds, updating multi-topic-list color stripes, and removing candidates connected only to hidden lists.

Neighbor candidates go through a four-step client-side filter pipeline before being rendered: (1) direction toggles (show/hide backward or forward neighbors), (2) the "top venues" filter (tier-1 venues only), (3) active topic-list filter (removes candidates connected only to toggled-off lists), and (4) a **relevance-based visibility cap** of 3 000 dots. When the filtered set exceeds 3 000, only the top candidates by relevance score are shown; the control bar updates to `Showing top K of N candidates (by relevance)`, where K is the number of dots rendered and N is the total size of the candidate pool (union of all reference and cited-by lists, unchanged by filter toggles). The relevance score is `log(1 + citations) + max(0, (year − 2000) / 2)` — highly-cited and recent papers rank first.

The **side panel** for any selected paper shows paginated, relevance-sorted reference and citing-paper lists (50 per page, also sortable by year or citation count). When fewer than 80 % of a paper's citing papers are stored locally, a staleness line appears below the "Cited by" list (`N of ~M citing papers loaded · Refresh`) with a one-click refresh link.

The **enrichment info bar** at the top of the timeline gives a live overview of how many seed papers have been enriched per source. In collapsed mode it shows a single summary line (`⚠ 8/12 seeds have references · 10/12 have citing papers` or `✓ All N enriched`). Expand it to see one row per source (OpenAlex, Semantic Scholar, GROBID) with three-state counts (not fetched / fetched with data / fetched no data) and **bulk-fetch** buttons that start server-side jobs with live progress bars and a Cancel option. The S2 row shows an estimated time (`~Ns`) and warns if S2 returns a 429 rate-limit response — wait ~1 hour or configure a personal API key to avoid this.

Other features: venue tier list (tier 1 = top venue, tier 3 = exclude from timeline) with optional per-project overrides (a project can set a local tier for any venue without affecting the global default), venue aliases for year-to-year name variation, multi-DOI support (a paper can carry several valid DOIs), PDF upload with auto text extraction, per-paper notes with AI/user provenance tracking, library sanitization tools (duplicate detection, work merge), SSL verification toggle for corporate proxy environments. Projects can be **merged** (topic lists, extraction schemas, ignored works, chat sessions, and venue tier overrides are all reconciled, with a conflict-resolution UI for ambiguous cases) and **exported/imported** as self-contained `.zip` archives (see [Import / Export](#import--export) below). **Structured extraction schemas** let you define tables of LLM-answerable questions (columns) and run them against any set of seed papers in a project — the **Extract & Review** tab shows a live results table where each cell displays the extracted answer with a provenance badge (AI/reviewed/user), an Accept button to mark AI answers as reviewed, and an inline editor (dropdown for constrained columns, text for free-form). Three workflows are available for filling a cell: **AI extraction** (runs the LLM against the paper's extracted text), **manual entry** (inline editor with a free-text field or constrained dropdown), and **paste from external LLM** (click the 📋 icon on any cell to open a dialog where you paste raw JSON returned by an external model — the dialog previews which columns will be filled, skipped, or not matched before you confirm). Re-running extraction skips already-reviewed cells and overwrites only AI-generated ones (including cells filled via the paste workflow). Results can be exported as **CSV** or **LaTeX** (booktabs table) from the Extract & Review action bar. The paper selector row above the results table shows topic-list color bars on each paper's title cell, per-topic-list bulk-select checkboxes (with indeterminate state when partially selected), and keeps selected papers sorted to the top. A **"Show prompt"** button in the Extract & Review action bar previews the full LLM prompt that will be sent — paper text is replaced by a `[Text of "title"]` placeholder so you can inspect the prompt structure without the full document content. **Per-paper and per-project chat** lets you discuss any paper (or a selection of project seed papers) with an LLM using extracted PDF text or raw PDF bytes (Anthropic only). The project discussion view has a unified left panel: a **"Discussion focus"** dropdown lets you switch between general paper discussion, designing a new extraction schema, or refining an existing one — the paper selector is always visible below it. In schema-design mode the LLM proposes extraction columns as interactive cards (Accept / Edit / Reject) directly in the chat thread; accepting a card adds the column to the schema immediately. The proposal parser tolerates alternative output formats (markdown tables, numbered/bulleted lists) from less-capable or open-weight models, so interactive cards appear regardless of how the model formatted its output. You can start a "New schema" discussion without creating anything first — the schema row is created only when you accept the first column. Once you send the first message the paper selection and schema choice are locked — use **New Chat** to start a fresh discussion. The paper selection is persisted in `localStorage` and restored automatically when you return to the discussion. Conversations persist across navigation — the last session is automatically restored on re-visit, and you can save named snapshots to load later. A **"Show prompt"** button next to the Send button previews the full prompt sent to the LLM — PDF bytes and extracted text are replaced by `[PDF of "title"]` / `[Text of "title"]` placeholders. LLM provider configuration (Anthropic, OpenAI, or any local OpenAI-compatible server such as Ollama — leave the API key blank for local servers) is available in Settings.

**Deep links & sharing**: the project view and extraction schema page encode their state (`?tab=`, `?work=`, `?schema=`) in the URL. A **Share** button copies the current URL to the clipboard so you can send a link that reopens the exact tab, selected paper, or schema.

External API calls (OpenAlex, Crossref) are cached locally. All data — SQLite database, PDFs, cache — lives under a single configurable directory.

---

## Stack

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy 2.0, SQLite (WAL mode)
- **Frontend**: React 18, TypeScript, Vite, TanStack React Query, D3.js
- **HTTP client**: httpx (OpenAlex, Crossref, Semantic Scholar)
- **LLM SDKs**: anthropic, openai (optional — only needed when LLM provider is configured)
- **BibTeX**: bibtexparser 1.4
- **PDF extraction**: pdfplumber

---

## Installation

The pre-built frontend (`frontend/dist/`) is committed to the repo, so **Node.js is not required** to run the application. Just install the Python package and start the server.

### Prerequisites

- conda (Miniconda or Anaconda) — for the Python environment

### Steps

```bash
# 1. Clone
git clone <repo-url>
cd SotAScope

# 2. Python environment (pins Python 3.11)
conda env create -f environment.yml          # creates env named 'sotascope'
# To use a different env name: conda env create -f environment.yml --name myenvname
conda activate sotascope
pip install -e .

# 3. Run
# Local-only (laptop/desktop — not accessible from other machines):
sotascope

# Shared server (accessible on the network):
sotascope --host 0.0.0.0

# Custom data directory:
sotascope --datadir /path/to/data
```

Open `http://localhost:8000` in your browser. The SQLite database and PDFs are created under `~/.sotascope/` by default. Override with `--datadir /path/to/data` (or set the `SOTASCOPE_DATA_DIR` environment variable).

No root or sudo rights are required.

---

### Server (shared team use)

Same install steps as above. For the server to keep running after you disconnect, run uvicorn inside a `tmux` or `screen` session:

```bash
tmux new -s sotascope
conda activate sotascope
sotascope --host 0.0.0.0
# Detach with Ctrl-b d
```

The app listens on `0.0.0.0:8000`. Put a reverse proxy (nginx, Caddy) in front if you want HTTPS or a custom port.

#### Optional: run as a systemd service

If you want the process to start automatically on boot and restart on failure, a systemd unit file is included. Edit `sotascope.service` and replace the two `/path/to/SotAScope` placeholders with the absolute path to your clone, then:

```bash
cp env.example env
# Edit env — set SOTASCOPE_BIN to the output of: conda run -n sotascope which sotascope
# Set any other options you need (data directory, proxy, contact email)

sudo cp sotascope.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sotascope
```

Check it started:

```bash
sudo systemctl status sotascope
journalctl -u sotascope -f
```

#### Updating

```bash
git pull
conda run -n sotascope pip install -e .
# Restart however you started it:
#   tmux: kill and relaunch sotascope
#   systemd: sudo systemctl restart sotascope
```

Schema migrations run automatically at startup.

---

## Configuration

Most configuration is done through the **Settings page** in the UI (`/settings`):

| Setting | Description |
|---|---|
| `api_contact_email` | E-mail sent to OpenAlex and Crossref for polite-pool access (better rate limits). Equivalent to the env vars below; the UI value takes precedence. |
| `pdf_storage_path` | Where PDFs are stored. Defaults to `{data_dir}/pdfs/`. On a server, point this to a persistent directory outside the repo. |
| `ssl_verify` | Set to `false` to disable SSL certificate verification for external API calls. Useful when behind a corporate proxy that uses a custom CA. Default: `true` (verification enabled). |
| `s2_api_key` | Semantic Scholar API key (optional). S2 enforces 1 req/s regardless, but without a key the quota is shared across all users on the same IP — 429 errors are common on shared/university networks. An API key gives you a dedicated quota. Apply at https://www.semanticscholar.org/product/api. SotAScope throttles to ~1.1 req/s (a small margin above the 1 req/s limit). |
| `llm_provider` | LLM provider: `anthropic` or `openai`. Leave blank to disable LLM features. |
| `llm_api_key` | API key for the selected provider. Optional when `llm_base_url` points to a local server. |
| `llm_model_id` | Model to use (e.g. `claude-sonnet-4-6`, `gpt-4o`). The Settings page loads available models from the provider API and shows a dropdown. |
| `llm_base_url` | Override the provider's default API endpoint. Use this to point to a local inference server (e.g. `http://localhost:11434/v1` for Ollama). If you omit the `/v1` suffix (e.g. enter `http://localhost:11434`), SotAScope appends it automatically. |
| `grobid_url` | GROBID service URL (e.g. `http://localhost:8070`). Empty = disabled. See [GROBID (optional)](#grobid-optional) below. |

### GROBID (optional)

GROBID extracts references directly from PDFs — useful for arXiv papers where OpenAlex has no reference data. Install via Docker:

```bash
docker run -d --name grobid -p 8070:8070 grobid/grobid:0.8.2-crf
```

Then set the GROBID URL in Settings to `http://localhost:8070`. A "Extract refs (GROBID)" button appears in the paper detail panel when a PDF is uploaded and GROBID is reachable. Each extracted reference is resolved via a 4-step chain: DOI lookup → arXiv ID lookup (OpenAlex then Semantic Scholar) → S2 title search with first-author/year verification → stored as an unresolved stub with title, authors, year, URL (from `<ptr target>`), and venue name (from `<monogr>`, when an `<analytic>` is present), displayed with an "unresolved" badge for manual resolution.

Environment variables (all prefixed `SOTASCOPE_`) can be set in a shell or in the `env` file (see `env.example`):

| Variable | Default | Description |
|---|---|---|
| `SOTASCOPE_DATA_DIR` | `~/.sotascope` | Root for the SQLite DB and default PDF storage |
| `SOTASCOPE_OPENALEX_API_KEY` | — | Polite-pool e-mail for OpenAlex |
| `SOTASCOPE_CROSSREF_MAILTO` | — | Polite-pool e-mail for Crossref |

---

## Import / Export

### BibTeX export

Works can be exported as BibTeX from the library (all works) or from within a project (a paper-selector dialog lets you pick by topic list or individually, matching the extraction table paper selector). Endpoint: `GET /api/works/export/bibtex?work_ids=...`.

### Project save (.zip)

A project — including topic lists, extraction schemas, extraction results, per-project venue tier overrides, chat sessions, and project-scoped work notes — can be saved as a `.zip` archive via the **Save project (.zip)** item in the Export dropdown. The manifest references works by DOI or arXiv ID (never by DB row ID), making archives portable across SotAScope instances. Only seed papers are included; candidates are rediscovered automatically on import via the normal enrichment pipeline.

Check **Include paper content (PDFs / extracted text)** in the save dialog to also bundle uploaded PDFs and their extracted `.txt` files into the archive under `files/{work_id}/`. This makes the archive larger but self-contained; the importer will restore the files and create `WorkPDF` rows automatically. The checkbox is unchecked by default.

### Project import (.zip)

Upload a previously exported `.zip` via `POST /api/projects/import`. Works are matched against the existing library by DOI or arXiv ID — exact matches are auto-linked with no user action; new works are created. If the project name already exists, you can merge into the existing project (triggering the project merge flow) or rename the incoming project. All imported seed works are automatically enriched (backward + forward citations + Crossref), exactly as when a paper is added to a topic list manually.

---

## Rebuilding the frontend

The pre-built frontend is sufficient for running the application. Only rebuild if you modify the frontend source code. Requires Node.js 18+.

```bash
cd frontend
npm install
npm run build   # outputs to frontend/dist/
cd ..
```

---

## Running tests

```bash
# Backend
python -m pytest tests/ -v

# Frontend — TypeScript type check + production build (requires Node.js)
cd frontend && npm run build
```

All tests use an in-memory SQLite database and mock external API calls; no network access or real API keys are needed.

To regenerate the synthetic PDF fixtures used by the extraction tests (requires `fpdf2` and `matplotlib`):

```bash
pip install -e ".[fixtures]"
python tests/fixtures/generate_fixtures.py
```

---

## Project structure

```
sotascope/          Python package (FastAPI app, models, API routes, services)
  app.py              Lifespan: migrations, backfills, startup normalization
  config.py           Pydantic settings (SOTASCOPE_ env prefix)
  models/             SQLAlchemy ORM models
  api/                FastAPI routers
  services/           Business logic (enrichment, PDF extraction)
  external/           OpenAlex, Crossref, and Semantic Scholar API clients
frontend/
  src/                React + TypeScript source
  dist/               Pre-built frontend — served by FastAPI (committed to repo)
tests/                pytest suite
env.example           Template for the env file (copy to env and fill in values)
sotascope.service   Optional systemd unit file for server deployment
```
