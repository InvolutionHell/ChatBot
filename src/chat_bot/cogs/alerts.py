"""FLAGGED 告警即时推送 Cog。

Bot 内嵌一个 aiohttp 服务器（127.0.0.1:CHATBOT_ALERT_PORT），接收后端
SharedLinkEnrichmentWorker 在判定 status=FLAGGED 时 fire 的 webhook POST。
收到后立即推送 Discord 管理员频道 + 邮件，不走每日 digest。

鉴权：X-Internal-Key header，和后端共用同一把密钥。
loopback 端口：和后端同机，不经 Caddy 不开公网，纯内网通信。

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
        # 只绑 127.0.0.1，外网不可达
        site = web.TCPSite(
            self._runner, host="127.0.0.1", port=self.settings.chatbot_alert_port
        )
        await site.start()
        log.info("alert_server_listening", port=self.settings.chatbot_alert_port)

    async def cog_unload(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    async def _handle_flagged(self, req: web.Request) -> web.Response:
        # 鉴权
        provided = req.headers.get("X-Internal-Key", "")
        expected = self.settings.internal_api_key.get_secret_value()
        if not expected or provided != expected:
            return web.json_response({"ok": False, "msg": "forbidden"}, status=403)

        try:
            payload = await req.json()
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
