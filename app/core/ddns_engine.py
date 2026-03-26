"""DDNS 后台引擎 — 定期检查 IP 变更并推送到 DNS 厂商.

数据流:
  1. 遍历所有 is_active=1 的 ddns_task
  2. 读取关联 Server 的 ipv4/ipv6
  3. 与 ddns_task.last_ip 比较
  4. 如有变更 → 调用 dns_service.update_record() → 更新 last_ip
  5. 记录审计日志
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.audit import audit
from app.core.config_cache import config_cache
from app.crud import dns as dns_crud
from app.db.session import async_session_factory
from app.models.clients import Server
from app.models.dns import DdnsTask, DnsRecord, DnsDomain

logger = logging.getLogger("collei.ddns_engine")


class DdnsEngine:
    """DDNS 后台任务引擎."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run_loop())
        logger.info("DDNS 引擎已启动")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("DDNS 引擎已停止")

    async def _run_loop(self) -> None:
        while True:
            interval = int(config_cache.get("ddns_check_interval") or 60)
            try:
                await self._check_all()
            except Exception as e:
                await audit.error(
                    msg_type="error",
                    message="DDNS 检查任务出错",
                    exc=e,
                    source="ddns_engine",
                )
            await asyncio.sleep(interval)

    async def _check_all(self) -> None:
        """遍历所有活跃 DDNS 任务，检查并推送 IP 变更."""
        from app.core import dns_service

        async with async_session_factory() as db:
            # 加载活跃任务及关联的 record → domain → credential
            result = await db.execute(
                select(DdnsTask)
                .where(DdnsTask.is_active == 1)
                .options(
                    selectinload(DdnsTask.record)
                    .selectinload(DnsRecord.domain)
                    .selectinload(DnsDomain.credential),
                ),
            )
            tasks = result.scalars().all()

            for task in tasks:
                try:
                    await self._process_task(db, task)
                except Exception as e:
                    logger.warning("DDNS task %d 处理失败: %s", task.id, e)
                    await dns_crud.update_ddns_task(
                        db, task.id,
                        last_error=str(e)[:500],
                        error_count=task.error_count + 1,
                    )

            await db.commit()

    async def _process_task(self, db, task: DdnsTask) -> None:
        from app.core import dns_service

        record = task.record
        if not record:
            return
        domain = record.domain
        if not domain or not domain.credential:
            return
        credential = domain.credential
        if not credential.is_valid:
            return

        # 从数据库获取服务器 IP
        result = await db.execute(
            select(Server).where(Server.uuid == task.server_uuid),
        )
        server = result.scalar_one_or_none()
        if not server:
            return

        current_ip = (
            server.ipv4 if task.ip_version == "ipv4" else server.ipv6
        )
        if not current_ip:
            return

        # IP 未变化则跳过
        if current_ip == task.last_ip:
            return

        # 解密凭证
        auth_params = dns_crud.get_decrypted_credentials(credential)

        # 推送到厂商
        try:
            await dns_service.update_record(
                provider=credential.provider,
                domain=domain.domain_name,
                auth_params=auth_params,
                identifier=record.record_id,
                rtype=record.type,
                name=record.name,
                content=current_ip,
                zone_id=domain.zone_id,
            )
        except dns_service.DnsAuthError:
            await dns_crud.mark_credential_invalid(db, credential.id)
            raise
        except dns_service.DnsServiceError:
            raise

        old_ip = task.last_ip
        now = int(time.time())
        # 更新本地状态
        await dns_crud.update_ddns_task(
            db, task.id,
            last_ip=current_ip,
            last_updated=now,
            last_error=None,
            error_count=0,
        )
        await dns_crud.update_record(
            db, record.id,
            content=current_ip,
            synced_at=now,
        )

        # 使用当前 session 写审计日志，避免另开 session 导致 SQLite database locked
        await audit.emit(
            db,
            msg_type="ddns_update",
            message=f"DDNS 更新: {record.name}.{domain.domain_name} → {current_ip}",
            detail=f"task_id={task.id}, server={task.server_uuid}, "
                   f"old_ip={old_ip}, new_ip={current_ip}",
            source="ddns_engine",
        )


ddns_engine = DdnsEngine()
