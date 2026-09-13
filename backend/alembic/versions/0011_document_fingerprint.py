"""document fingerprint for duplicate detection

Revision ID: 0011_document_fingerprint
Revises: 0010_org_id_not_null
Create Date: 2026-09-13 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0011_document_fingerprint"
down_revision: Union[str, None] = "0010_org_id_not_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as b:
        b.add_column(sa.Column("fingerprint", sa.String(length=64), nullable=True))
        b.create_index("ix_documents_fingerprint", ["fingerprint"])


def downgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as b:
        b.drop_index("ix_documents_fingerprint")
        b.drop_column("fingerprint")
