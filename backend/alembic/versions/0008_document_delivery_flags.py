"""document webhook delivery status + auto_approved flag

Revision ID: 0008_document_delivery_flags
Revises: 0007_auto_approve_configs
Create Date: 2026-08-16 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0008_document_delivery_flags"
down_revision: Union[str, None] = "0007_auto_approve_configs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as b:
        b.add_column(sa.Column("webhook_status", sa.String(length=20), nullable=True))
        b.add_column(sa.Column("webhook_detail", sa.Text(), nullable=True))
        b.add_column(sa.Column("auto_approved", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as b:
        b.drop_column("auto_approved")
        b.drop_column("webhook_detail")
        b.drop_column("webhook_status")
