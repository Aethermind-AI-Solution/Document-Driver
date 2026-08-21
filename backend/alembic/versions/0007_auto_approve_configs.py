"""auto approve configs

Revision ID: 0007_auto_approve_configs
Revises: 0006_document_revision
Create Date: 2026-08-16 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0007_auto_approve_configs"
down_revision: Union[str, None] = "0006_document_revision"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table("auto_approve_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_type", sa.String(length=80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("min_confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"))
    with op.batch_alter_table("auto_approve_configs", schema=None) as b:
        b.create_index(b.f("ix_auto_approve_configs_document_type"), ["document_type"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("auto_approve_configs", schema=None) as b:
        b.drop_index(b.f("ix_auto_approve_configs_document_type"))
    op.drop_table("auto_approve_configs")
