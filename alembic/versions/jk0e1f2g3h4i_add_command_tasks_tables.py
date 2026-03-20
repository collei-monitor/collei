"""add command tasks tables

Revision ID: jk0e1f2g3h4i
Revises: ij9d0e1f2g3h
Create Date: 2026-03-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "jk0e1f2g3h4i"
down_revision = "ij9d0e1f2g3h"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tasks ──
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("timeout_sec", sa.Integer(), server_default=sa.text("300")),
        sa.Column("created_at", sa.Integer(), nullable=True),
    )

    # ── task_executions ──
    op.create_table(
        "task_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "task_id", sa.String(36),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id", sa.String(36),
            sa.ForeignKey("servers.uuid", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("dispatched_at", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.Integer(), nullable=True),
    )
    op.create_index("ix_task_executions_task_id", "task_executions", ["task_id"])
    op.create_index("ix_task_executions_agent_id", "task_executions", ["agent_id"])

    # ── task_execution_logs ──
    op.create_table(
        "task_execution_logs",
        sa.Column(
            "execution_id", sa.String(36),
            sa.ForeignKey("task_executions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("output", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("task_execution_logs")
    op.drop_index("ix_task_executions_agent_id", table_name="task_executions")
    op.drop_index("ix_task_executions_task_id", table_name="task_executions")
    op.drop_table("task_executions")
    op.drop_table("tasks")
