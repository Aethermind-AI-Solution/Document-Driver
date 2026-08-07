from pydantic import BaseModel, Field
from typing import Literal

class FieldUpdate(BaseModel):
    field_name: str
    field_value: str | None = None
    validated: bool = False

class DocumentUpdate(BaseModel):
    fields: list[FieldUpdate]
    action: Literal["approve", "reject", "save"] = "save"
    reason: str | None = None

class SchemaFieldDef(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str
    type: Literal["string", "number", "date", "array"] = "string"
    required: bool = False
    pattern: str | None = None
    enum: list[str] | None = None
    columns: list[str] | None = None

class SchemaPayload(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_-]+$")
    name: str
    fields: list[SchemaFieldDef] = Field(min_length=1)
