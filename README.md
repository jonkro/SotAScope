# LitExplorer

A local-first research literature dashboard for mapping the state of the art around a research topic.

You build a library of papers (BibTeX import, DOI lookup, or search-by-title), group them into topic lists, and LitExplorer fetches the citation graph from OpenAlex, Crossref, and Semantic Scholar. The main view is a **citation timeline**: seeds (papers you selected) are squares, backward-citation neighbors are circles, forward-citation neighbors are diamonds, all plotted on a log-citation-count y-axis. A sliding window lets you count only citations from the last N years. Topic lists in the legend are clickable to toggle their visibility — hiding seeds, updating multi-topic-list color stripes, and removing candidates connected only to hidden lists.

Other features: venue tier list (tier 1 = top venue, tier 3 = exclude from timeline), venue aliases for year-to-year name variation, multi-DOI support (a paper can carry several valid DOIs), PDF upload with auto text extraction, per-paper notes with AI/user provenance tracking, library sanitization tools (duplicate detection, work merge), SSL verification toggle for corporate proxy environments. **Structured extraction schemas** let you define tables of LLM-answerable questions (columns) and run them against any set of seed papers in a project — the **Extract & Review** tab shows a live results table where each cell displays the extracted answer with a provenance badge (AI/reviewed/user), an Accept button to mark AI answers as reviewed, and an inline editor (dropdown for constrained columns, text for free-form). Re-running extraction skips already-reviewed cells and overwrites only AI-generated ones. Results can be exported as **CSV** or **LaTeX** (booktabs table) from the Extract & Review action bar. **Per-paper and per-project chat** lets you discuss any paper (or a selection of project seed papers) with an LLM using extracted PDF text or raw PDF bytes (Anthropic only). The project discussion view has a paper selector panel with topic-list color bars (thin vertical bars on each paper's left edge matching the timeline legend colors), topic list bulk-select checkboxes (with indeterminate state when partially selected), and selected papers sorted to the top. Conversations persist across navigation — the last session is automatically restored on re-visit, and you can save named snapshots to load later. LLM provider configuration (Anthropic, OpenAI, or any local OpenAI-compatible server such as Ollama — leave the API key blank for local servers) is available in Settings.

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
cd LitExplorer

# 2. Python environment (pins Python 3.11)
conda env create -f environment.yml
conda activate litexplorer
pip install -e .

# 3. Run
# Local-only (laptop/desktop — not accessible from other machines):
uvicorn litexplorer.app:app --host 127.0.0.1 --port 8000

# Shared server (accessible on the network):
uvicorn litexplorer.app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in your browser. The SQLite database and PDFs are created under `~/.litexplorer/` by default. Set `LITEXPLORER_DATA_DIR` to use a different location.

No root or sudo rights are required.

---

### Server (shared team use)

Same install steps as above. For the server to keep running after you disconnect, run uvicorn inside a `tmux` or `screen` session:

```bash
tmux new -s litexplorer
conda activate litexplorer
uvicorn litexplorer.app:app --host 0.0.0.0 --port 8000
# Detach with Ctrl-b d
```

The app listens on `0.0.0.0:8000`. Put a reverse proxy (nginx, Caddy) in front if you want HTTPS or a custom port.

#### Optional: run as a systemd service

If you want the process to start automatically on boot and restart on failure, a systemd unit file is included. Edit `litexplorer.service` and replace the two `/path/to/LitExplorer` placeholders with the absolute path to your clone, then:

```bash
cp env.example env
# Edit env — set UVICORN_BIN to the output of: conda run -n litexplorer which uvicorn
# Set any other options you need (data directory, proxy, contact email)

sudo cp litexplorer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now litexplorer
```

Check it started:

```bash
sudo systemctl status litexplorer
journalctl -u litexplorer -f
```

#### Updating

```bash
git pull
conda run -n litexplorer pip install -e .
# Restart however you started it:
#   tmux: kill and relaunch uvicorn
#   systemd: sudo systemctl restart litexplorer
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
| `s2_api_key` | Semantic Scholar API key (optional). S2 enforces 1 req/s regardless, but without a key the quota is shared across all users on the same IP — 429 errors are common on shared/university networks. An API key gives you a dedicated quota. Apply at https://www.semanticscholar.org/product/api. |
| `llm_provider` | LLM provider: `anthropic` or `openai`. Leave blank to disable LLM features. |
| `llm_api_key` | API key for the selected provider. Optional when `llm_base_url` points to a local server. |
| `llm_model_id` | Model to use (e.g. `claude-sonnet-4-6`, `gpt-4o`). The Settings page loads available models from the provider API and shows a dropdown. |
| `llm_base_url` | Override the provider's default API endpoint. Use this to point to a local inference server (e.g. `http://localhost:11434/v1` for Ollama). If you omit the `/v1` suffix (e.g. enter `http://localhost:11434`), LitExplorer appends it automatically. |

Environment variables (all prefixed `LITEXPLORER_`) can be set in a shell or in the `env` file (see `env.example`):

| Variable | Default | Description |
|---|---|---|
| `LITEXPLORER_DATA_DIR` | `~/.litexplorer` | Root for the SQLite DB and default PDF storage |
| `LITEXPLORER_OPENALEX_API_KEY` | — | Polite-pool e-mail for OpenAlex |
| `LITEXPLORER_CROSSREF_MAILTO` | — | Polite-pool e-mail for Crossref |

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
# Backend (328 tests)
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
litexplorer/          Python package (FastAPI app, models, API routes, services)
  app.py              Lifespan: migrations, backfills, startup normalization
  config.py           Pydantic settings (LITEXPLORER_ env prefix)
  models/             SQLAlchemy ORM models
  api/                FastAPI routers
  services/           Business logic (enrichment, PDF extraction)
  external/           OpenAlex, Crossref, and Semantic Scholar API clients
frontend/
  src/                React + TypeScript source
  dist/               Pre-built frontend — served by FastAPI (committed to repo)
tests/                pytest suite
env.example           Template for the env file (copy to env and fill in values)
litexplorer.service   Optional systemd unit file for server deployment
```
