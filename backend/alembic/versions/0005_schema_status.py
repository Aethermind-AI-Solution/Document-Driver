"""dynamic schema status

Revision ID: 0005_schema_status
Revises: 46ae0d9054f0
Create Date: 2026-08-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_schema_status"
down_revision: Union[str, None] = "46ae0d9054f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("schema_definitions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("status", sa.String(length=20),
                                      nullable=False, server_default="approved"))
        batch_op.add_column(sa.Column("origin_document_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("created_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("schema_definitions", schema=None) as batch_op:
        batch_op.drop_column("created_at")
        batch_op.drop_column("origin_document_id")
        batch_op.drop_column("status")
