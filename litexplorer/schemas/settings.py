"""Pydantic schemas for application settings."""

from pydantic import BaseModel


class SettingOut(BaseModel):
    key: str
    value: str
    description: str | None = None

    model_config = {"from_attributes": True}


class SettingUpdate(BaseModel):
    value: str
