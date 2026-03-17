"""add logs table

Revision ID: ij9d0e1f2g3h
Revises: hi8c9d0e1f2g
Create Date: 2026-03-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "ij9d0e1f2g3h"
down_revision = "hi8c9d0e1f2g"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("level", sa.String(), nullable=False, server_default=sa.text("'info'")),
        sa.Column("msg_type", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("ip", sa.String(), nullable=True),
        sa.Column("user_uuid", sa.String(), nullable=True),
        sa.Column("server_uuid", sa.String(), nullable=True),
        sa.Column("time", sa.Integer(), nullable=False),
    )
    op.create_index("ix_logs_time", "logs", ["time"])
    op.create_index("ix_logs_msg_type", "logs", ["msg_type"])


def downgrade() -> None:
    op.drop_index("ix_logs_msg_type", table_name="logs")
    op.drop_index("ix_logs_time", table_name="logs")
    op.drop_table("logs")
