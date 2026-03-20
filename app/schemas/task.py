"""远程命令执行相关的 Pydantic 请求/响应模型."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# 通用响应
# ═══════════════════════════════════════════════════════════════════════════════

class MessageResponse(BaseModel):
    message: str


# ═══════════════════════════════════════════════════════════════════════════════
# Task — 任务定义
# ═══════════════════════════════════════════════════════════════════════════════

class TaskCreate(BaseModel):
    """创建任务请求."""
    type: str = Field(
        ..., min_length=1, max_length=50,
        description="任务类型: shell / command / script / upgrade_agent",
    )
    payload: str = Field(
        ..., min_length=1,
        description='任务参数 JSON 字符串，如 {"command": "apt update -y"}',
    )
    timeout_sec: int = Field(
        300, ge=1, le=86400,
        description="任务超时时间 (秒)，默认 300",
    )
    agent_ids: list[str] = Field(
        ..., min_length=1,
        description="目标服务器 UUID 列表（至少一个）",
    )


class TaskRead(BaseModel):
    """任务基本信息."""
    id: str
    type: str
    payload: str
    timeout_sec: int
    created_at: int | None = None

    model_config = {"from_attributes": True}


class TaskDetail(BaseModel):
    """任务详情（包含所有执行记录）."""
    id: str
    type: str
    payload: str
    timeout_sec: int
    created_at: int | None = None
    executions: list[TaskExecutionRead] = []

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# TaskExecution — 执行记录
# ═══════════════════════════════════════════════════════════════════════════════

class TaskExecutionRead(BaseModel):
    """执行记录基本信息."""
    id: str
    task_id: str
    agent_id: str
    status: str
    exit_code: int | None = None
    dispatched_at: int | None = None
    completed_at: int | None = None

    model_config = {"from_attributes": True}


class TaskExecutionDetail(BaseModel):
    """执行记录详情（含日志输出）."""
    id: str
    task_id: str
    agent_id: str
    status: str
    exit_code: int | None = None
    dispatched_at: int | None = None
    completed_at: int | None = None
    output: str | None = None

    model_config = {"from_attributes": True}


class TaskExecutionUpdate(BaseModel):
    """Agent 上报执行状态更新."""
    status: str = Field(
        ...,
        description="状态: running / success / failed / timeout",
    )
    exit_code: int | None = Field(None, description="进程退出码")
    output: str | None = Field(None, description="终端输出 (stdout/stderr)")


# ═══════════════════════════════════════════════════════════════════════════════
# Task 创建响应
# ═══════════════════════════════════════════════════════════════════════════════

class TaskCreateResponse(BaseModel):
    """创建任务后返回任务 ID 和各执行记录."""
    task: TaskRead
    executions: list[TaskExecutionRead]


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 侧 — 任务下发 / 结果上报
# ═══════════════════════════════════════════════════════════════════════════════

class AgentPendingTask(BaseModel):
    """下发给 Agent 的待执行任务."""
    execution_id: str = Field(..., description="执行记录 ID")
    task_id: str = Field(..., description="任务 ID")
    type: str = Field(..., description="任务类型")
    payload: str = Field(..., description="任务参数 JSON")
    timeout_sec: int = Field(..., description="超时时间 (秒)")


class AgentTaskReport(BaseModel):
    """Agent 上报任务执行结果."""
    execution_id: str = Field(..., description="执行记录 ID")
    status: str = Field(
        ...,
        description="状态: running / success / failed / timeout",
    )
    exit_code: int | None = Field(None, description="进程退出码")
    output: str | None = Field(None, description="终端输出 (stdout/stderr)")
