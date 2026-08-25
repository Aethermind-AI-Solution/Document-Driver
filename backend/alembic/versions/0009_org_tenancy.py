"""org tenancy: organizations + org_id (nullable) + backfill + composite uniques

Revision ID: 0009_org_tenancy
Revises: 0008_document_delivery_flags
Create Date: 2026-08-25 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0009_org_tenancy"
down_revision: Union[str, None] = "0008_document_delivery_flags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ["documents", "users", "extracted_fields", "audit_logs",
                 "schema_definitions", "webhook_configs", "auto_approve_configs"]


def upgrade() -> None:
    op.create_table("organizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.execute("INSERT INTO organizations (id, name, created_at) "
               "VALUES (1, 'Default Organization', CURRENT_TIMESTAMP)")
    for t in TENANT_TABLES:
        with op.batch_alter_table(t, schema=None) as b:
            b.add_column(sa.Column("org_id", sa.Integer(), nullable=True))
            b.create_foreign_key(f"fk_{t}_org_id_organizations", "organizations", ["org_id"], ["id"])
            b.create_index(f"ix_{t}_org_id", ["org_id"])
    for t in TENANT_TABLES:
        op.execute(f"UPDATE {t} SET org_id = 1 WHERE org_id IS NULL")
    # swap global uniques -> composite (org_id, ...). The original `key` unique
    # constraint was created unnamed (sa.UniqueConstraint("key")), so SQLite
    # reflects it with name=None; apply a naming_convention during batch reflection
    # so it gets a deterministic name we can drop by. NOTE: pin real Postgres
    # names before prod (Postgres would have auto-named it schema_definitions_key_key).
    with op.batch_alter_table("schema_definitions", schema=None,
                               naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}) as b:
        b.drop_constraint("uq_schema_definitions_key", type_="unique")
        b.create_unique_constraint("uq_schema_definitions_org_id_key", ["org_id", "key"])
    with op.batch_alter_table("webhook_configs", schema=None) as b:
        b.drop_index("ix_webhook_configs_document_type")
        b.create_index("ix_webhook_configs_org_document_type", ["org_id", "document_type"], unique=True)
    with op.batch_alter_table("auto_approve_configs", schema=None) as b:
        b.drop_index("ix_auto_approve_configs_document_type")
        b.create_index("ix_auto_approve_configs_org_document_type", ["org_id", "document_type"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("auto_approve_configs", schema=None) as b:
        b.drop_index("ix_auto_approve_configs_org_document_type")
        b.create_index("ix_auto_approve_configs_document_type", ["document_type"], unique=True)
    with op.batch_alter_table("webhook_configs", schema=None) as b:
        b.drop_index("ix_webhook_configs_org_document_type")
        b.create_index("ix_webhook_configs_document_type", ["document_type"], unique=True)
    with op.batch_alter_table("schema_definitions", schema=None) as b:
        b.drop_constraint("uq_schema_definitions_org_id_key", type_="unique")
        b.create_unique_constraint("uq_schema_definitions_key", ["key"])
    for t in TENANT_TABLES:
        with op.batch_alter_table(t, schema=None) as b:
            b.drop_index(f"ix_{t}_org_id")
            b.drop_constraint(f"fk_{t}_org_id_organizations", type_="foreignkey")
            b.drop_column("org_id")
    op.drop_table("organizations")
