"""客户端路由辅助函数."""

from __future__ import annotations

from app.schemas.clients import GroupRead, ServerBrief, ServerFullDetail


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


def build_server_full_detail(server, status_obj=None, groups=None) -> ServerFullDetail:
    """构建服务器完整详情（包含所有字段、状态和分组）."""
    return ServerFullDetail(
        uuid=server.uuid,
        name=server.name,
        cpu_name=server.cpu_name,
        virtualization=server.virtualization,
        arch=server.arch,
        cpu_cores=server.cpu_cores,
        os=server.os,
        kernel_version=server.kernel_version,
        ipv4=server.ipv4,
        ipv6=server.ipv6,
        region=server.region,
        mem_total=server.mem_total,
        swap_total=server.swap_total,
        disk_total=server.disk_total,
        version=server.version,
        remark=server.remark,
        top=server.top,
        hidden=server.hidden,
        is_approved=server.is_approved,
        enable_statistics_mode=server.enable_statistics_mode,
        created_at=server.created_at,
        status=status_obj.status if status_obj else 0,
        last_online=status_obj.last_online if status_obj else None,
        boot_time=status_obj.boot_time if status_obj else None,
        current_run_id=status_obj.current_run_id if status_obj else None,
        total_flow_out=status_obj.total_flow_out if status_obj else None,
        total_flow_in=status_obj.total_flow_in if status_obj else None,
        groups=[GroupRead.model_validate(g) for g in groups] if groups else [],
    )
