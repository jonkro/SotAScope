from pydantic import BaseModel


class FieldCreate(BaseModel):
    name: str


class FieldOut(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}
