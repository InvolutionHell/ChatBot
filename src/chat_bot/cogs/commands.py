"""Slash 命令 Cog。

/share <url>：手动提交一个链接到内卷地狱主站。

UX 细节：
- 后端 enrichment 是 @Async 的，submit 立刻返回 PENDING
- 直接把 PENDING 抛给用户会像"审核中...静默失败"
- 所以这里 submit 成功后轮询 5×1s，最长等 5 秒拿最终 status；拿到了就 edit 原回复
- 5 秒拿不到就 edit 成"审核中，稍后看主站"，不给人卡死的感觉
"""

from __future__ import annotations

import asyncio
import re

import discord
import structlog
from discord import app_commands
from discord.ext import commands

from ..api_client import (
    DuplicateURL,
    InternalAPIError,
    fetch_link,
    submit_internal,
)
from ..config import Settings

_URL_RE = re.compile(r"^https?://[^\s<>\"'\]\)]+$", re.IGNORECASE)

log = structlog.get_logger(__name__)

# 轮询终态的前端展示
_STATUS_CN = {
    "APPROVED": ("✅ 已上架主站", discord.Color.green()),
    "PENDING_MANUAL": ("⏳ 进入人工审核队列", discord.Color.orange()),
    "FLAGGED": ("⚠️ 命中安全检查，进人工审核", discord.Color.orange()),
    "REJECTED": ("❌ 被拒绝", discord.Color.red()),
    "ARCHIVED": ("📦 原文已失效", discord.Color.greyple()),
}


class ShareCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings

    @app_commands.command(name="share", description="提交一个链接到内卷地狱分享库")
    @app_commands.describe(
        url="以 http:// 或 https:// 开头的完整 URL",
        recommendation="可选：一句话推荐语",
    )
    async def share(
        self,
        interaction: discord.Interaction,
        url: str,
        recommendation: str | None = None,
    ) -> None:
        if not _URL_RE.match(url.strip()):
            await interaction.response.send_message(
                "格式不对，必须以 http:// 或 https:// 开头。", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=False, thinking=True)

        try:
            result = await submit_internal(
                base_url=self.settings.internal_submit_url,
                internal_key=self.settings.internal_api_key.get_secret_value(),
                url=url,
                submitter_label=interaction.user.display_name,
                recommendation=recommendation,
                timeout=self.settings.chatbot_api_timeout,
            )
        except DuplicateURL:
            await interaction.followup.send(
                "这个链接已经在分享库里了（去重）。", ephemeral=True
            )
            return
        except InternalAPIError as e:
            log.error("slash_share_api_error", url=url, status=e.status)
            await interaction.followup.send(
                f"提交失败：后端返回 {e.status}。", ephemeral=True
            )
            return
        except Exception as e:
            log.error("slash_share_unexpected_error", url=url, error=str(e))
            await interaction.followup.send("提交失败，已通知开发者。", ephemeral=True)
            return

        # 轮询 enrichment 结果：每 1s 拉一次，最多等 5s
        final_status = result.status
        og_title = result.og_title
        for _ in range(5):
            if final_status != "PENDING":
                break
            await asyncio.sleep(1)
            try:
                detail = await fetch_link(
                    base_url=self.settings.internal_submit_url,
                    internal_key=self.settings.internal_api_key.get_secret_value(),
                    link_id=result.link_id,
                    timeout=self.settings.chatbot_api_timeout,
                )
            except Exception as e:
                log.warning("slash_share_poll_failed", error=str(e))
                break
            if detail is None:
                break
            final_status = detail.status
            og_title = detail.og_title

        status_label, color = _STATUS_CN.get(
            final_status, ("⏳ 审核中，结果请稍后在主站查看", discord.Color.blurple())
        )

        embed = discord.Embed(
            title=og_title or result.host,
            url=url,
            description=f"**{status_label}**",
            color=color,
        )
        embed.add_field(name="ID", value=str(result.link_id), inline=True)
        embed.add_field(name="来源", value=result.host, inline=True)
        embed.set_footer(text=f"提交人 {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    settings: Settings = bot.chatbot_settings  # type: ignore[attr-defined]
    await bot.add_cog(ShareCommands(bot, settings))
