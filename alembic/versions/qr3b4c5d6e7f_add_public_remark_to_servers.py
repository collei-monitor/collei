"""add public_remark to servers

Revision ID: qr3b4c5d6e7f
Revises: pq2a3b4c5d6e
Create Date: 2026-04-05 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "qr3b4c5d6e7f"
down_revision = "pq2a3b4c5d6e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("servers", sa.Column("public_remark", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("servers", "public_remark")
