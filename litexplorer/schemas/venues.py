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
# Venue fields (nested in venue detail)
# ---------------------------------------------------------------------------

class VenueFieldNested(BaseModel):
    id: int
    field_id: int
    field_name: str | None = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Venues
# ---------------------------------------------------------------------------

class VenueCreate(BaseModel):
    name: str
    dblp_id: str | None = None
    openalex_id: str | None = None
    issn: str | None = None
    publisher: str | None = None
    venue_type: str | None = None
    tier: int = 2


class VenueUpdate(BaseModel):
    name: str | None = None
    dblp_id: str | None = None
    openalex_id: str | None = None
    issn: str | None = None
    publisher: str | None = None
    venue_type: str | None = None
    tier: int | None = None


class VenueOut(BaseModel):
    id: int
    name: str
    dblp_id: str | None
    openalex_id: str | None
    issn: str | None
    publisher: str | None
    venue_type: str | None
    tier: int

    model_config = {"from_attributes": True}


class VenueDetail(VenueOut):
    aliases: list[VenueAliasOut] = []
    fields: list[VenueFieldNested] = []
