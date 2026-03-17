"""add load_minute and load_hour tables

Revision ID: hi8c9d0e1f2g
Revises: gh7b8c9d0e1f
Create Date: 2026-03-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "hi8c9d0e1f2g"
down_revision = "gh7b8c9d0e1f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── load_minute ──
    op.create_table(
        "load_minute",
        sa.Column(
            "server_uuid", sa.String(),
            sa.ForeignKey("servers.uuid", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("time", sa.Integer(), primary_key=True),
        sa.Column("cpu", sa.Float(), nullable=True),
        sa.Column("ram", sa.Integer(), nullable=True),
        sa.Column("ram_total", sa.Integer(), nullable=True),
        sa.Column("swap", sa.Integer(), nullable=True),
        sa.Column("swap_total", sa.Integer(), nullable=True),
        sa.Column("load", sa.Float(), nullable=True),
        sa.Column("disk", sa.Integer(), nullable=True),
        sa.Column("disk_total", sa.Integer(), nullable=True),
        sa.Column("net_in", sa.Integer(), nullable=True),
        sa.Column("net_out", sa.Integer(), nullable=True),
        sa.Column("tcp", sa.Integer(), nullable=True),
        sa.Column("udp", sa.Integer(), nullable=True),
        sa.Column("process", sa.Integer(), nullable=True),
    )

    # ── load_hour ──
    op.create_table(
        "load_hour",
        sa.Column(
            "server_uuid", sa.String(),
            sa.ForeignKey("servers.uuid", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("time", sa.Integer(), primary_key=True),
        sa.Column("cpu", sa.Float(), nullable=True),
        sa.Column("ram", sa.Integer(), nullable=True),
        sa.Column("ram_total", sa.Integer(), nullable=True),
        sa.Column("swap", sa.Integer(), nullable=True),
        sa.Column("swap_total", sa.Integer(), nullable=True),
        sa.Column("load", sa.Float(), nullable=True),
        sa.Column("disk", sa.Integer(), nullable=True),
        sa.Column("disk_total", sa.Integer(), nullable=True),
        sa.Column("net_in", sa.Integer(), nullable=True),
        sa.Column("net_out", sa.Integer(), nullable=True),
        sa.Column("tcp", sa.Integer(), nullable=True),
        sa.Column("udp", sa.Integer(), nullable=True),
        sa.Column("process", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("load_hour")
    op.drop_table("load_minute")
