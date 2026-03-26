"""restructure oidc provider table for fastapi-sso

Revision ID: no5p6q7r8s9t
Revises: mn4i5j6k7l8m
Create Date: 2026-03-26 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "no5p6q7r8s9t"
down_revision = "mn4i5j6k7l8m"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("oidc") as batch_op:
        batch_op.add_column(
            sa.Column("provider_type", sa.String(), server_default=sa.text("'google'"), nullable=False))
        batch_op.add_column(
            sa.Column("client_id", sa.String(), server_default=sa.text("''"), nullable=False))
        batch_op.add_column(
            sa.Column("client_secret", sa.String(), server_default=sa.text("''"), nullable=False))
        batch_op.add_column(
            sa.Column("enabled", sa.Integer(), server_default=sa.text("1"), nullable=False))
        batch_op.add_column(
            sa.Column("display_order", sa.Integer(), server_default=sa.text("0"), nullable=False))
        batch_op.add_column(
            sa.Column("scope", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("oidc") as batch_op:
        batch_op.drop_column("scope")
        batch_op.drop_column("display_order")
        batch_op.drop_column("enabled")
        batch_op.drop_column("client_secret")
        batch_op.drop_column("client_id")
        batch_op.drop_column("provider_type")
