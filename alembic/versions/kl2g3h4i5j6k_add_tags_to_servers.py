"""add tags to servers

Revision ID: kl2g3h4i5j6k
Revises: jk0e1f2g3h4i
Create Date: 2026-03-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "kl2g3h4i5j6k"
down_revision = "jk0e1f2g3h4i"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column("tags", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("servers", "tags")
