"""IP 归属国家查询工具.

使用本地 MaxMind DB (.mmdb) 文件解析 IP 对应的 ISO 3166-1 alpha-2 国家代码.

支持的数据库文件（data/ 目录下）:
  - GeoLite2  →  GeoLite2.mmdb
  - MaxMind   →  MaxMind.mmdb

配置项: configs 表中 key="ip_db", value 为上述名称之一（默认 GeoLite2）.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import maxminddb

logger = logging.getLogger(__name__)

# data/ 目录（相对于项目根路径）
_DATA_DIR = Path(__file__).parent.parent.parent / "data"

# 可用数据库文件映射
DB_FILES: dict[str, Path] = {
    "GeoLite2": _DATA_DIR / "GeoLite2.mmdb",
    "MaxMind": _DATA_DIR / "MaxMind.mmdb",
}

# 默认数据库
DEFAULT_DB = "GeoLite2"


def _lookup_sync(ip: str, db_name: str) -> str | None:
    """同步查询 IP 归属国家代码（在线程池中运行）."""
    db_path = DB_FILES.get(db_name)
    if not db_path:
        logger.warning("未知的 IP 数据库: %s", db_name)
        return None
    if not db_path.exists():
        logger.warning("IP 数据库文件不存在: %s", db_path)
        return None
    try:
        with maxminddb.open_database(str(db_path)) as reader:
            record = reader.get(ip)
            if isinstance(record, dict):
                country = record.get("country")
                if isinstance(country, dict):
                    code = country.get("iso_code")
                    if isinstance(code, str):
                        return code
    except Exception as exc:  # noqa: BLE001
        logger.warning("IP 查询失败 [%s]: %s", ip, exc)
    return None


# 争议地区代码重映射表
_DISPUTED_REMAP: dict[str, str] = {
    "TW": "CN",
}


def remap_region(code: str | None, disputed_territory_enabled: bool) -> str | None:
    """根据争议地区设置重映射国家代码."""
    if code and disputed_territory_enabled:
        return _DISPUTED_REMAP.get(code, code)
    return code


async def lookup_country(ip: str | None, db_name: str = DEFAULT_DB) -> str | None:
    """异步查询 IP 归属国家代码.

    Args:
        ip: IPv4 或 IPv6 地址字符串，为 None 时直接返回 None.
        db_name: 数据库名称（"GeoLite2" 或 "MaxMind"）.

    Returns:
        ISO 3166-1 alpha-2 国家代码（如 "US"、"CN"），查询失败返回 None.
    """
    if not ip:
        return None
    return await asyncio.to_thread(_lookup_sync, ip, db_name)


def list_available_dbs() -> list[str]:
    """返回当前存在的可用数据库名称列表."""
    return [name for name, path in DB_FILES.items() if path.exists()]
