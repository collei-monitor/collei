"""add_alerts_and_notifications

Revision ID: 9e5f6a7b8c9d
Revises: ae7f9b2df2e7
Create Date: 2026-03-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e5f6a7b8c9d'
down_revision: Union[str, None] = '8d4e5f6a7b8c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── alert_rules ───────────────────────────────────────────────────────
    op.create_table(
        'alert_rules',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('metric', sa.String(), nullable=False),
        sa.Column('condition', sa.String(), nullable=False),
        sa.Column('threshold', sa.Float(), nullable=False),
        sa.Column('duration', sa.Integer(),
                  server_default=sa.text('60'), nullable=False),
        sa.Column('enabled', sa.Integer(),
                  server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── message_sender_providers ──────────────────────────────────────────
    op.create_table(
        'message_sender_providers',
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('addition', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('name'),
    )

    # ── alert_channels ────────────────────────────────────────────────────
    op.create_table(
        'alert_channels',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('provider_name', sa.String(), nullable=True),
        sa.Column('target', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ['provider_name'],
            ['message_sender_providers.name'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── alert_rule_mapping ────────────────────────────────────────────────
    op.create_table(
        'alert_rule_mapping',
        sa.Column('rule_id', sa.Integer(), nullable=False),
        sa.Column('target_type', sa.String(), nullable=False),
        sa.Column('target_id', sa.String(), nullable=False),
        sa.Column('channel_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ['rule_id'], ['alert_rules.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(
            ['channel_id'], ['alert_channels.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('rule_id', 'target_type', 'target_id'),
    )

    # ── alert_history ─────────────────────────────────────────────────────
    op.create_table(
        'alert_history',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('server_uuid', sa.String(), nullable=True),
        sa.Column('rule_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('created_at', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ['server_uuid'], ['servers.uuid'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(
            ['rule_id'], ['alert_rules.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_alert_history_server_rule',
        'alert_history', ['server_uuid', 'rule_id'])

    # ── logs ──────────────────────────────────────────────────────────────
    op.create_table(
        'logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('ip', sa.String(), nullable=True),
        sa.Column('uuid', sa.String(), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('msg_type', sa.String(), nullable=False),
        sa.Column('time', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('logs')
    op.drop_index('ix_alert_history_server_rule',
                  table_name='alert_history')
    op.drop_table('alert_history')
    op.drop_table('alert_rule_mapping')
    op.drop_table('alert_channels')
    op.drop_table('message_sender_providers')
    op.drop_table('alert_rules')
