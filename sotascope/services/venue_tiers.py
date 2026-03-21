"""Helper functions for resolving project-specific venue tiers.

Global tier: stored on Venue.tier (applies to all projects by default).
Local override: stored in ProjectVenueTier (per project_id + venue_id).

Resolution rule: if a ProjectVenueTier row exists for (project_id, venue_id),
use its tier; otherwise fall back to Venue.tier (default 2 = regular).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from sotascope.models.library import Venue
from sotascope.models.project import ProjectVenueTier


def resolve_venue_tier(project_id: int, venue_id: int, db: Session) -> int:
    """Return the effective tier for a venue in a project context."""
    override = db.scalars(
        select(ProjectVenueTier).where(
            ProjectVenueTier.project_id == project_id,
            ProjectVenueTier.venue_id == venue_id,
        )
    ).one_or_none()
    if override is not None:
        return override.tier
    venue = db.get(Venue, venue_id)
    return venue.tier if venue is not None else 2


def bulk_resolve_venue_tiers(
    project_id: int, venue_ids: set[int], db: Session
) -> dict[int, int]:
    """Efficiently return effective tiers for a set of venue IDs in a project.

    Returns a dict mapping venue_id -> effective_tier.  Venues not found in
    either table default to tier 2.
    """
    if not venue_ids:
        return {}

    # 1. Fetch local overrides for this project
    override_rows = db.execute(
        select(ProjectVenueTier.venue_id, ProjectVenueTier.tier).where(
            ProjectVenueTier.project_id == project_id,
            ProjectVenueTier.venue_id.in_(venue_ids),
        )
    ).all()
    overrides: dict[int, int] = {vid: tier for vid, tier in override_rows}

    # 2. Fetch global tiers for venues that have no local override
    missing = venue_ids - set(overrides)
    global_tiers: dict[int, int] = {}
    if missing:
        global_rows = db.execute(
            select(Venue.id, Venue.tier).where(Venue.id.in_(missing))
        ).all()
        global_tiers = {vid: tier for vid, tier in global_rows}

    return {
        vid: overrides.get(vid, global_tiers.get(vid, 2)) for vid in venue_ids
    }
