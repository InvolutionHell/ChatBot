"""FLAGGED 告警即时推送 Cog。

Bot 内嵌一个 aiohttp 服务器（0.0.0.0:CHATBOT_ALERT_PORT），接收后端
SharedLinkEnrichmentWorker 在判定 status=FLAGGED 时 fire 的 webhook POST。
收到后立即推送 Discord 管理员频道 + 邮件，不走每日 digest。

为什么绑 0.0.0.0 而不是 127.0.0.1：backend 跑在 Docker 容器里，从容器看
宿主机是 docker bridge (host.docker.internal)，只绑 loopback 接不到。
对外暴露面由三层兜：
  (a) X-Internal-Key 常量时间比较，防 timing 猜解
  (b) 可选 HMAC-SHA256 签名（WEBHOOK_HMAC_SECRET 配了就强校验）
  (c) 上游 Oracle VCN ingress / Docker networking 决定哪些 IP 能打过来

payload 形如：
{
  "type": "flagged",
  "id": 42,
  "url": "...",
  "host": "...",
  "title": "...",
  "recommendation": "...",
  "flags": {"nsfw": false, "ad": true, "flame": false}
}
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
import structlog
from aiohttp import web
from discord.ext import commands

from ..config import Settings
from ..email_sender import SmtpConfig, send_email

log = structlog.get_logger(__name__)

_CST = ZoneInfo("Asia/Shanghai")


def _loads_json(raw: bytes) -> dict:
    """把 raw bytes 解析为 dict（上层用 try/except 兜 JSONDecodeError）。"""
    return json.loads(raw.decode("utf-8"))


class AlertServer(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings
        self._runner: web.AppRunner | None = None

    async def cog_load(self) -> None:
        # 注意：cog_load 运行在 bot.setup_hook 里——此时 bot 还没 connect，
        # 不能在这里 await wait_until_ready，否则会死锁（on_ready 要等 setup_hook 返回）。
        # aiohttp server 不依赖 Discord 登录，直接在这里挂起即可。
        app = web.Application()
        app.router.add_post("/alert/flagged", self._handle_flagged)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        # 绑 0.0.0.0：Backend 跑在 Docker 容器里，从容器内看 127.0.0.1 是容器自己
        # 而不是宿主机，因此必须监听所有接口才能接 docker bridge (host.docker.internal)。
        # 公网侧安全性由 Oracle VCN ingress + X-Internal-Key header + 可选 HMAC 签名保证。
        site = web.TCPSite(
            self._runner, host="0.0.0.0", port=self.settings.chatbot_alert_port  # noqa: S104
        )
        await site.start()
        log.info("alert_server_listening", port=self.settings.chatbot_alert_port)
        # HMAC 没配：允许向前兼容（后端可能还没部署签名逻辑），但必须显式警告
        if self.settings.webhook_hmac_secret is None:
            log.warning(
                "alert_webhook_hmac_disabled",
                msg="WEBHOOK_HMAC_SECRET 未配置：/alert/flagged 只做 X-Internal-Key"
                " 校验，不验签。属于过渡模式（backend 尚未发出签名）。backend 上线"
                " 签名后，把密钥同步到本服务 env 即可启用。",
            )

    async def cog_unload(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @staticmethod
    def _verify_hmac(secret: str, raw_body: bytes, sig_header: str) -> bool:
        """校验 X-Signature: sha256=<hex> 格式的签名。

        - header 缺失 / 格式不对 / digest 不匹配 → False
        - 用 hmac.compare_digest 做常量时间比较
        """
        if not sig_header or not sig_header.startswith("sha256="):
            return False
        provided_hex = sig_header[len("sha256="):].strip()
        if not provided_hex:
            return False
        expected_hex = hmac.new(
            secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        # 大小写无关比较——避免后端大写/小写差异导致误拒
        return hmac.compare_digest(provided_hex.lower(), expected_hex.lower())

    async def _handle_flagged(self, req: web.Request) -> web.Response:
        # 鉴权 1：X-Internal-Key（常量时间比较，避免 timing 泄露 key 前缀）
        provided = req.headers.get("X-Internal-Key", "")
        expected = self.settings.internal_api_key.get_secret_value()
        if not expected or not hmac.compare_digest(provided, expected):
            return web.json_response({"ok": False, "msg": "forbidden"}, status=403)

        # 先把原始 body 读出来——HMAC 必须对 raw bytes 算，JSON parse 之后再序列化会漂
        raw_body = await req.read()

        # 鉴权 2：HMAC-SHA256 签名（可选）。配了 secret 就强校验，没配就跳过。
        hmac_secret = self.settings.webhook_hmac_secret
        if hmac_secret is not None:
            sig_header = req.headers.get("X-Signature", "")
            if not self._verify_hmac(hmac_secret.get_secret_value(), raw_body, sig_header):
                log.warning(
                    "alert_hmac_reject",
                    has_header=bool(sig_header),
                    body_len=len(raw_body),
                )
                return web.json_response(
                    {"ok": False, "msg": "invalid signature"}, status=401
                )

        try:
            payload = _loads_json(raw_body)
        except Exception:
            return web.json_response({"ok": False, "msg": "bad json"}, status=400)

        if payload.get("type") != "flagged":
            return web.json_response({"ok": False, "msg": "unsupported type"}, status=400)

        log.info(
            "alert_flagged_received",
            link_id=payload.get("id"),
            host=payload.get("host"),
            flags=payload.get("flags"),
        )

        # 并行推两边（Discord + 邮件），失败都不影响 ACK 给后端
        await self._push_discord(payload)
        await self._push_email(payload)

        return web.json_response({"ok": True})

    # ── Discord 推送 ───────────────────────────────────────────────────────
    async def _push_discord(self, payload: dict) -> None:
        channel_id = self.settings.discord_admin_channel_id
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            log.warning("alert_admin_channel_not_found", channel_id=channel_id)
            return

        flags = payload.get("flags") or {}
        flag_tags = [k for k, v in flags.items() if v]
        flag_label = " / ".join(flag_tags) or "unknown"

        embed = discord.Embed(
            title=f"⚠️ 命中 {flag_label}",
            description=payload.get("title") or payload.get("url", ""),
            url=payload.get("url"),
            color=discord.Color.red(),
        )
        embed.add_field(name="ID", value=str(payload.get("id")), inline=True)
        embed.add_field(name="来源", value=payload.get("host") or "-", inline=True)
        if payload.get("recommendation"):
            embed.add_field(name="推荐语", value=payload["recommendation"], inline=False)
        embed.set_footer(text="请尽快人工复核：involutionhell.com/admin/community")

        try:
            await channel.send(embed=embed)
        except Exception as e:
            log.error("alert_discord_send_failed", error=str(e))

    # ── 邮件推送 ──────────────────────────────────────────────────────────
    async def _push_email(self, payload: dict) -> None:
        if not self.settings.email_configured:
            return

        flags = payload.get("flags") or {}
        flag_tags = [k for k, v in flags.items() if v]
        flag_label = " / ".join(flag_tags) or "unknown"
        now = datetime.now(_CST).strftime("%Y-%m-%d %H:%M:%S")

        subject = f"[内卷地狱] ⚠️ FLAGGED [{flag_label}] id={payload.get('id')}"
        body_lines = [
            f"时间：{now} (Asia/Shanghai)",
            f"链接 ID：{payload.get('id')}",
            f"来源：{payload.get('host')}",
            f"URL：{payload.get('url')}",
            f"标题：{payload.get('title') or '(无)'}",
            f"推荐语：{payload.get('recommendation') or '(无)'}",
            f"命中：{flag_label}",
            "",
            "请尽快人工复核：https://involutionhell.com/admin/community",
        ]
        cfg = SmtpConfig(
            host="smtp.gmail.com",
            port=587,
            user=self.settings.gmail_user,
            password=self.settings.gmail_app_password.get_secret_value(),
            from_addr=self.settings.gmail_user,
            to_addr=self.settings.digest_email_to,
        )
        await send_email(cfg, subject=subject, body_text="\n".join(body_lines))


async def setup(bot: commands.Bot) -> None:
    settings: Settings = bot.chatbot_settings  # type: ignore[attr-defined]
    await bot.add_cog(AlertServer(bot, settings))
