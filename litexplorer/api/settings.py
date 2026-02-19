"""Settings API router — read and update application settings."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from litexplorer.api.deps import get_db
from litexplorer.models.settings import Setting
from litexplorer.schemas.settings import SettingOut, SettingUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


def get_setting_value(db: Session, key: str) -> str | None:
    """Read a setting value from the DB. Returns None if missing or empty."""
    row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if row is None or not row.value:
        return None
    return row.value


@router.get("", response_model=list[SettingOut])
def list_settings(db: Session = Depends(get_db)):
    """Return all application settings."""
    rows = db.scalars(select(Setting).order_by(Setting.key)).all()
    return [SettingOut.model_validate(r) for r in rows]


@router.patch("/{key}", response_model=SettingOut)
def update_setting(key: str, body: SettingUpdate, db: Session = Depends(get_db)):
    """Update a setting's value."""
    row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
    row.value = body.value
    db.commit()
    db.refresh(row)
    return SettingOut.model_validate(row)
