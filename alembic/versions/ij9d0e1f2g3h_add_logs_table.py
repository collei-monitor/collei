"""add logs table

Revision ID: ij9d0e1f2g3h
Revises: hi8c9d0e1f2g
Create Date: 2026-03-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "ij9d0e1f2g3h"
down_revision = "hi8c9d0e1f2g"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "logs" not in existing_tables:
        # 全新建表
        op.create_table(
            "logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("level", sa.String(), nullable=False, server_default=sa.text("'info'")),
            sa.Column("msg_type", sa.String(), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("source", sa.String(), nullable=True),
            sa.Column("ip", sa.String(), nullable=True),
            sa.Column("user_uuid", sa.String(), nullable=True),
            sa.Column("server_uuid", sa.String(), nullable=True),
            sa.Column("time", sa.Integer(), nullable=False),
        )
    else:
        # 表已存在，补齐缺失列
        existing_cols = {c["name"] for c in inspector.get_columns("logs")}
        new_columns = [
            ("level", sa.String(), False, sa.text("'info'")),
            ("detail", sa.Text(), True, None),
            ("source", sa.String(), True, None),
            ("user_uuid", sa.String(), True, None),
            ("server_uuid", sa.String(), True, None),
        ]
        for col_name, col_type, nullable, server_default in new_columns:
            if col_name not in existing_cols:
                op.add_column(
                    "logs",
                    sa.Column(col_name, col_type, nullable=nullable, server_default=server_default),
                )

    # 索引 — 仅在不存在时创建
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("logs")}
    if "ix_logs_time" not in existing_indexes:
        op.create_index("ix_logs_time", "logs", ["time"])
    if "ix_logs_msg_type" not in existing_indexes:
        op.create_index("ix_logs_msg_type", "logs", ["msg_type"])


def downgrade() -> None:
    op.drop_index("ix_logs_msg_type", table_name="logs")
    op.drop_index("ix_logs_time", table_name="logs")
    op.drop_table("logs")
