from pydantic import BaseModel, Field
from typing import Any, Literal

class FieldUpdate(BaseModel):
    field_name: str
    field_value: str | None = None
    validated: bool = False

class DocumentUpdate(BaseModel):
    fields: list[FieldUpdate]
    action: Literal["approve", "reject", "save"] = "save"
    reason: str | None = None

class SchemaPayload(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_-]+$")
    name: str
    fields: list[dict[str, Any]]
