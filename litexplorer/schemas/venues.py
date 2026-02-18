from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

class VenueAliasCreate(BaseModel):
    alias: str


class VenueAliasOut(BaseModel):
    id: int
    venue_id: int
    alias: str

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Venue tiers (nested in venue detail)
# ---------------------------------------------------------------------------

class VenueTierNested(BaseModel):
    id: int
    field_id: int
    field_name: str | None = None
    tier: int

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Venues
# ---------------------------------------------------------------------------

class VenueCreate(BaseModel):
    name: str
    dblp_id: str | None = None
    openalex_id: str | None = None
    venue_type: str | None = None


class VenueUpdate(BaseModel):
    name: str | None = None
    dblp_id: str | None = None
    openalex_id: str | None = None
    venue_type: str | None = None


class VenueOut(BaseModel):
    id: int
    name: str
    dblp_id: str | None
    openalex_id: str | None
    venue_type: str | None

    model_config = {"from_attributes": True}


class VenueDetail(VenueOut):
    aliases: list[VenueAliasOut] = []
    tiers: list[VenueTierNested] = []
