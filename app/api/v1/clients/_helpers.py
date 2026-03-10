"""客户端路由辅助函数."""

from __future__ import annotations

from app.schemas.clients import GroupRead, ServerBrief


def build_server_brief(server, status_obj=None, groups=None) -> ServerBrief:
    """构建服务器简要信息（含状态和分组）."""
    return ServerBrief(
        uuid=server.uuid,
        name=server.name,
        cpu_name=server.cpu_name,
        arch=server.arch,
        os=server.os,
        region=server.region,
        ipv4=server.ipv4,
        ipv6=server.ipv6,
        version=server.version,
        top=server.top,
        hidden=server.hidden,
        is_approved=server.is_approved,
        created_at=server.created_at,
        status=status_obj.status if status_obj else 0,
        last_online=status_obj.last_online if status_obj else None,
        boot_time=status_obj.boot_time if status_obj else None,
        groups=[GroupRead.model_validate(g) for g in groups] if groups else [],
    )
