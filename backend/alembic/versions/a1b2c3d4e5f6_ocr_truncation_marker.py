"""ocr truncation marker (AUDIT BC-F17)

Revision ID: a1b2c3d4e5f6
Revises: 7768bb0933f0
Create Date: 2026-07-18
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "7768bb0933f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ocr_results",
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column("ocr_results", sa.Column("total_pages", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("ocr_results", "total_pages")
    op.drop_column("ocr_results", "truncated")
