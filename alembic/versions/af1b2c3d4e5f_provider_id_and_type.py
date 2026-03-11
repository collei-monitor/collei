"""provider_id_and_type

Revision ID: af1b2c3d4e5f
Revises: 9e5f6a7b8c9d
Create Date: 2026-03-11 00:00:00.000000

message_sender_providers: 增加 id (INTEGER PK) 和 type 字段, name 不再是主键
alert_channels: provider_name 改为 provider_id (FK -> message_sender_providers.id)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'af1b2c3d4e5f'
down_revision: Union[str, None] = '9e5f6a7b8c9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite 不支持 ALTER TABLE 修改主键/外键，需要重建表

    # ── 1. 重建 message_sender_providers ──────────────────────────────────
    op.rename_table('message_sender_providers', '_old_message_sender_providers')

    op.create_table(
        'message_sender_providers',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('type', sa.String(), nullable=True),
        sa.Column('addition', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # 迁移数据: 旧表只有 name + addition
    op.execute(
        "INSERT INTO message_sender_providers (name, addition) "
        "SELECT name, addition FROM _old_message_sender_providers"
    )

    # ── 2. 重建 alert_channels (provider_name -> provider_id) ─────────────
    op.rename_table('alert_channels', '_old_alert_channels')

    op.create_table(
        'alert_channels',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('provider_id', sa.Integer(), nullable=True),
        sa.Column('target', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ['provider_id'],
            ['message_sender_providers.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
    )

    # 迁移数据: 通过 name 关联回新表的 id
    op.execute(
        "INSERT INTO alert_channels (id, name, provider_id, target) "
        "SELECT oc.id, oc.name, np.id, oc.target "
        "FROM _old_alert_channels oc "
        "LEFT JOIN message_sender_providers np ON oc.provider_name = np.name"
    )

    # ── 3. 重建 alert_rule_mapping (因外键指向 alert_channels) ────────────
    op.rename_table('alert_rule_mapping', '_old_alert_rule_mapping')

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

    op.execute(
        "INSERT INTO alert_rule_mapping (rule_id, target_type, target_id, channel_id) "
        "SELECT rule_id, target_type, target_id, channel_id "
        "FROM _old_alert_rule_mapping"
    )

    # ── 4. 清理旧表 ──────────────────────────────────────────────────────
    op.drop_table('_old_alert_rule_mapping')
    op.drop_table('_old_alert_channels')
    op.drop_table('_old_message_sender_providers')


def downgrade() -> None:
    # ── 1. 恢复 message_sender_providers (name 作为 PK) ──────────────────
    op.rename_table('message_sender_providers', '_new_message_sender_providers')

    op.create_table(
        'message_sender_providers',
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('addition', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('name'),
    )

    op.execute(
        "INSERT INTO message_sender_providers (name, addition) "
        "SELECT name, addition FROM _new_message_sender_providers"
    )

    # ── 2. 恢复 alert_channels (provider_id -> provider_name) ─────────────
    op.rename_table('alert_channels', '_new_alert_channels')

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

    op.execute(
        "INSERT INTO alert_channels (id, name, provider_name, target) "
        "SELECT nc.id, nc.name, np.name, nc.target "
        "FROM _new_alert_channels nc "
        "LEFT JOIN _new_message_sender_providers np ON nc.provider_id = np.id"
    )

    # ── 3. 恢复 alert_rule_mapping ────────────────────────────────────────
    op.rename_table('alert_rule_mapping', '_new_alert_rule_mapping')

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

    op.execute(
        "INSERT INTO alert_rule_mapping (rule_id, target_type, target_id, channel_id) "
        "SELECT rule_id, target_type, target_id, channel_id "
        "FROM _new_alert_rule_mapping"
    )

    # ── 4. 清理 ──────────────────────────────────────────────────────────
    op.drop_table('_new_alert_rule_mapping')
    op.drop_table('_new_alert_channels')
    op.drop_table('_new_message_sender_providers')
