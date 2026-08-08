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

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"

class UserOut(BaseModel):
    id: int
    email: str
    role: str
    is_active: bool

class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)

class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8)
    role: Literal["admin", "reviewer", "viewer"] = "reviewer"

TokenResponse.model_rebuild()
