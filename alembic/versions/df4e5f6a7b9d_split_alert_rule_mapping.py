"""split_alert_rule_mapping_into_targets_and_channels

Revision ID: df4e5f6a7b9d
Revises: cf3d4e5f6a8c
Create Date: 2026-03-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "df4e5f6a7b9d"
down_revision = "cf3d4e5f6a8c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 创建 alert_rule_targets 表
    op.create_table(
        "alert_rule_targets",
        sa.Column("rule_id", sa.Integer, sa.ForeignKey("alert_rules.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("target_type", sa.String, primary_key=True),
        sa.Column("target_id", sa.String, primary_key=True),
        sa.Column("is_exclude", sa.Integer, server_default=sa.text("0"), nullable=False),
    )

    # 2) 创建 alert_rule_channels 表
    op.create_table(
        "alert_rule_channels",
        sa.Column("rule_id", sa.Integer, sa.ForeignKey("alert_rules.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("channel_id", sa.Integer, sa.ForeignKey("alert_channels.id", ondelete="CASCADE"), primary_key=True),
    )

    # 3) 迁移数据: 从旧表拆分到两张新表
    op.execute(
        "INSERT OR IGNORE INTO alert_rule_targets (rule_id, target_type, target_id, is_exclude) "
        "SELECT DISTINCT rule_id, target_type, target_id, 0 FROM alert_rule_mapping"
    )
    op.execute(
        "INSERT OR IGNORE INTO alert_rule_channels (rule_id, channel_id) "
        "SELECT DISTINCT rule_id, channel_id FROM alert_rule_mapping"
    )

    # 4) 删除旧表
    op.drop_table("alert_rule_mapping")


def downgrade() -> None:
    # 1) 重建旧表
    op.create_table(
        "alert_rule_mapping",
        sa.Column("rule_id", sa.Integer, sa.ForeignKey("alert_rules.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("target_type", sa.String, primary_key=True),
        sa.Column("target_id", sa.String, primary_key=True),
        sa.Column("channel_id", sa.Integer, sa.ForeignKey("alert_channels.id", ondelete="CASCADE"), primary_key=True),
    )

    # 2) 从两张新表还原 (笛卡尔积)
    op.execute(
        "INSERT OR IGNORE INTO alert_rule_mapping (rule_id, target_type, target_id, channel_id) "
        "SELECT t.rule_id, t.target_type, t.target_id, c.channel_id "
        "FROM alert_rule_targets t "
        "JOIN alert_rule_channels c ON t.rule_id = c.rule_id"
    )

    # 3) 删除新表
    op.drop_table("alert_rule_channels")
    op.drop_table("alert_rule_targets")
