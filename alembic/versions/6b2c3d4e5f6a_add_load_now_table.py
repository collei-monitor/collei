"""add_load_now_table

Revision ID: 6b2c3d4e5f6a
Revises: 5a1b2c3d4e5f
Create Date: 2026-03-05 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6b2c3d4e5f6a'
down_revision: Union[str, None] = '5a1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'load_now',
        sa.Column('server_uuid', sa.String(), sa.ForeignKey('servers.uuid', ondelete='CASCADE'), nullable=False),
        sa.Column('time', sa.Integer(), nullable=False),
        sa.Column('cpu', sa.Float(), nullable=True),
        sa.Column('ram', sa.Integer(), nullable=True),
        sa.Column('ram_total', sa.Integer(), nullable=True),
        sa.Column('swap', sa.Integer(), nullable=True),
        sa.Column('swap_total', sa.Integer(), nullable=True),
        sa.Column('load', sa.Float(), nullable=True),
        sa.Column('disk', sa.Integer(), nullable=True),
        sa.Column('disk_total', sa.Integer(), nullable=True),
        sa.Column('net_in', sa.Integer(), nullable=True),
        sa.Column('net_out', sa.Integer(), nullable=True),
        sa.Column('tcp', sa.Integer(), nullable=True),
        sa.Column('udp', sa.Integer(), nullable=True),
        sa.Column('process', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('server_uuid', 'time'),
    )
    # 按 server_uuid 查询时加速
    op.create_index('ix_load_now_server_uuid', 'load_now', ['server_uuid'])


def downgrade() -> None:
    op.drop_index('ix_load_now_server_uuid', table_name='load_now')
    op.drop_table('load_now')
