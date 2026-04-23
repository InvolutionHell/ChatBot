"""每日审核摘要 Cog。

每天本地时间 DIGEST_TIME_CST (默认 09:00 Asia/Shanghai) 拉一次后端 summary：
- 若配了 DISCORD_ADMIN_CHANNEL_ID：往管理员频道发一条卡片
- 若配了 Gmail SMTP 三件套：往 DIGEST_EMAIL_TO 发一封邮件

两个都不配则 Cog 只打日志不做事，不崩。
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import discord
import structlog
from discord.ext import commands, tasks

from ..api_client import InternalAPIError, fetch_summary
from ..config import Settings
from ..email_sender import SmtpConfig, send_email

log = structlog.get_logger(__name__)

_CST = ZoneInfo("Asia/Shanghai")


def _parse_hhmm(raw: str) -> time:
    """'09:00' → datetime.time；非法降级 9:00。"""
    try:
        hh, mm = raw.split(":")
        return time(int(hh), int(mm), tzinfo=_CST)
    except Exception:
        log.warning("bad_digest_time_cst", raw=raw)
        return time(9, 0, tzinfo=_CST)


class DailyDigest(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings

        # discord.py tasks.loop 的 time 参数只接 time 对象，不接 cron；改配置需重启 bot
        run_at = _parse_hhmm(settings.digest_time_cst)
        self._loop = tasks.loop(time=run_at)(self._run)
        self._loop.before_loop(self._before)

    def cog_load(self) -> None:
        self._loop.start()

    def cog_unload(self) -> None:
        self._loop.cancel()

    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    async def _run(self) -> None:
        try:
            summary = await fetch_summary(
                base_url=self.settings.internal_submit_url,
                internal_key=self.settings.internal_api_key.get_secret_value(),
                sample_limit=10,
                timeout=self.settings.chatbot_api_timeout,
            )
        except InternalAPIError as e:
            log.error("digest_fetch_failed", status=e.status, message=e.message)
            return
        except Exception as e:
            log.error("digest_fetch_unexpected", error=str(e))
            return

        total_pending = summary.pending_manual + summary.flagged
        log.info(
            "digest_fetched",
            pending_manual=summary.pending_manual,
            flagged=summary.flagged,
            approved_last_24h=summary.approved_last_24h,
        )

        # 无待审且无新通过，也值得告诉管理员"今天系统正常"，但省流量：只在有数据时发
        if total_pending == 0 and summary.approved_last_24h == 0:
            log.info("digest_skip_empty")
            return

        discord_task = self._send_discord(summary)
        email_task = self._send_email(summary)
        # 两个都 await（不用 gather 防异常互相吞）
        await discord_task
        await email_task

    # ── Discord 推送 ───────────────────────────────────────────────────────
    async def _send_discord(self, summary) -> None:
        channel_id = self.settings.discord_admin_channel_id
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            log.warning("digest_admin_channel_not_found", channel_id=channel_id)
            return

        today = datetime.now(_CST).strftime("%Y-%m-%d")
        embed = discord.Embed(
            title=f"📋 审核摘要 · {today}",
            color=discord.Color.orange() if (summary.pending_manual + summary.flagged) else discord.Color.green(),
        )
        embed.add_field(name="人工待审", value=str(summary.pending_manual), inline=True)
        embed.add_field(name="已标记", value=str(summary.flagged), inline=True)
        embed.add_field(name="24h 新增通过", value=str(summary.approved_last_24h), inline=True)

        if summary.pending_samples:
            lines = [f"• [{s['host']}]({s['url']}) · `{s['id']}`" for s in summary.pending_samples[:10]]
            embed.add_field(name="最久未处理", value="\n".join(lines), inline=False)

        embed.set_footer(text="处理入口：api.involutionhell.com/admin/community")
        try:
            await channel.send(embed=embed)
        except Exception as e:
            log.error("digest_discord_send_failed", error=str(e))

    # ── 邮件推送 ──────────────────────────────────────────────────────────
    async def _send_email(self, summary) -> None:
        if not self.settings.email_configured:
            return

        today = datetime.now(_CST).strftime("%Y-%m-%d")
        subject = f"[内卷地狱] 审核摘要 {today}：待审 {summary.pending_manual + summary.flagged}"

        lines = [
            f"日期：{today} (Asia/Shanghai)",
            "",
            f"人工待审（PENDING_MANUAL）：{summary.pending_manual}",
            f"已标记（FLAGGED）：      {summary.flagged}",
            f"24h 新增通过（APPROVED）：{summary.approved_last_24h}",
            "",
        ]
        if summary.pending_samples:
            lines.append("最久未处理：")
            for s in summary.pending_samples:
                lines.append(f"  [{s['id']}] {s['host']} → {s['url']}")
            lines.append("")
        lines.append("处理入口：https://api.involutionhell.com/admin/community")

        body_text = "\n".join(lines)

        cfg = SmtpConfig(
            host="smtp.gmail.com",
            port=587,
            user=self.settings.gmail_user,
            password=self.settings.gmail_app_password.get_secret_value(),
            from_addr=self.settings.gmail_user,
            to_addr=self.settings.digest_email_to,
        )
        ok = await send_email(cfg, subject=subject, body_text=body_text)
        log.info("digest_email_sent" if ok else "digest_email_failed")


async def setup(bot: commands.Bot) -> None:
    settings: Settings = bot.chatbot_settings  # type: ignore[attr-defined]
    await bot.add_cog(DailyDigest(bot, settings))
