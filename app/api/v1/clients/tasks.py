"""远程命令执行管理 API 路由（需管理员登录）.

端点:
  POST   /clients/tasks                           创建任务并下发到指定服务器
  GET    /clients/tasks                           获取任务列表
  GET    /clients/tasks/{task_id}                 获取任务详情（含执行记录）
  DELETE /clients/tasks/{task_id}                 删除任务
  GET    /clients/tasks/{task_id}/executions      获取任务的所有执行记录
  GET    /clients/tasks/executions/{execution_id} 获取执行记录详情（含日志）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.server_cache import server_cache
from app.crud import task as crud_task
from app.crud import clients as crud_clients
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.task import (
    MessageResponse,
    TaskCreate,
    TaskCreateResponse,
    TaskDetail,
    TaskExecutionDetail,
    TaskExecutionRead,
    TaskExecutionUpdate,
    TaskRead,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ═══════════════════════════════════════════════════════════════════════════════
# 任务管理 (管理员)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("", response_model=TaskCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: TaskCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建任务并为每个目标服务器创建执行记录.

    前端提交任务后，执行记录状态为 pending；Agent 下次上报时将拉取到该任务。
    """
    # 校验目标服务器存在且已批准
    for agent_id in body.agent_ids:
        server = await crud_clients.get_server_by_uuid(db, agent_id)
        if not server:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Server '{agent_id}' not found",
            )
        if server.is_approved != 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Server '{agent_id}' is not approved",
            )

    # 创建任务
    task = await crud_task.create_task(
        db,
        type=body.type,
        payload=body.payload,
        timeout_sec=body.timeout_sec,
    )

    # 批量创建执行记录
    executions = await crud_task.batch_create_executions(
        db,
        task_id=task.id,
        agent_ids=body.agent_ids,
    )

    return TaskCreateResponse(
        task=TaskRead.model_validate(task),
        executions=[TaskExecutionRead.model_validate(e) for e in executions],
    )


@router.get("", response_model=list[TaskRead])
async def list_tasks(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取任务列表（按创建时间倒序）."""
    return await crud_task.get_all_tasks(db, limit=limit, offset=offset)


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task_detail(
    task_id: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取任务详情，包含所有执行记录."""
    task = await crud_task.get_task_with_executions(db, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return TaskDetail(
        id=task.id,
        type=task.type,
        payload=task.payload,
        timeout_sec=task.timeout_sec,
        created_at=task.created_at,
        executions=[TaskExecutionRead.model_validate(e) for e in task.executions],
    )


@router.delete("/{task_id}", response_model=MessageResponse)
async def delete_task(
    task_id: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除任务及其所有执行记录和日志."""
    deleted = await crud_task.delete_task(db, task_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return MessageResponse(message="Task deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# 执行记录查看 (管理员)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/{task_id}/executions", response_model=list[TaskExecutionRead])
async def list_task_executions(
    task_id: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取某个任务的所有执行记录."""
    task = await crud_task.get_task(db, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return await crud_task.get_executions_by_task(db, task_id)


@router.get("/executions/{execution_id}", response_model=TaskExecutionDetail)
async def get_execution_detail(
    execution_id: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取单条执行记录详情（含终端输出日志）."""
    execution = await crud_task.get_execution_with_log(db, execution_id)
    if not execution:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Execution not found")
    return TaskExecutionDetail(
        id=execution.id,
        task_id=execution.task_id,
        agent_id=execution.agent_id,
        status=execution.status,
        exit_code=execution.exit_code,
        dispatched_at=execution.dispatched_at,
        completed_at=execution.completed_at,
        output=execution.log.output if execution.log else None,
    )


@router.put("/executions/{execution_id}", response_model=TaskExecutionRead)
async def update_execution(
    execution_id: str,
    body: TaskExecutionUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """管理员手动更新执行记录状态（用于超时或异常处理）."""
    execution = await crud_task.get_execution(db, execution_id)
    if not execution:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Execution not found")

    import time
    completed_at = int(time.time()) if body.status in ("success", "failed", "timeout") else None
    updated = await crud_task.update_execution_status(
        db, execution_id,
        status=body.status,
        exit_code=body.exit_code,
        completed_at=completed_at,
    )
    if body.output is not None:
        await crud_task.upsert_execution_log(db, execution_id, body.output)
    return updated
