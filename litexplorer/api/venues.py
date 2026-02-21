"""CRUD routes for venues, their aliases, and field associations."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from litexplorer.api.deps import get_db
from litexplorer.models.library import Field, Venue, VenueAlias, VenueField, Work
from litexplorer.schemas.venues import (
    VenueAliasCreate,
    VenueAliasOut,
    VenueCreate,
    VenueDetail,
    VenueFieldNested,
    VenueOut,
    VenueUpdate,
)

router = APIRouter(prefix="/api/venues", tags=["venues"])


def _get_venue(db: Session, venue_id: int) -> Venue:
    venue = db.get(Venue, venue_id)
    if not venue:
        raise HTTPException(status_code=404, detail="Venue not found")
    return venue


def _venue_detail(venue: Venue, work_count: int = 0) -> VenueDetail:
    return VenueDetail(
        **{c.key: getattr(venue, c.key) for c in Venue.__table__.columns},
        aliases=[VenueAliasOut.model_validate(a) for a in venue.aliases],
        fields=[
            VenueFieldNested(
                id=vf.id,
                field_id=vf.field_id,
                field_name=vf.field.name if vf.field else None,
            )
            for vf in venue.fields
        ],
        work_count=work_count,
    )


_VENUE_SORT_COLUMNS = {"name", "venue_type", "tier", "work_count", "field_display"}


@router.get("", response_model=list[VenueOut])
def list_venues(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, description="Search venue name"),
    sort_by: str = Query("name", description="Sort column"),
    sort_dir: str = Query("asc", description="Sort direction: asc or desc"),
    db: Session = Depends(get_db),
):
    work_count_sq = (
        select(func.count())
        .select_from(Work)
        .where(Work.venue_id == Venue.id)
        .correlate(Venue)
        .scalar_subquery()
        .label("work_count")
    )
    field_name_sq = (
        select(func.min(Field.name))
        .select_from(VenueField)
        .join(Field, VenueField.field_id == Field.id)
        .where(VenueField.venue_id == Venue.id)
        .correlate(Venue)
        .scalar_subquery()
        .label("field_display")
    )
    stmt = (
        select(Venue, work_count_sq, field_name_sq)
        .options(joinedload(Venue.fields).joinedload(VenueField.field))
    )
    if q:
        stmt = stmt.where(Venue.name.ilike(f"%{q}%"))

    # Determine sort expression
    col = sort_by if sort_by in _VENUE_SORT_COLUMNS else "name"
    if col == "work_count":
        sort_expr = work_count_sq
    elif col == "field_display":
        sort_expr = field_name_sq
    else:
        sort_expr = getattr(Venue, col)
    sort_expr = sort_expr.desc() if sort_dir == "desc" else sort_expr.asc()
    stmt = stmt.order_by(sort_expr)

    rows = db.execute(stmt.offset(offset).limit(limit)).unique().all()
    results = []
    for v, wc, fd in rows:
        d = {c.key: getattr(v, c.key) for c in Venue.__table__.columns}
        names = [vf.field.name for vf in v.fields if vf.field]
        if len(names) == 0:
            d["field_display"] = None
        elif len(names) == 1:
            d["field_display"] = names[0]
        else:
            d["field_display"] = "multi"
        d["work_count"] = wc or 0
        results.append(VenueOut(**d))
    return results


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
            joinedload(Venue.fields).joinedload(VenueField.field),
        )
    ).unique().one_or_none()
    if not venue:
        raise HTTPException(status_code=404, detail="Venue not found")
    wc = db.scalar(select(func.count()).select_from(Work).where(Work.venue_id == venue_id)) or 0
    return _venue_detail(venue, work_count=wc)


@router.patch("/{venue_id}", response_model=VenueDetail)
def update_venue(venue_id: int, body: VenueUpdate, db: Session = Depends(get_db)):
    venue = _get_venue(db, venue_id)
    updates = body.model_dump(exclude_unset=True)

    # When renaming, preserve old name as alias for search continuity
    if "name" in updates and updates["name"] and updates["name"] != venue.name:
        old_name = venue.name
        existing = db.scalars(
            select(VenueAlias).where(
                VenueAlias.venue_id == venue.id, VenueAlias.alias == old_name
            )
        ).one_or_none()
        if not existing:
            # Place at end of sort order
            max_order = db.scalar(
                select(func.max(VenueAlias.sort_order)).where(VenueAlias.venue_id == venue.id)
            ) or -1
            db.add(VenueAlias(venue_id=venue.id, alias=old_name, sort_order=max_order + 1))

    for key, value in updates.items():
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


@router.post("/{venue_id}/aliases/reorder", response_model=list[VenueAliasOut])
def reorder_aliases(venue_id: int, body: dict, db: Session = Depends(get_db)):
    """Reorder aliases for a venue. Body: {"alias_ids": [id1, id2, ...]}"""
    _get_venue(db, venue_id)
    alias_ids = body.get("alias_ids", [])
    if not alias_ids:
        raise HTTPException(status_code=422, detail="alias_ids is required")
    aliases = db.scalars(
        select(VenueAlias).where(
            VenueAlias.venue_id == venue_id, VenueAlias.id.in_(alias_ids)
        )
    ).all()
    alias_map = {a.id: a for a in aliases}
    for idx, aid in enumerate(alias_ids):
        if aid in alias_map:
            alias_map[aid].sort_order = idx
    db.commit()
    # Return updated aliases in new order
    updated = db.scalars(
        select(VenueAlias)
        .where(VenueAlias.venue_id == venue_id)
        .order_by(VenueAlias.sort_order)
    ).all()
    return [VenueAliasOut.model_validate(a) for a in updated]


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


# ---------------------------------------------------------------------------
# Field associations
# ---------------------------------------------------------------------------

@router.post("/{venue_id}/fields", response_model=VenueFieldNested, status_code=201)
def add_field(venue_id: int, body: dict, db: Session = Depends(get_db)):
    """Associate a venue with a field. Body: {"field_id": int}"""
    venue = _get_venue(db, venue_id)
    field_id = body.get("field_id")
    if not field_id or not db.get(Field, field_id):
        raise HTTPException(status_code=422, detail="Field not found")
    existing = db.scalars(
        select(VenueField).where(
            VenueField.venue_id == venue.id, VenueField.field_id == field_id
        )
    ).one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Field already associated")
    vf = VenueField(venue_id=venue.id, field_id=field_id)
    db.add(vf)
    db.commit()
    db.refresh(vf)
    return VenueFieldNested(
        id=vf.id,
        field_id=vf.field_id,
        field_name=vf.field.name if vf.field else None,
    )


@router.delete("/{venue_id}/fields/{field_id}", status_code=204)
def remove_field(venue_id: int, field_id: int, db: Session = Depends(get_db)):
    vf = db.scalars(
        select(VenueField).where(
            VenueField.venue_id == venue_id, VenueField.field_id == field_id
        )
    ).one_or_none()
    if not vf:
        raise HTTPException(status_code=404, detail="Field association not found")
    db.delete(vf)
    db.commit()
