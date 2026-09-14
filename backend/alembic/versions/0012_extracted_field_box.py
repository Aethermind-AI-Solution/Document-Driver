"""extracted_field bounding box for click-to-highlight review

Revision ID: 0012_extracted_field_box
Revises: 0011_document_fingerprint
Create Date: 2026-09-14 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0012_extracted_field_box"
down_revision: Union[str, None] = "0011_document_fingerprint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("extracted_fields", schema=None) as b:
        b.add_column(sa.Column("box", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("extracted_fields", schema=None) as b:
        b.drop_column("box")
