"""DNS 厂商 API 服务 — 基于 dns-lexicon 的统一封装.

提供对 89+ DNS 厂商的统一接口:
  - list_records()    同步域名下所有解析记录
  - create_record()   创建解析记录
  - update_record()   更新解析记录
  - delete_record()   删除解析记录

lexicon 是同步库，所有调用通过 asyncio.to_thread() 桥接为异步。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, NoReturn

from lexicon.client import Client
from lexicon.config import ConfigResolver

logger = logging.getLogger("collei.dns_service")


class DnsServiceError(Exception):
    """DNS 操作失败."""


class DnsAuthError(DnsServiceError):
    """认证失败（凭证无效）."""


def _build_config(
    provider: str,
    domain: str,
    auth_params: dict,
    zone_id: str | None = None,
) -> ConfigResolver:
    """构建 lexicon ConfigResolver."""
    provider_opts: dict[str, Any] = {**auth_params}
    if zone_id:
        provider_opts["zone_id"] = zone_id

    return ConfigResolver().with_dict({
        "provider_name": provider,
        "domain": domain,
        provider: provider_opts,
    })


def _sync_list_records(
    config: ConfigResolver,
    rtype: str | None = None,
    name: str | None = None,
) -> list[dict]:
    """同步版 — 列出解析记录."""
    with Client(config) as ops:
        return ops.list_records(rtype or "A", name)


def _sync_create_record(
    config: ConfigResolver,
    rtype: str,
    name: str,
    content: str,
) -> bool:
    """同步版 — 创建解析记录."""
    with Client(config) as ops:
        return ops.create_record(rtype, name, content)


def _sync_update_record(
    config: ConfigResolver,
    identifier: str,
    rtype: str,
    name: str,
    content: str,
) -> bool:
    """同步版 — 更新解析记录."""
    with Client(config) as ops:
        return ops.update_record(identifier, rtype, name, content)


def _sync_delete_record(
    config: ConfigResolver,
    identifier: str,
    rtype: str | None = None,
    name: str | None = None,
    content: str | None = None,
) -> bool:
    """同步版 — 删除解析记录."""
    with Client(config) as ops:
        return ops.delete_record(identifier, rtype, name, content)


# ─── 异步公开接口 ─────────────────────────────────────────────────────────────

async def list_records(
    provider: str,
    domain: str,
    auth_params: dict,
    zone_id: str | None = None,
    rtype: str | None = None,
    name: str | None = None,
) -> list[dict]:
    """列出域名下的解析记录.

    Returns:
        包含 record_id, type, name, content, ttl 等的字典列表.

    Raises:
        DnsAuthError: 认证失败.
        DnsServiceError: 其他 API 错误.
    """
    config = _build_config(provider, domain, auth_params, zone_id)
    try:
        return await asyncio.to_thread(_sync_list_records, config, rtype, name)
    except Exception as e:
        _raise_typed(e)


async def create_record(
    provider: str,
    domain: str,
    auth_params: dict,
    rtype: str,
    name: str,
    content: str,
    zone_id: str | None = None,
) -> bool:
    """在厂商侧创建解析记录."""
    config = _build_config(provider, domain, auth_params, zone_id)
    try:
        return await asyncio.to_thread(
            _sync_create_record, config, rtype, name, content,
        )
    except Exception as e:
        _raise_typed(e)


async def update_record(
    provider: str,
    domain: str,
    auth_params: dict,
    identifier: str,
    rtype: str,
    name: str,
    content: str,
    zone_id: str | None = None,
) -> bool:
    """在厂商侧更新解析记录."""
    config = _build_config(provider, domain, auth_params, zone_id)
    try:
        return await asyncio.to_thread(
            _sync_update_record, config, identifier, rtype, name, content,
        )
    except Exception as e:
        _raise_typed(e)


async def delete_record(
    provider: str,
    domain: str,
    auth_params: dict,
    identifier: str,
    zone_id: str | None = None,
    rtype: str | None = None,
    name: str | None = None,
    content: str | None = None,
) -> bool:
    """在厂商侧删除解析记录."""
    config = _build_config(provider, domain, auth_params, zone_id)
    try:
        return await asyncio.to_thread(
            _sync_delete_record, config, identifier, rtype, name, content,
        )
    except Exception as e:
        _raise_typed(e)


def _raise_typed(e: Exception) -> NoReturn:
    """将 lexicon 异常转为类型化异常."""
    msg = str(e)
    if "401" in msg or "403" in msg or "authentication" in msg.lower():
        raise DnsAuthError(msg) from e
    raise DnsServiceError(msg) from e
