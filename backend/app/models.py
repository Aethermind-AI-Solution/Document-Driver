from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

def _default_org_id() -> int:
    """Python-side INSERT default: if a _TenantMixin row is constructed
    without an explicit org_id, stamp it from the ambient context var (same
    source services.log() already uses). Mirrors create_user()'s existing
    org_id-falls-back-to-default pattern. Raises (fail-closed, same as any
    other org-scoped operation) if no org context is set."""
    from .context import current_org_id
    return current_org_id()


class _TenantMixin:
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True,
                                        default=_default_org_id)

class Document(Base, _TenantMixin):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    document_type: Mapped[str] = mapped_column(String(80), default="invoice")
    upload_date: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    status: Mapped[str] = mapped_column(String(40), default="uploaded")
    processing_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_required: Mapped[bool] = mapped_column(Boolean, default=True)
    stored_path: Mapped[str] = mapped_column(Text)
    pipeline_trace: Mapped[list | None] = mapped_column(JSON, nullable=True)
    anomalies: Mapped[list | None] = mapped_column(JSON, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    webhook_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    webhook_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_approved: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    extracted_fields: Mapped[list["ExtractedField"]] = relationship(cascade="all, delete-orphan")
    audit_logs: Mapped[list["AuditLog"]] = relationship(cascade="all, delete-orphan")

class ExtractedField(Base, _TenantMixin):
    __tablename__ = "extracted_fields"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    field_name: Mapped[str] = mapped_column(String(120))
    field_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_value: Mapped[str | None] = mapped_column(Text, nullable=True)  # AI value before any human edit
    confidence: Mapped[float] = mapped_column(Float)
    validated: Mapped[bool] = mapped_column(Boolean, default=False)
    edited_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    grounded: Mapped[str | None] = mapped_column(String(20), nullable=True)

class AuditLog(Base, _TenantMixin):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    action: Mapped[str] = mapped_column(String(120))
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

class User(Base, _TenantMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="reviewer")  # admin|reviewer|viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

class SchemaDefinition(Base, _TenantMixin):
    __tablename__ = "schema_definitions"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_schema_definitions_org_id_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    fields: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="approved", server_default="approved")
    origin_document_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=True)

class WebhookConfig(Base, _TenantMixin):
    __tablename__ = "webhook_configs"
    __table_args__ = (Index("ix_webhook_configs_org_document_type", "org_id", "document_type", unique=True),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_type: Mapped[str] = mapped_column(String(80), index=True)
    url: Mapped[str] = mapped_column(Text)
    secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

class AutoApproveConfig(Base, _TenantMixin):
    __tablename__ = "auto_approve_configs"
    __table_args__ = (Index("ix_auto_approve_configs_org_document_type", "org_id", "document_type", unique=True),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_type: Mapped[str] = mapped_column(String(80), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    min_confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
