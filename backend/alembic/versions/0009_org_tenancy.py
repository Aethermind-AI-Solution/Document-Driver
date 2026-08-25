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
    # constraint was created unnamed (sa.UniqueConstraint("key")) in 0001_initial,
    # so its reflected name differs by backend: SQLite reflects it as name=None,
    # while Postgres auto-names it `schema_definitions_key_key`. Batch mode only
    # rewrites the whole table (and thus tolerates unnamed constraints) on SQLite;
    # on Postgres it ALTERs in place and needs the real constraint name. Reflect
    # the actual name at runtime so this works portably on both backends.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    key_uqs = [uc["name"] for uc in insp.get_unique_constraints("schema_definitions")
               if uc["column_names"] == ["key"]]
    with op.batch_alter_table("schema_definitions", schema=None) as b:
        for name in key_uqs:
            if name:
                b.drop_constraint(name, type_="unique")
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
