"""add ssh_scripts table

Revision ID: pq2a3b4c5d6e
Revises: op1a2b3c4d5e
Create Date: 2026-03-28 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "pq2a3b4c5d6e"
down_revision = "op1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ssh_scripts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("language", sa.String(), server_default=sa.text("'bash'"), nullable=True),
        sa.Column("top", sa.Integer(), server_default=sa.text("0"), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("ssh_scripts")
