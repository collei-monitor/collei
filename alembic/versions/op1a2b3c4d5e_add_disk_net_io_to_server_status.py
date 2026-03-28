"""add current_disk_io and current_net_io to server_status

Revision ID: op1a2b3c4d5e
Revises: no5p6q7r8s9t
Create Date: 2026-03-28 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "op1a2b3c4d5e"
down_revision = "no5p6q7r8s9t"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "server_status",
        sa.Column("current_disk_io", sa.Text(), server_default="[]", nullable=True),
    )
    op.add_column(
        "server_status",
        sa.Column("current_net_io", sa.Text(), server_default="[]", nullable=True),
    )


def downgrade() -> None:
    op.drop_column("server_status", "current_net_io")
    op.drop_column("server_status", "current_disk_io")
