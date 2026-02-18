"""CRUD routes for research fields."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from litexplorer.api.deps import get_db
from litexplorer.models.library import Field
from litexplorer.schemas.fields import FieldCreate, FieldOut

router = APIRouter(prefix="/api/fields", tags=["fields"])


@router.get("", response_model=list[FieldOut])
def list_fields(db: Session = Depends(get_db)):
    return db.scalars(select(Field).order_by(Field.name)).all()


@router.post("", response_model=FieldOut, status_code=201)
def create_field(body: FieldCreate, db: Session = Depends(get_db)):
    existing = db.scalars(select(Field).where(Field.name == body.name)).one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Field already exists")
    field = Field(name=body.name)
    db.add(field)
    db.commit()
    db.refresh(field)
    return field
