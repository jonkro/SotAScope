from pydantic import BaseModel


class FieldCreate(BaseModel):
    name: str


class FieldOut(BaseModel):
    id: int
    name: str
    venue_count: int = 0

    model_config = {"from_attributes": True}
