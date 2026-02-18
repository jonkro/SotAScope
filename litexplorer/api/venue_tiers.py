"""Venue tier management — map venues to tiers within research fields."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from litexplorer.api.deps import get_db
from litexplorer.models.library import Field, Venue, VenueTier
from litexplorer.schemas.venue_tiers import VenueTierOut, VenueTierSet

router = APIRouter(prefix="/api/venue-tiers", tags=["venue-tiers"])


def _tier_out(t: VenueTier) -> VenueTierOut:
    return VenueTierOut(
        id=t.id,
        venue_id=t.venue_id,
        field_id=t.field_id,
        tier=t.tier,
        venue_name=t.venue.name if t.venue else None,
        field_name=t.field.name if t.field else None,
    )


@router.get("", response_model=list[VenueTierOut])
def list_tiers(
    venue_id: int | None = Query(None),
    field_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    stmt = select(VenueTier)
    if venue_id is not None:
        stmt = stmt.where(VenueTier.venue_id == venue_id)
    if field_id is not None:
        stmt = stmt.where(VenueTier.field_id == field_id)
    tiers = db.scalars(stmt).all()
    return [_tier_out(t) for t in tiers]


@router.put("", response_model=VenueTierOut)
def upsert_tier(body: VenueTierSet, db: Session = Depends(get_db)):
    """Create or update a venue tier for a (venue, field) pair."""
    if not db.get(Venue, body.venue_id):
        raise HTTPException(status_code=422, detail="Venue not found")
    if not db.get(Field, body.field_id):
        raise HTTPException(status_code=422, detail="Field not found")

    existing = db.scalars(
        select(VenueTier).where(
            VenueTier.venue_id == body.venue_id, VenueTier.field_id == body.field_id
        )
    ).one_or_none()

    if existing:
        existing.tier = body.tier
        db.commit()
        db.refresh(existing)
        return _tier_out(existing)

    tier = VenueTier(venue_id=body.venue_id, field_id=body.field_id, tier=body.tier)
    db.add(tier)
    db.commit()
    db.refresh(tier)
    return _tier_out(tier)


@router.delete("/{tier_id}", status_code=204)
def delete_tier(tier_id: int, db: Session = Depends(get_db)):
    tier = db.get(VenueTier, tier_id)
    if not tier:
        raise HTTPException(status_code=404, detail="Venue tier not found")
    db.delete(tier)
    db.commit()
