"""通知发送模块 — 支持 Telegram Bot, Webhook, SMTP.

根据 message_sender_providers 的配置自动选择发送方式:
  - 名称含 "telegram" → Telegram Bot API
  - 名称含 "webhook"  → HTTP POST
  - 名称含 "smtp"/"email" → SMTP（需配置完整）
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


async def send_notification(channel: dict[str, Any], message: str) -> None:
    """根据渠道配置发送通知."""
    provider_type = (channel.get("provider_type") or "").lower()
    addition_raw = channel.get("addition")
    target = channel.get("target")

    addition: dict[str, Any] = {}
    if addition_raw:
        try:
            addition = json.loads(addition_raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("渠道 %s 的 provider 配置解析失败", channel.get("name"))
            return

    try:
        if "telegram" in provider_type:
            await _send_telegram(addition, target, message)
        elif "webhook" in provider_type:
            await _send_webhook(addition, target, message)
        elif "smtp" in provider_type or "email" in provider_type:
            await _send_email(addition, target, message)
        else:
            logger.warning("不支持的通知提供商: %s", provider_type)
    except Exception:
        logger.exception("通知发送失败 [%s]", channel.get("name"))


async def _send_telegram(
    addition: dict[str, Any], chat_id: str | None, text: str,
) -> None:
    """通过 Telegram Bot API 发送消息."""
    bot_token = addition.get("bot_token", "")
    if not bot_token or not chat_id:
        logger.warning("Telegram 配置不完整 (需要 bot_token 和 chat_id)")
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json={"chat_id": chat_id, "text": text})
        if resp.status_code != 200:
            logger.warning("Telegram 发送失败: %s %s", resp.status_code, resp.text)


async def _send_webhook(
    addition: dict[str, Any], url: str | None, message: str,
) -> None:
    """通过 Webhook POST 发送消息."""
    webhook_url = url or addition.get("url", "")
    if not webhook_url:
        logger.warning("Webhook URL 未配置")
        return
    headers = addition.get("headers") or {}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            webhook_url, json={"message": message}, headers=headers,
        )
        if resp.status_code >= 400:
            logger.warning("Webhook 发送失败: %s", resp.status_code)


async def _send_email(
    addition: dict[str, Any], to_addr: str | None, message: str,
) -> None:
    """通过 SMTP 发送邮件（需要 aiosmtplib 依赖）."""
    try:
        import aiosmtplib
        from email.message import EmailMessage
    except ImportError:
        logger.warning("SMTP 发送需要 aiosmtplib，请安装: pip install aiosmtplib")
        return

    host = addition.get("smtp_host", "")
    port = int(addition.get("smtp_port", 587))
    username = addition.get("smtp_username", "")
    password = addition.get("smtp_password", "")
    from_addr = addition.get("from_address", username)

    if not all([host, username, password, to_addr]):
        logger.warning("SMTP 配置不完整")
        return

    msg = EmailMessage()
    msg["Subject"] = "Collei 告警通知"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(message)

    await aiosmtplib.send(
        msg, hostname=host, port=port,
        username=username, password=password,
        use_tls=True,
    )
