from pydantic import BaseModel


class VenueTierSet(BaseModel):
    """Upsert a tier: set the tier for a (venue, field) pair."""
    venue_id: int
    field_id: int
    tier: int


class VenueTierOut(BaseModel):
    id: int
    venue_id: int
    field_id: int
    tier: int
    venue_name: str | None = None
    field_name: str | None = None

    model_config = {"from_attributes": True}
