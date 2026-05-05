"""SQLite 写操作重试工具，缓解高并发下的锁冲突."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError

T = TypeVar("T")


def _is_sqlite_locked_error(exc: OperationalError) -> bool:
    text = str(exc).lower()
    return "database is locked" in text or "database table is locked" in text


async def run_with_sqlite_lock_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    retries: int = 5,
    initial_delay: float = 0.05,
    max_delay: float = 0.5,
) -> T:
    """执行 SQLite 写操作，遇到锁冲突时按指数退避重试."""
    delay = initial_delay
    for attempt in range(retries + 1):
        try:
            return await operation()
        except OperationalError as exc:
            if attempt >= retries or not _is_sqlite_locked_error(exc):
                raise
            await asyncio.sleep(delay)
            delay = min(max_delay, delay * 2)

    raise RuntimeError("unreachable")
