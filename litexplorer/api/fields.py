"""CRUD routes for research fields."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from litexplorer.api.deps import get_db
from litexplorer.models.library import Field
from litexplorer.schemas.fields import FieldCreate, FieldOut

router = APIRouter(prefix="/api/fields", tags=["fields"])


@router.get("", response_model=list[FieldOut])
def list_fields(db: Session = Depends(get_db)):
    fields = db.scalars(
        select(Field).options(selectinload(Field.venues)).order_by(Field.name)
    ).all()
    return [
        FieldOut(id=f.id, name=f.name, venue_count=len(f.venues))
        for f in fields
    ]


@router.post("", response_model=FieldOut, status_code=201)
def create_field(body: FieldCreate, db: Session = Depends(get_db)):
    existing = db.scalars(select(Field).where(Field.name == body.name)).one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Field already exists")
    field = Field(name=body.name)
    db.add(field)
    db.commit()
    db.refresh(field, ["venues"])
    return FieldOut(id=field.id, name=field.name, venue_count=len(field.venues))


@router.delete("/{field_id}", status_code=204)
def delete_field(field_id: int, db: Session = Depends(get_db)):
    field = db.get(Field, field_id)
    if not field:
        raise HTTPException(status_code=404, detail="Field not found")
    db.delete(field)
    db.commit()
    return Response(status_code=204)
