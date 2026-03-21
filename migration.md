# Migrating from LitExplorer to SotAScope

## What changes

| Old | New |
|---|---|
| `litexplorer` conda env (recommended name) | `sotascope` |
| `litexplorer` CLI command | `sotascope` |
| `~/.litexplorer/` data directory | `~/.sotascope/` |
| `LITEXPLORER_DATA_DIR` env var | `SOTASCOPE_DATA_DIR` |
| `LITEXPLORER_OPENALEX_API_KEY` env var | `SOTASCOPE_OPENALEX_API_KEY` |
| `LITEXPLORER_CROSSREF_MAILTO` env var | `SOTASCOPE_CROSSREF_MAILTO` |
| `litexplorer.service` systemd unit | `sotascope.service` |
| Browser localStorage keys (`litexplorer:…`) | `sotascope:…` (resets UI state) |

The **database file itself** (`litexplorer.db`) is not renamed — existing databases work as-is.

---

## Step 1 — Install the package

Activate your existing conda environment (whatever it is called) and reinstall:

```bash
pip uninstall litexplorer -y        # remove stale editable install
pip install --upgrade pip           # ensure pip supports pyproject.toml editable installs
pip install -e /path/to/SotAScope   # or cd into the repo and run: pip install -e .
```

Verify:

```bash
sotascope --help
```

---

## Step 2 — Migrate the data directory

**Do not use `mv ~/.litexplorer ~/.sotascope` if `~/.sotascope` already exists** (e.g. because you ran `sotascope` once before migrating). That would nest `.litexplorer` inside `.sotascope` as a hidden directory, and the app would see an empty database.

The safe way:

```bash
# If ~/.sotascope does NOT exist yet:
mv ~/.litexplorer ~/.sotascope

# If ~/.sotascope already exists (app was run once before migrating):
mv ~/.sotascope/litexplorer.db ~/.sotascope/litexplorer.db.empty  # keep as backup
mv ~/.sotascope/pdfs ~/.sotascope/pdfs.empty                      # keep as backup
mv ~/.litexplorer/litexplorer.db ~/.sotascope/
mv ~/.litexplorer/pdfs ~/.sotascope/
rmdir ~/.litexplorer
rm ~/.sotascope/litexplorer.db.empty
rm -r ~/.sotascope/pdfs.empty
```

Verify the data is in the right place:

```bash
ls -lh ~/.sotascope/litexplorer.db   # should be non-trivially large (not ~192 KB)
```

A freshly created empty database is ~192 KB. Your real database will be much larger.

---

## Step 3 — Rename the conda environment (optional)

This is cosmetic — the app runs fine in an env named `litexplorer`. Skip if you prefer.

```bash
# conda 23.9+ only:
conda rename -n litexplorer sotascope

# Older conda: create a new env from the spec file instead:
conda env create -f environment.yml   # creates 'sotascope' env (default name)
conda activate sotascope
pip install -e .
```

The environment name in `environment.yml` defaults to `sotascope`, but you can override it with `--name` without editing the file:

```bash
conda env create -f environment.yml --name myenvname
conda activate myenvname
pip install -e .
```

**If `which python` still points to the base Python after activation**, your conda version is too old to properly modify PATH. Either upgrade conda (`conda update -n base conda`) or use explicit paths:

```bash
/usr/local/anaconda3/envs/sotascope/bin/python -m sotascope
# or just use the full path to the entry point:
/usr/local/anaconda3/envs/sotascope/bin/sotascope
```

---

## Step 4 — Update env file and systemd service (server deployments)

```bash
cp env.example env          # or edit your existing env file
# Replace LITEXPLORER_ prefixes with SOTASCOPE_ in the env file

cp sotascope.service /etc/systemd/system/
# Edit WorkingDirectory and EnvironmentFile paths inside the file, then:
systemctl daemon-reload
systemctl disable --now litexplorer   # stop old service
systemctl enable --now sotascope      # start new service
```

---

## Step 5 — Clear browser state (optional)

localStorage keys changed from `litexplorer:…` to `sotascope:…`. Old keys are harmless but orphaned — they will never be read again. If you want a clean slate:

1. Open browser DevTools → Application → Local Storage → your SotAScope URL
2. Filter by `litexplorer:` and delete those keys

Or just leave them — they take up negligible space.

---

## Verify everything works

```bash
sotascope                        # starts on http://127.0.0.1:8000
python -m pytest tests/ -q       # all 770 tests should pass
```
