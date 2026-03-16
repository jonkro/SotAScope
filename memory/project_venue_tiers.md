---
name: per_project_venue_tiers
description: Per-project venue tier overrides — model, service, API, frontend tab
type: project
---

Per-project venue tiers implemented (Mar 2026).

**Why:** Different projects may want to classify venues differently (e.g., a CV project treats CVPR as tier 1, but an NLP project doesn't).

**How to apply:** When resolving venue tiers in a project context, always use `resolve_venue_tier()` or `bulk_resolve_venue_tiers()` from `litexplorer/services/venue_tiers.py` rather than reading `Venue.tier` directly.

## Architecture

- `ProjectVenueTier` model in `litexplorer/models/project.py`: `(project_id FK, venue_id FK, tier INTEGER, UNIQUE(project_id, venue_id))`. CASCADE delete on project_id.
- Migration in `app.py` `_migrate_schema()`: creates `project_venue_tiers` table if not exists.
- Service helpers in `litexplorer/services/venue_tiers.py`:
  - `resolve_venue_tier(project_id, venue_id, db) -> int` — single venue
  - `bulk_resolve_venue_tiers(project_id, venue_ids, db) -> dict[int, int]` — batch
- Pydantic schemas in `litexplorer/schemas/projects.py`: `ProjectVenueTierOut`, `ProjectVenueTierUpdate`
- API endpoints in `litexplorer/api/projects.py` (prefix `/api/projects`):
  - `GET /{id}/venue-tiers` — all project-relevant venues with global/local/effective tier
  - `PUT /{id}/venue-tiers/{venue_id}` — upsert local override
  - `DELETE /{id}/venue-tiers/{venue_id}` — delete override (reverts to global)
- Timeline endpoint (`api/timeline.py`) uses `bulk_resolve_venue_tiers` for `tier1_venue_ids` / `ignored_venue_ids`.

## Frontend

- TS type: `ProjectVenueTierOut` in `types.ts`
- API functions in `api.ts`: `fetchProjectVenueTiers`, `setProjectVenueTier`, `resetProjectVenueTier`
- Hook in `hooks/useVenueTiers.ts`: `useProjectVenueTiers`, `useSetProjectVenueTier`, `useResetProjectVenueTier`
- Tab component: `components/ProjectVenueTiersTab.tsx` — search input, flat venue list with tier dropdown, "(global)"/"(local)" badge, "✕" reset button
- "Venue Tiers" tab added to `ProjectDetailPage.tsx` (rightmost base tab, before promoted schema tabs)

## Tests

`tests/test_project_venue_tiers.py` — 22 tests covering service layer + API CRUD + timeline integration.
