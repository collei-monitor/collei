"""add dns and ddns tables

Revision ID: mn4i5j6k7l8m
Revises: lm3h4i5j6k7l
Create Date: 2026-03-25 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "mn4i5j6k7l8m"
down_revision = "lm3h4i5j6k7l"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── dns_credentials ──
    op.create_table(
        "dns_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("credentials", sa.Text(), nullable=False),
        sa.Column("is_valid", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── dns_domains ──
    op.create_table(
        "dns_domains",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("credential_id", sa.Integer(), nullable=True),
        sa.Column("domain_name", sa.String(), nullable=False),
        sa.Column("zone_id", sa.String(), nullable=True),
        sa.Column("sync_status", sa.String(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("last_sync_at", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["credential_id"], ["dns_credentials.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain_name"),
    )

    # ── dns_records ──
    op.create_table(
        "dns_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("domain_id", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("ttl", sa.Integer(), server_default=sa.text("600"), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("proxied", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.String(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("synced_at", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["domain_id"], ["dns_domains.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dns_record_domain", "dns_records", ["domain_id"])

    # ── ddns_tasks ──
    op.create_table(
        "ddns_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("server_uuid", sa.String(), nullable=False),
        sa.Column("ip_version", sa.String(), server_default=sa.text("'ipv4'"), nullable=False),
        sa.Column("last_ip", sa.String(), nullable=True),
        sa.Column("is_active", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("last_updated", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("error_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["record_id"], ["dns_records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["server_uuid"], ["servers.uuid"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_id"),
    )


def downgrade() -> None:
    op.drop_table("ddns_tasks")
    op.drop_index("ix_dns_record_domain", table_name="dns_records")
    op.drop_table("dns_records")
    op.drop_table("dns_domains")
    op.drop_table("dns_credentials")
