"""add_billing_traffic_and_flow

Revision ID: 8d4e5f6a7b8c
Revises: 7c3d4e5f6a7b
Create Date: 2026-03-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8d4e5f6a7b8c'
down_revision: Union[str, None] = '7c3d4e5f6a7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # servers 表新增 enable_statistics_mode 字段
    with op.batch_alter_table('servers', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('enable_statistics_mode', sa.Integer(),
                       server_default=sa.text('0'), nullable=False))

    # server_status 表新增 total_flow_out / total_flow_in 字段
    with op.batch_alter_table('server_status', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('total_flow_out', sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column('total_flow_in', sa.Integer(), nullable=True))

    # 新建 server_billing_rules 表
    op.create_table(
        'server_billing_rules',
        sa.Column('uuid', sa.String(),
                   sa.ForeignKey('servers.uuid', ondelete='CASCADE'),
                   nullable=False),
        sa.Column('billing_cycle', sa.Integer(), nullable=True),
        sa.Column('billing_cycle_data', sa.Integer(), nullable=True),
        sa.Column('billing_cycle_cost', sa.Float(), nullable=True),
        sa.Column('traffic_reset_day', sa.Integer(), nullable=True),
        sa.Column('traffic_threshold', sa.Integer(), nullable=True),
        sa.Column('accounting_mode', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('uuid'),
    )

    # 新建 traffic_hourly_stats 表
    op.create_table(
        'traffic_hourly_stats',
        sa.Column('server_uuid', sa.String(),
                   sa.ForeignKey('servers.uuid', ondelete='CASCADE'),
                   nullable=False),
        sa.Column('time', sa.Integer(), nullable=False),
        sa.Column('net_in', sa.Integer(),
                   server_default=sa.text('0'), nullable=False),
        sa.Column('net_out', sa.Integer(),
                   server_default=sa.text('0'), nullable=False),
        sa.PrimaryKeyConstraint('server_uuid', 'time'),
    )
    op.create_index(
        'ix_traffic_hourly_stats_server_uuid',
        'traffic_hourly_stats', ['server_uuid'])


def downgrade() -> None:
    op.drop_index('ix_traffic_hourly_stats_server_uuid',
                  table_name='traffic_hourly_stats')
    op.drop_table('traffic_hourly_stats')
    op.drop_table('server_billing_rules')

    with op.batch_alter_table('server_status', schema=None) as batch_op:
        batch_op.drop_column('total_flow_in')
        batch_op.drop_column('total_flow_out')

    with op.batch_alter_table('servers', schema=None) as batch_op:
        batch_op.drop_column('enable_statistics_mode')
