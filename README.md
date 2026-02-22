# LitExplorer

A local-first research literature dashboard for mapping the state of the art around a research topic.

You build a library of papers (BibTeX import or DOI lookup), group them into topic lists, and LitExplorer fetches the citation graph from OpenAlex and Crossref. The main view is a **citation timeline**: seeds (papers you selected) are squares, backward-citation neighbors are circles, forward-citation neighbors are diamonds, all plotted on a log-citation-count y-axis. A sliding window lets you count only citations from the last N years.

Other features: venue tier list (tier 1 = top venue, tier 3 = exclude from timeline), venue aliases for year-to-year name variation, PDF upload with auto text extraction, per-paper notes with AI/user provenance tracking, library sanitization tools (duplicate detection, work merge).

External API calls (OpenAlex, Crossref) are cached locally. All data — SQLite database, PDFs, cache — lives under a single configurable directory.

---

## Stack

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy 2.0, SQLite (WAL mode)
- **Frontend**: React 18, TypeScript, Vite, TanStack React Query, D3.js
- **HTTP client**: httpx (OpenAlex + Crossref)
- **BibTeX**: bibtexparser 1.4
- **PDF extraction**: pdfplumber

---

## Installation

### Laptop / desktop (local use)

**Prerequisites**: Python 3.11+, Node.js 18+, conda (recommended for Python version pinning).

```bash
# 1. Clone
git clone <repo-url>
cd LitExplorer

# 2. Python environment
conda env create -f environment.yml   # creates the 'litexplorer' env
conda activate litexplorer
pip install -e .

# 3. Frontend
cd frontend
npm install
npm run build          # outputs to frontend/dist — served by FastAPI
cd ..

# 4. Run
uvicorn litexplorer.app:app --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000` in your browser. The SQLite database and PDFs are created under `~/.litexplorer/` by default. Set `LITEXPLORER_DATA_DIR` to use a different location.

---

### Server (shared team use)

**Prerequisites on the server**: conda (Miniconda or Anaconda), Node.js 18+.

#### 1. Clone and build

```bash
git clone <repo-url>
cd LitExplorer

# Create the conda environment (pins Python 3.11)
conda env create -f environment.yml
conda activate litexplorer
pip install -e .

# Frontend
cd frontend && npm install && npm run build && cd ..
```

#### 2. Create the environment file

```bash
cp env.example env
# Edit env and fill in at minimum UVICORN_BIN:
#   conda run -n litexplorer which uvicorn
```

See `env.example` for all available options (data directory, contact email, proxy).

#### 3. Start the server

**Option A — tmux/screen** (simplest, no root required):

```bash
conda activate litexplorer
uvicorn litexplorer.app:app --host 0.0.0.0 --port 8000
```

Run this inside a `tmux` or `screen` session so it keeps running after you disconnect.

**Option B — systemd** (survives reboots, restarts on failure):

Edit `litexplorer.service` and replace the two `/path/to/LitExplorer` placeholders
with the absolute path to your clone. Then:

```bash
sudo cp litexplorer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now litexplorer
```

Check it started:

```bash
sudo systemctl status litexplorer
journalctl -u litexplorer -f
```

The app listens on `0.0.0.0:8000`. Put a reverse proxy (nginx, Caddy) in front if you want HTTPS or a custom port.

#### Updating

```bash
git pull
conda run -n litexplorer pip install -e .
cd frontend && npm install && npm run build && cd ..
# Option A: restart uvicorn in your tmux/screen session
# Option B: sudo systemctl restart litexplorer
```

Schema migrations run automatically at startup.

---

## Configuration

Most configuration is done through the **Settings page** in the UI (`/settings`):

| Setting | Description |
|---|---|
| `api_contact_email` | E-mail sent to OpenAlex and Crossref for polite-pool access (better rate limits). Equivalent to the env vars above; the UI value takes precedence. |
| `pdf_storage_path` | Where PDFs are stored. Defaults to `{data_dir}/pdfs/`. On a server, set this to a directory outside the repo on a persistent volume. |

Environment variables (all prefixed `LITEXPLORER_`) override defaults but are themselves overridden by the database settings above where both apply.

| Variable | Default | Description |
|---|---|---|
| `LITEXPLORER_DATA_DIR` | `~/.litexplorer` | Root for the SQLite DB and default PDF storage |
| `LITEXPLORER_OPENALEX_API_KEY` | — | Polite-pool e-mail for OpenAlex |
| `LITEXPLORER_CROSSREF_MAILTO` | — | Polite-pool e-mail for Crossref |

---

## Running tests

```bash
# Backend (163 tests)
python -m pytest tests/ -v

# Frontend — TypeScript type check + production build
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
litexplorer/          Python package (FastAPI app, models, API routes, services)
  app.py              Lifespan: migrations, backfills, startup normalization
  config.py           Pydantic settings (LITEXPLORER_ env prefix)
  models/             SQLAlchemy ORM models
  api/                FastAPI routers
  services/           Business logic (enrichment, PDF extraction)
  external/           OpenAlex and Crossref API clients
frontend/             React + TypeScript source (Vite)
  src/
    pages/            Route-level components
    components/       Reusable UI components
    hooks/            TanStack React Query hooks
tests/                pytest suite
litexplorer.service   systemd unit file for server deployment
```
