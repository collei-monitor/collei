"""add_notify_recovery_to_alert_rules

Revision ID: bf2c3d4e5f6b
Revises: af1b2c3d4e5f
Create Date: 2026-03-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'bf2c3d4e5f6b'
down_revision: Union[str, None] = 'af1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'alert_rules',
        sa.Column(
            'notify_recovery', sa.Integer(),
            server_default=sa.text('0'), nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('alert_rules', 'notify_recovery')
