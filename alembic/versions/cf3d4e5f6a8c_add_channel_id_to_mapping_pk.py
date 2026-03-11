"""add_channel_id_to_mapping_pk

Revision ID: cf3d4e5f6a8c
Revises: bf2c3d4e5f6b
Create Date: 2026-03-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cf3d4e5f6a8c'
down_revision: Union[str, None] = 'bf2c3d4e5f6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite 不支持 ALTER TABLE 修改主键，需要通过重建表实现
    op.execute("""
        CREATE TABLE alert_rule_mapping_new (
            rule_id    INTEGER NOT NULL,
            target_type TEXT   NOT NULL,
            target_id   TEXT   NOT NULL,
            channel_id  INTEGER NOT NULL,
            PRIMARY KEY (rule_id, target_type, target_id, channel_id),
            FOREIGN KEY (rule_id)    REFERENCES alert_rules(id)    ON DELETE CASCADE,
            FOREIGN KEY (channel_id) REFERENCES alert_channels(id) ON DELETE CASCADE
        )
    """)
    op.execute("""
        INSERT INTO alert_rule_mapping_new (rule_id, target_type, target_id, channel_id)
        SELECT rule_id, target_type, target_id, channel_id
        FROM alert_rule_mapping
    """)
    op.drop_table('alert_rule_mapping')
    op.rename_table('alert_rule_mapping_new', 'alert_rule_mapping')


def downgrade() -> None:
    op.execute("""
        CREATE TABLE alert_rule_mapping_old (
            rule_id    INTEGER NOT NULL,
            target_type TEXT   NOT NULL,
            target_id   TEXT   NOT NULL,
            channel_id  INTEGER,
            PRIMARY KEY (rule_id, target_type, target_id),
            FOREIGN KEY (rule_id)    REFERENCES alert_rules(id)    ON DELETE CASCADE,
            FOREIGN KEY (channel_id) REFERENCES alert_channels(id) ON DELETE CASCADE
        )
    """)
    op.execute("""
        INSERT OR IGNORE INTO alert_rule_mapping_old (rule_id, target_type, target_id, channel_id)
        SELECT rule_id, target_type, target_id, channel_id
        FROM alert_rule_mapping
    """)
    op.drop_table('alert_rule_mapping')
    op.rename_table('alert_rule_mapping_old', 'alert_rule_mapping')
