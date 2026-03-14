"""add network monitoring tables

Revision ID: gh7b8c9d0e1f
Revises: fg6a7b8c9d0e
Create Date: 2026-03-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "gh7b8c9d0e1f"
down_revision = "fg6a7b8c9d0e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── network_targets ──
    op.create_table(
        "network_targets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("host", sa.String(), nullable=False),
        sa.Column("protocol", sa.String(), server_default=sa.text("'icmp'")),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("interval", sa.Integer(), server_default=sa.text("60")),
        sa.Column("enabled", sa.Integer(), server_default=sa.text("1")),
    )

    # ── network_target_dispatch ──
    op.create_table(
        "network_target_dispatch",
        sa.Column(
            "target_id", sa.Integer(),
            sa.ForeignKey("network_targets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("node_type", sa.String(), primary_key=True),
        sa.Column("node_id", sa.String(), primary_key=True),
        sa.Column("is_exclude", sa.Integer(), server_default=sa.text("0")),
    )

    # ── network_status ──
    op.create_table(
        "network_status",
        sa.Column(
            "target_id", sa.Integer(),
            sa.ForeignKey("network_targets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "server_uuid", sa.String(),
            sa.ForeignKey("servers.uuid", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("time", sa.Integer(), primary_key=True),
        sa.Column("median_latency", sa.Float(), nullable=True),
        sa.Column("max_latency", sa.Float(), nullable=True),
        sa.Column("min_latency", sa.Float(), nullable=True),
        sa.Column("packet_loss", sa.Float(), server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_table("network_status")
    op.drop_table("network_target_dispatch")
    op.drop_table("network_targets")
