"""GitHub Releases 版本检查器 — 每小时轮询一次，缓存最新版本与聚合 changelog.

用法:
  # 启动时初始化（读取当前版本 & commit hash）
  update_checker.startup()

  # 后台任务中定时调用
  await update_checker.check()

  # API 层读取缓存
  info = update_checker.get_info()  # -> dict | None
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from importlib.metadata import version as pkg_version
from pathlib import Path

import httpx
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

_GITHUB_OWNER = "collei-monitor"
_GITHUB_REPO = "collei"
_RELEASES_URL = f"https://api.github.com/repos/{_GITHUB_OWNER}/{_GITHUB_REPO}/releases"
_REQUEST_TIMEOUT = 15


def _parse_version(tag: str) -> Version | None:
    """解析 tag 为 PEP 440 版本，失败返回 None."""
    try:
        return Version(tag.lstrip("v"))
    except InvalidVersion:
        return None


def _read_commit_hash() -> str | None:
    """优先读 .commit_hash 文件，fallback 到 git rev-parse."""
    # 1) 尝试读文件（Docker / deploy.sh 构建时写入）
    for candidate in (Path(".commit_hash"), Path("/app/.commit_hash")):
        try:
            content = candidate.read_text().strip()
            if content:
                return content
        except OSError:
            continue

    # 2) fallback: git（开发环境）
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except Exception:  # noqa: BLE001
        pass

    return None


class UpdateChecker:
    """版本检查器（内存缓存，单例）."""

    def __init__(self) -> None:
        self.current_version: str | None = None
        self.current_commit: str | None = None
        self.latest_version: str | None = None
        self.latest_commit: str | None = None
        self.has_update: bool = False
        self.changelog: str | None = None
        self.checked_at: int | None = None

    def startup(self) -> None:
        """应用启动时调用：读取当前版本 & commit hash."""
        try:
            self.current_version = pkg_version("collei")
        except Exception:  # noqa: BLE001
            self.current_version = "unknown"

        self.current_commit = _read_commit_hash()
        logger.info(
            "UpdateChecker: current_version=%s, commit=%s",
            self.current_version,
            self.current_commit or "unknown",
        )

    async def check(self) -> None:
        """调用 GitHub Releases API，比较版本并聚合 changelog."""
        try:
            async with httpx.AsyncClient(
                timeout=_REQUEST_TIMEOUT,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Collei-Panel/1.0",
                },
            ) as client:
                resp = await client.get(_RELEASES_URL)
                resp.raise_for_status()
                releases: list[dict] = resp.json()

            if not releases:
                return

            cur = _parse_version(self.current_version or "")

            # 按语义版本降序排列（最新在前），跳过无法解析的 tag
            valid: list[tuple[Version, dict]] = []
            for r in releases:
                if r.get("draft"):
                    continue
                v = _parse_version(r.get("tag_name", ""))
                if v is not None:
                    valid.append((v, r))
            valid.sort(key=lambda x: x[0], reverse=True)

            if not valid:
                return

            latest_ver, latest_rel = valid[0]

            self.latest_version = str(latest_ver)
            self.latest_commit = (latest_rel.get("target_commitish") or "")[:12] or None
            self.has_update = cur is not None and latest_ver > cur
            self.checked_at = int(time.time())

            # 聚合 changelog：当前版本 < release <= 最新版本
            if self.has_update and cur is not None:
                parts: list[str] = []
                for v, r in valid:
                    if v <= cur:
                        break
                    tag = r.get("tag_name", "")
                    body = (r.get("body") or "").strip()
                    if body:
                        parts.append(f"## {tag}\n\n{body}")
                    else:
                        parts.append(f"## {tag}\n\n*No release notes.*")
                self.changelog = "\n\n---\n\n".join(parts) if parts else None
            else:
                self.changelog = None

            logger.info(
                "UpdateChecker: latest=%s, has_update=%s",
                self.latest_version,
                self.has_update,
            )

        except Exception:  # noqa: BLE001
            logger.warning("UpdateChecker: failed to check for updates", exc_info=True)

    def get_info(self) -> dict | None:
        """返回缓存的版本信息（供 API 层使用）."""
        if self.current_version is None:
            return None
        return {
            "current_version": self.current_version,
            "current_commit": self.current_commit,
            "latest_version": self.latest_version,
            "latest_commit": self.latest_commit,
            "has_update": self.has_update,
            "changelog": self.changelog,
            "checked_at": self.checked_at,
        }


# 全局单例
update_checker = UpdateChecker()
