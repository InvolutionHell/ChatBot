"""Gmail SMTP 发信（用 App Password）。

为什么用 SMTP 而不是 Gmail API：
- Gmail API 需要 OAuth2 + token refresh 循环，对一个每天只发 1 封邮件的机器人太重
- App Password + STARTTLS 是一次性配置，后续零维护
- 用户流程：https://myaccount.google.com/apppasswords → 生成 16 字符 App Password → 写 .env

前提：Google 账号已开 2FA；未开 2FA 时 App Password 页面不可见。
"""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage

import aiosmtplib
import structlog

log = structlog.get_logger(__name__)


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_addr: str
    # 多收件人逗号分隔（"a@x.com, b@y.com"），aiosmtplib 会依据 To header 自动推导
    to_addr: str


def _split_addrs(raw: str) -> list[str]:
    return [a.strip() for a in raw.split(",") if a.strip()]


async def send_email(cfg: SmtpConfig, subject: str, body_text: str, body_html: str | None = None) -> bool:
    """发一封邮件；失败时打日志返回 False，绝不抛出（不阻塞 bot 主流程）。

    to_addr 支持逗号分隔的多地址。
    """
    recipients = _split_addrs(cfg.to_addr)
    if not recipients:
        log.warning("email_no_recipients")
        return False

    msg = EmailMessage()
    msg["From"] = cfg.from_addr
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    try:
        await aiosmtplib.send(
            msg,
            hostname=cfg.host,
            port=cfg.port,
            start_tls=True,
            username=cfg.user,
            password=cfg.password,
            recipients=recipients,  # 显式给一遍避免解析歧义
            timeout=20,
        )
        return True
    except Exception as e:
        log.error("email_send_failed", error=str(e), to=cfg.to_addr)
        return False
