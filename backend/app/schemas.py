from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime

class FieldUpdate(BaseModel):
    field_name: str
    field_value: str | None = None
    validated: bool = False

class DocumentUpdate(BaseModel):
    fields: list[FieldUpdate]
    action: Literal["approve", "reject", "save", "reopen"] = "save"
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

class SchemaEditPayload(BaseModel):
    name: str | None = None
    fields: list[SchemaFieldDef] | None = Field(default=None, min_length=1)

class SuggestedSchemaOut(BaseModel):
    id: int
    key: str
    name: str
    fields: list
    origin_document_id: int | None = None
    created_at: datetime | None = None

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

class WebhookCreate(BaseModel):
    document_type: str
    url: str
    secret: str | None = None
    active: bool = True

class WebhookUpdate(BaseModel):
    url: str | None = None
    secret: str | None = None
    active: bool | None = None

class WebhookOut(BaseModel):
    id: int
    document_type: str
    url: str
    active: bool
    has_secret: bool

class AutoApproveCreate(BaseModel):
    document_type: str = Field(pattern=r"^[a-z0-9_-]+$")
    enabled: bool = False
    min_confidence: float = Field(gt=0.9, le=1.0)

class AutoApproveUpdate(BaseModel):
    enabled: bool | None = None
    min_confidence: float | None = Field(default=None, gt=0.9, le=1.0)

class AutoApproveOut(BaseModel):
    id: int
    document_type: str
    enabled: bool
    min_confidence: float
    created_at: datetime | None = None
