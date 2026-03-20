"""远程命令执行的 CRUD / DAO 操作."""

from __future__ import annotations

import time
from typing import Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.task import Task, TaskExecution, TaskExecutionLog


# ═══════════════════════════════════════════════════════════════════════════════
# Task — 任务定义
# ═══════════════════════════════════════════════════════════════════════════════

async def create_task(
    db: AsyncSession,
    *,
    type: str,
    payload: str,
    timeout_sec: int = 300,
) -> Task:
    """创建一条任务记录."""
    task = Task(type=type, payload=payload, timeout_sec=timeout_sec)
    db.add(task)
    await db.flush()
    return task


async def get_task(db: AsyncSession, task_id: str) -> Task | None:
    """根据 ID 获取任务（不含执行记录）."""
    result = await db.execute(select(Task).where(Task.id == task_id))
    return result.scalar_one_or_none()


async def get_task_with_executions(db: AsyncSession, task_id: str) -> Task | None:
    """根据 ID 获取任务（含执行记录）."""
    result = await db.execute(
        select(Task)
        .where(Task.id == task_id)
        .options(selectinload(Task.executions)),
    )
    return result.scalar_one_or_none()


async def get_all_tasks(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Task]:
    """获取任务列表（按创建时间倒序）."""
    stmt = (
        select(Task)
        .order_by(Task.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def delete_task(db: AsyncSession, task_id: str) -> bool:
    """删除任务（级联删除执行记录和日志）."""
    result = await db.execute(delete(Task).where(Task.id == task_id))
    await db.flush()
    return (result.rowcount or 0) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# TaskExecution — 执行记录
# ═══════════════════════════════════════════════════════════════════════════════

async def create_execution(
    db: AsyncSession,
    *,
    task_id: str,
    agent_id: str,
) -> TaskExecution:
    """为指定任务创建一条执行记录（状态为 pending）."""
    execution = TaskExecution(task_id=task_id, agent_id=agent_id)
    db.add(execution)
    await db.flush()
    return execution


async def batch_create_executions(
    db: AsyncSession,
    *,
    task_id: str,
    agent_ids: list[str],
) -> list[TaskExecution]:
    """为指定任务批量创建执行记录."""
    executions = []
    for agent_id in agent_ids:
        execution = TaskExecution(task_id=task_id, agent_id=agent_id)
        db.add(execution)
        executions.append(execution)
    await db.flush()
    return executions


async def get_execution(db: AsyncSession, execution_id: str) -> TaskExecution | None:
    """根据 ID 获取单条执行记录."""
    result = await db.execute(
        select(TaskExecution).where(TaskExecution.id == execution_id),
    )
    return result.scalar_one_or_none()


async def get_execution_with_log(db: AsyncSession, execution_id: str) -> TaskExecution | None:
    """根据 ID 获取执行记录（含日志）."""
    result = await db.execute(
        select(TaskExecution)
        .where(TaskExecution.id == execution_id)
        .options(selectinload(TaskExecution.log)),
    )
    return result.scalar_one_or_none()


async def get_executions_by_task(
    db: AsyncSession,
    task_id: str,
) -> Sequence[TaskExecution]:
    """获取某个任务的所有执行记录."""
    result = await db.execute(
        select(TaskExecution)
        .where(TaskExecution.task_id == task_id)
        .order_by(TaskExecution.dispatched_at.desc().nullslast()),
    )
    return result.scalars().all()


async def get_pending_executions_for_agent(
    db: AsyncSession,
    agent_id: str,
) -> Sequence[TaskExecution]:
    """获取某个 Agent 所有待执行（pending）的任务执行记录."""
    result = await db.execute(
        select(TaskExecution)
        .where(
            TaskExecution.agent_id == agent_id,
            TaskExecution.status == "pending",
        )
        .options(selectinload(TaskExecution.task)),
    )
    return result.scalars().all()


async def update_execution_status(
    db: AsyncSession,
    execution_id: str,
    *,
    status: str,
    exit_code: int | None = None,
    dispatched_at: int | None = None,
    completed_at: int | None = None,
) -> TaskExecution | None:
    """更新执行记录的状态."""
    values: dict = {"status": status}
    if exit_code is not None:
        values["exit_code"] = exit_code
    if dispatched_at is not None:
        values["dispatched_at"] = dispatched_at
    if completed_at is not None:
        values["completed_at"] = completed_at
    await db.execute(
        update(TaskExecution)
        .where(TaskExecution.id == execution_id)
        .values(**values),
    )
    await db.flush()
    return await get_execution(db, execution_id)


async def mark_execution_dispatched(
    db: AsyncSession,
    execution_id: str,
) -> TaskExecution | None:
    """将执行记录标记为已下发（sent）."""
    return await update_execution_status(
        db, execution_id,
        status="sent",
        dispatched_at=int(time.time()),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TaskExecutionLog — 执行日志
# ═══════════════════════════════════════════════════════════════════════════════

async def upsert_execution_log(
    db: AsyncSession,
    execution_id: str,
    output: str | None,
) -> TaskExecutionLog:
    """创建或更新执行日志（追加输出内容）."""
    result = await db.execute(
        select(TaskExecutionLog)
        .where(TaskExecutionLog.execution_id == execution_id),
    )
    log = result.scalar_one_or_none()
    if log is None:
        log = TaskExecutionLog(execution_id=execution_id, output=output or "")
        db.add(log)
    else:
        if output:
            log.output = (log.output or "") + output
    await db.flush()
    return log


async def get_execution_log(
    db: AsyncSession,
    execution_id: str,
) -> TaskExecutionLog | None:
    """获取执行日志."""
    result = await db.execute(
        select(TaskExecutionLog)
        .where(TaskExecutionLog.execution_id == execution_id),
    )
    return result.scalar_one_or_none()
