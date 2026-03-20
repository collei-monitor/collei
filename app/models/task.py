"""远程命令执行相关的 SQLAlchemy 模型.

对应数据库文档 § 6 — Command Tasks:
  tasks                任务定义表
  task_executions      任务执行状态表
  task_execution_logs  任务执行日志表
"""

import time
import uuid as _uuid

from sqlalchemy import ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


# ─── helpers ──────────────────────────────────────────────────────────────────

def _gen_uuid() -> str:
    return str(_uuid.uuid4())


def _now() -> int:
    return int(time.time())


# ─── Tasks ────────────────────────────────────────────────────────────────────

class Task(Base):
    """任务定义表 — 记录用户的操作意图."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_gen_uuid,
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    timeout_sec: Mapped[int] = mapped_column(
        Integer, default=300, server_default=text("300"),
    )
    created_at: Mapped[int] = mapped_column(Integer, default=_now)

    # ── 关系 ──
    executions: Mapped[list["TaskExecution"]] = relationship(
        "TaskExecution",
        back_populates="task",
        cascade="all, delete-orphan",
    )


# ─── Task Executions ─────────────────────────────────────────────────────────

class TaskExecution(Base):
    """任务执行状态表 — 专注于任务调度与状态流转."""

    __tablename__ = "task_executions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_gen_uuid,
    )
    task_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.uuid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default=text("'pending'"),
        nullable=False,
    )
    exit_code: Mapped[int | None] = mapped_column(Integer)
    dispatched_at: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[int | None] = mapped_column(Integer)

    # ── 关系 ──
    task: Mapped["Task"] = relationship("Task", back_populates="executions")
    log: Mapped["TaskExecutionLog | None"] = relationship(
        "TaskExecutionLog",
        back_populates="execution",
        uselist=False,
        cascade="all, delete-orphan",
    )


# ─── Task Execution Logs ─────────────────────────────────────────────────────

class TaskExecutionLog(Base):
    """任务执行日志表 — 存储大段文本日志数据."""

    __tablename__ = "task_execution_logs"

    execution_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("task_executions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    output: Mapped[str | None] = mapped_column(Text)

    # ── 关系 ──
    execution: Mapped["TaskExecution"] = relationship(
        "TaskExecution", back_populates="log",
    )
