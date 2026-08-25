"""org_id NOT NULL: enforce tenant column now that all rows are backfilled
and every write path stamps org_id (fail-closed loader-criteria is live)

Revision ID: 0010_org_id_not_null
Revises: 0009_org_tenancy
Create Date: 2026-08-25 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0010_org_id_not_null"
down_revision: Union[str, None] = "0009_org_tenancy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ["documents", "users", "extracted_fields", "audit_logs",
                 "schema_definitions", "webhook_configs", "auto_approve_configs"]


def upgrade() -> None:
    for t in TENANT_TABLES:
        with op.batch_alter_table(t, schema=None) as b:
            b.alter_column("org_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    for t in TENANT_TABLES:
        with op.batch_alter_table(t, schema=None) as b:
            b.alter_column("org_id", existing_type=sa.Integer(), nullable=True)
