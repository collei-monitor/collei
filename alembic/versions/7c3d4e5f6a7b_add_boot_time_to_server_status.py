"""add_boot_time_to_server_status

Revision ID: 7c3d4e5f6a7b
Revises: ae7f9b2df2e7
Create Date: 2026-03-07 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c3d4e5f6a7b'
down_revision: Union[str, None] = '6b2c3d4e5f6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('server_status', schema=None) as batch_op:
        batch_op.add_column(sa.Column('boot_time', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('server_status', schema=None) as batch_op:
        batch_op.drop_column('boot_time')
