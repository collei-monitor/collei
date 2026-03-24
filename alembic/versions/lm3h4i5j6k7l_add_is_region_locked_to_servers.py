"""add is_region_locked to servers

Revision ID: lm3h4i5j6k7l
Revises: kl2g3h4i5j6k
Create Date: 2026-03-24 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "lm3h4i5j6k7l"
down_revision = "kl2g3h4i5j6k"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column("is_region_locked", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("servers", "is_region_locked")
