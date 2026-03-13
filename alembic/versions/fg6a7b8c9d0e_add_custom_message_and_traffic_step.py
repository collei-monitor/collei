"""add custom_message and traffic_notify_step to alert_rules

Revision ID: fg6a7b8c9d0e
Revises: ef5a6b7c8d9e
Create Date: 2025-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "fg6a7b8c9d0e"
down_revision = "ef5a6b7c8d9e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("alert_rules") as batch_op:
        batch_op.add_column(
            sa.Column("custom_message", sa.Text(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("traffic_notify_step", sa.Float(), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("alert_rules") as batch_op:
        batch_op.drop_column("traffic_notify_step")
        batch_op.drop_column("custom_message")
