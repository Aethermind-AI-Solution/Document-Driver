"""document revision counter

Revision ID: 0006_document_revision
Revises: 0005_schema_status
Create Date: 2026-08-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_document_revision"
down_revision: Union[str, None] = "0005_schema_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_column("revision")
