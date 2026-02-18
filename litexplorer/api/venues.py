"""CRUD routes for venues and their aliases."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from litexplorer.api.deps import get_db
from litexplorer.models.library import Venue, VenueAlias, VenueTier
from litexplorer.schemas.venues import (
    VenueAliasCreate,
    VenueAliasOut,
    VenueCreate,
    VenueDetail,
    VenueOut,
    VenueTierNested,
    VenueUpdate,
)

router = APIRouter(prefix="/api/venues", tags=["venues"])


def _get_venue(db: Session, venue_id: int) -> Venue:
    venue = db.get(Venue, venue_id)
    if not venue:
        raise HTTPException(status_code=404, detail="Venue not found")
    return venue


def _venue_detail(venue: Venue) -> VenueDetail:
    return VenueDetail(
        **{c.key: getattr(venue, c.key) for c in Venue.__table__.columns},
        aliases=[VenueAliasOut.model_validate(a) for a in venue.aliases],
        tiers=[
            VenueTierNested(
                id=t.id,
                field_id=t.field_id,
                field_name=t.field.name if t.field else None,
                tier=t.tier,
            )
            for t in venue.tiers
        ],
    )


@router.get("", response_model=list[VenueOut])
def list_venues(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, description="Search venue name"),
    db: Session = Depends(get_db),
):
    stmt = select(Venue).order_by(Venue.name)
    if q:
        stmt = stmt.where(Venue.name.ilike(f"%{q}%"))
    return db.scalars(stmt.offset(offset).limit(limit)).all()


@router.post("", response_model=VenueDetail, status_code=201)
def create_venue(body: VenueCreate, db: Session = Depends(get_db)):
    venue = Venue(**body.model_dump())
    db.add(venue)
    db.commit()
    db.refresh(venue)
    return _venue_detail(venue)


@router.get("/{venue_id}", response_model=VenueDetail)
def get_venue(venue_id: int, db: Session = Depends(get_db)):
    venue = db.scalars(
        select(Venue)
        .where(Venue.id == venue_id)
        .options(
            joinedload(Venue.aliases),
            joinedload(Venue.tiers).joinedload(VenueTier.field),
        )
    ).unique().one_or_none()
    if not venue:
        raise HTTPException(status_code=404, detail="Venue not found")
    return _venue_detail(venue)


@router.patch("/{venue_id}", response_model=VenueDetail)
def update_venue(venue_id: int, body: VenueUpdate, db: Session = Depends(get_db)):
    venue = _get_venue(db, venue_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(venue, key, value)
    db.commit()
    db.refresh(venue)
    return _venue_detail(venue)


@router.delete("/{venue_id}", status_code=204)
def delete_venue(venue_id: int, db: Session = Depends(get_db)):
    venue = _get_venue(db, venue_id)
    db.delete(venue)
    db.commit()


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

@router.post("/{venue_id}/aliases", response_model=VenueAliasOut, status_code=201)
def add_alias(venue_id: int, body: VenueAliasCreate, db: Session = Depends(get_db)):
    venue = _get_venue(db, venue_id)
    alias = VenueAlias(venue_id=venue.id, alias=body.alias)
    db.add(alias)
    db.commit()
    db.refresh(alias)
    return alias


@router.delete("/{venue_id}/aliases/{alias_id}", status_code=204)
def remove_alias(venue_id: int, alias_id: int, db: Session = Depends(get_db)):
    alias = db.scalars(
        select(VenueAlias).where(
            VenueAlias.id == alias_id, VenueAlias.venue_id == venue_id
        )
    ).one_or_none()
    if not alias:
        raise HTTPException(status_code=404, detail="Alias not found")
    db.delete(alias)
    db.commit()
