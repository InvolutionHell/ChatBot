"""Slash 命令 Cog。

/share <url>：手动提交链接到内卷地狱主站，**并在频道里公开展示**。

设计思路（用户反馈：/share 比直接贴链接还差 → 没人会用）：
- Discord 本身会对任意 URL 自动 unfurl 出 OG 预览卡片（标题/描述/封面图）
- 所以 /share 的回复 **把 URL 当纯文本** 发到频道，让 Discord 直接渲染，不自己造轮子
- 额外加一行小字注释 "已收录到内卷地狱 · #id"，用 Discord 的 `-#` subtext 语法
- 不等后端异步审核完成、不轮询 status、不显示"审核中"——直接"已收录"：
  - 这个事实在 API 返回 200 的那一刻就是真的（行已落 shared_links）
  - PENDING_MANUAL / FLAGGED 是主站展示层的决定，跟"是否收录"是两件事
  - 告诉用户"审核中"只会让人误以为失败（见 user feedback）

/share 现在对普通用户价值 = 直接贴 + 多一句"✅ 已收录"+主站链接跳转。
"""

from __future__ import annotations

import re

import discord
import structlog
from discord import app_commands
from discord.ext import commands

from ..api_client import DuplicateURL, InternalAPIError, submit_internal
from ..config import Settings

_URL_RE = re.compile(r"^https?://[^\s<>\"'\]\)]+$", re.IGNORECASE)

log = structlog.get_logger(__name__)


class ShareCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings

    @app_commands.command(name="share", description="把链接收录到内卷地狱分享库（频道内公开）")
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
        url = url.strip()
        if not _URL_RE.match(url):
            await interaction.response.send_message(
                "格式不对，必须以 http:// 或 https:// 开头。", ephemeral=True
            )
            return

        # 不 thinking（不显示"bot is thinking..."），想让 OG 预览尽快出
        await interaction.response.defer(ephemeral=False, thinking=False)

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
                f"这个链接已经在分享库里了（去重）：{url}", ephemeral=True
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

        # 把 URL 作为消息正文发出去：Discord 自动 unfurl 出 OG 预览卡（标题/描述/封面图）
        # 小字 caption 用 `-# ...` 语法（Discord 的 subtext 行，显示为灰色细字）
        content = (
            f"{url}\n"
            f"-# ✅ 已收录到 [内卷地狱分享库](https://involutionhell.com/share) "
            f"· `#{result.link_id}` · by {interaction.user.display_name}"
        )
        if recommendation:
            content = f"> {recommendation}\n{content}"

        await interaction.followup.send(content=content)


async def setup(bot: commands.Bot) -> None:
    settings: Settings = bot.chatbot_settings  # type: ignore[attr-defined]
    await bot.add_cog(ShareCommands(bot, settings))
