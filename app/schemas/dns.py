"""DNS 与 DDNS 的 Pydantic 请求/响应模型."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# 通用
# ═══════════════════════════════════════════════════════════════════════════════

class MessageResponse(BaseModel):
    message: str


# ═══════════════════════════════════════════════════════════════════════════════
# DnsCredential — 凭证
# ═══════════════════════════════════════════════════════════════════════════════

class CredentialCreate(BaseModel):
    """创建 DNS 凭证."""
    name: str = Field(..., min_length=1, max_length=128)
    provider: str = Field(..., min_length=1, max_length=64,
                          description="dns-lexicon 厂商标识，如 cloudflare / aliyun / dnspod")
    credentials: dict = Field(..., description="厂商 auth 参数，如 {\"auth_token\": \"xxx\"}")


class CredentialUpdate(BaseModel):
    """更新 DNS 凭证（所有字段可选）."""
    name: str | None = Field(None, min_length=1, max_length=128)
    credentials: dict | None = Field(None, description="更新 auth 参数")


class CredentialRead(BaseModel):
    """返回给前端的凭证数据（不返回凭证明文）."""
    id: int
    name: str
    provider: str
    is_valid: int
    created_at: int
    updated_at: int

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# DnsDomain — 托管域名
# ═══════════════════════════════════════════════════════════════════════════════

class DomainCreate(BaseModel):
    """创建托管域名."""
    credential_id: int
    domain_name: str = Field(..., min_length=1, max_length=253)
    zone_id: str | None = Field(None, max_length=128,
                                description="厂商区域 ID（CF scoped token / route53 等需要）")


class DomainUpdate(BaseModel):
    """更新托管域名（所有字段可选）."""
    credential_id: int | None = None
    zone_id: str | None = None


class DomainRead(BaseModel):
    """返回给前端的域名数据."""
    id: int
    credential_id: int | None
    domain_name: str
    zone_id: str | None
    sync_status: str
    last_sync_at: int | None
    created_at: int
    updated_at: int

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# DnsRecord — 解析记录
# ═══════════════════════════════════════════════════════════════════════════════

class RecordCreate(BaseModel):
    """手动创建解析记录（通过 API 推送到厂商后写入本地缓存）."""
    name: str = Field(..., min_length=1, max_length=253, description="主机记录，如 @ / www / api")
    type: str = Field(..., description="记录类型: A / AAAA / CNAME / TXT / MX / SRV ...")
    content: str = Field(..., min_length=1, max_length=4096, description="记录值")
    ttl: int = Field(600, ge=1, le=86400)
    priority: int | None = Field(None, ge=0, le=65535, description="MX/SRV 优先级")
    proxied: int = Field(0, ge=0, le=1, description="CF 云朵代理")


class RecordUpdate(BaseModel):
    """更新解析记录."""
    content: str | None = Field(None, min_length=1, max_length=4096)
    ttl: int | None = Field(None, ge=1, le=86400)
    priority: int | None = Field(None, ge=0, le=65535)
    proxied: int | None = Field(None, ge=0, le=1)


class RecordRead(BaseModel):
    """返回给前端的解析记录."""
    id: int
    domain_id: int
    record_id: str
    name: str
    type: str
    content: str
    ttl: int
    priority: int | None = None
    proxied: int
    status: str
    synced_at: int | None

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# DdnsTask — DDNS 任务
# ═══════════════════════════════════════════════════════════════════════════════

class DdnsTaskCreate(BaseModel):
    """创建 DDNS 任务 — 绑定解析记录到监控节点."""
    record_id: int
    server_uuid: str = Field(..., min_length=1)
    ip_version: str = Field("ipv4", description="ipv4 / ipv6")


class DdnsTaskUpdate(BaseModel):
    """更新 DDNS 任务."""
    server_uuid: str | None = Field(None, min_length=1)
    ip_version: str | None = Field(None, description="ipv4 / ipv6")
    is_active: int | None = Field(None, ge=0, le=1)


class DdnsTaskRead(BaseModel):
    """返回给前端的 DDNS 任务."""
    id: int
    record_id: int
    server_uuid: str
    ip_version: str
    last_ip: str | None
    is_active: int
    last_updated: int | None
    last_error: str | None
    error_count: int
    created_at: int
    # 关联字段
    record_name: str | None = None
    record_type: str | None = None
    domain_name: str | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_task(cls, task: object) -> "DdnsTaskRead":
        obj = cls.model_validate(task)
        record = getattr(task, "record", None)
        if record:
            obj.record_name = record.name
            obj.record_type = record.type
            domain = getattr(record, "domain", None)
            if domain:
                obj.domain_name = domain.domain_name
        return obj
