"""Collei CLI 管理工具.

子命令:
  passwd                重置用户密码（并清除所有会话）
  disable-2fa           关闭用户的两步验证（并清除所有会话）
  allow-password-login  启用或禁用密码登录
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys


def _preload_env() -> None:
    """尝试从 /data/.secrets 加载密钥环境变量（Docker 容器场景）.

    docker exec 新进程不会继承 entrypoint.sh export 的变量，
    因此需要手动加载。CLI 本身不使用 JWT，读取失败时设置
    dummy key 仅用于抑制 Settings 校验警告。
    """
    secrets_path = os.environ.get("COLLEI_DATA_DIR", "/data") + "/.secrets"
    try:
        with open(secrets_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip().strip("'\""))
    except OSError:
        pass
    # CLI 不需要 JWT，若仍未加载则设置 dummy 抑制警告
    os.environ.setdefault("COLLEI_SECRET_KEY", "cli-not-used")


_preload_env()


# ── 公共辅助 ──────────────────────────────────────────────────────────────────

async def _get_target_user(session, username: str | None):
    """按 username 查找用户；未指定时返回第一个用户（单用户系统）."""
    from app.crud.auth import get_all_users, get_user_by_username

    if username:
        user = await get_user_by_username(session, username)
        if user is None:
            print(f"错误: 用户 '{username}' 不存在", file=sys.stderr)
            sys.exit(1)
        return user

    users = await get_all_users(session)
    if not users:
        print("错误: 数据库中没有任何用户", file=sys.stderr)
        sys.exit(1)
    return users[0]


# ── passwd ────────────────────────────────────────────────────────────────────

async def _cmd_passwd(args: argparse.Namespace) -> None:
    from app.core.security import hash_password
    from app.crud.auth import delete_user_sessions, update_user
    from app.db.session import async_session_factory

    # 获取密码
    password = args.password
    generated = False
    if not password:
        password = secrets.token_urlsafe(12)[:12]
        generated = True

    async with async_session_factory() as session:
        user = await _get_target_user(session, args.username)

        await update_user(session, user.uuid, passwd=hash_password(password))
        await delete_user_sessions(session, user.uuid)
        await session.commit()

    if generated:
        print(f"已为用户 '{user.username}' 生成新密码: {password}")
    else:
        print(f"已重置用户 '{user.username}' 的密码。")
    print("所有现有会话已失效。")


# ── disable-2fa ───────────────────────────────────────────────────────────────

async def _cmd_disable_2fa(args: argparse.Namespace) -> None:
    from app.crud.auth import delete_user_sessions, update_user
    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        user = await _get_target_user(session, args.username)

        if not user.two_factor:
            print(f"用户 '{user.username}' 未启用两步验证，无需操作。")
            return

        await update_user(session, user.uuid, two_factor=None)
        await delete_user_sessions(session, user.uuid)
        await session.commit()

    print(f"已关闭用户 '{user.username}' 的两步验证，所有现有会话已失效。")


# ── allow-password-login ──────────────────────────────────────────────────────

async def _cmd_allow_password_login(args: argparse.Namespace) -> None:
    from app.crud.config import set_config
    from app.db.session import async_session_factory

    value = "true" if args.action == "enable" else "false"

    async with async_session_factory() as session:
        if value == "false":
            from app.crud.auth import get_enabled_oidc_providers
            providers = await get_enabled_oidc_providers(session)
            if not providers:
                print(
                    "错误: 无法禁用密码登录 — 没有已启用的 SSO/OIDC 提供商",
                    file=sys.stderr,
                )
                print(
                    "请先在 Collei 设置中配置并启用至少一个 SSO/OIDC 登录方式。",
                    file=sys.stderr,
                )
                sys.exit(1)
        await set_config(session, "allow_password_login", value)

    state = "已启用" if value == "true" else "已禁用"
    print(f"密码登录{state}。")
    if value == "false":
        print("提示: 请确保已配置 SSO/OIDC 登录方式，否则将无法登录。")
    print("注意: 若 Collei 服务正在运行，需重启以使配置在内存缓存中生效。")


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="collei",
        description="Collei 管理工具",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # passwd
    p_passwd = subparsers.add_parser("passwd", help="重置用户密码")
    p_passwd.add_argument("--username", help="目标用户名（默认: 第一个用户）")
    p_passwd.add_argument("--password", help="新密码（不指定则自动生成 12 位随机密码）")

    # disable-2fa
    p_2fa = subparsers.add_parser("disable-2fa", help="关闭用户的两步验证")
    p_2fa.add_argument("--username", help="目标用户名（默认: 第一个用户）")

    # allow-password-login
    p_pw_login = subparsers.add_parser(
        "allow-password-login", help="启用或禁用密码登录",
    )
    p_pw_login.add_argument(
        "action", choices=["enable", "disable"],
        help="enable = 允许密码登录, disable = 禁止密码登录",
    )

    args = parser.parse_args()

    if args.command == "passwd":
        asyncio.run(_cmd_passwd(args))
    elif args.command == "disable-2fa":
        asyncio.run(_cmd_disable_2fa(args))
    elif args.command == "allow-password-login":
        asyncio.run(_cmd_allow_password_login(args))


if __name__ == "__main__":
    main()
