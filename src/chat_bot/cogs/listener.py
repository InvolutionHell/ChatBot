"""频道消息监听 Cog：从消息里抽链接 → 调后端 internal API。

Bot 只做转发工作：
- URL 正则匹配
- 频道白名单过滤
- 调 api_client.submit_internal
- 成功 / 去重 / 失败打日志，不回消息（被动网，不打扰群聊）

OG 抓取 / DeepSeek 分类 / 入库 全部在后端 SharedLinkEnrichmentWorker 里完成。
"""

from __future__ import annotations

import re

import discord
import structlog
from discord.ext import commands

from ..api_client import DuplicateURL, InternalAPIError, submit_internal
from ..config import Settings

_URL_RE = re.compile(r"https?://[^\s<>\"'\]\)]+", re.IGNORECASE)

log = structlog.get_logger(__name__)


class ShareListener(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # 跳过 bot 自己 / 系统消息 / webhook
        if message.author.bot or message.webhook_id is not None:
            return
        # 只处理监听频道里的消息
        if message.channel.id not in self.settings.watch_channel_ids:
            return

        urls = _URL_RE.findall(message.content)
        if not urls:
            return

        for url in urls:
            await self._handle_one_url(message, url)

    async def _handle_one_url(self, message: discord.Message, url: str) -> None:
        try:
            result = await submit_internal(
                base_url=self.settings.internal_submit_url,
                internal_key=self.settings.internal_api_key.get_secret_value(),
                url=url,
                submitter_label=message.author.display_name,
                timeout=self.settings.chatbot_api_timeout,
            )
        except DuplicateURL:
            log.info(
                "share_duplicate",
                url=url,
                submitter=message.author.display_name,
            )
            return
        except InternalAPIError as e:
            log.error(
                "share_api_error",
                url=url,
                status=e.status,
                message=e.message,
            )
            return
        except Exception as e:
            # 网络、解析等非 API 错误
            log.error("share_unexpected_error", url=url, error=str(e))
            return

        log.info(
            "share_ingested",
            url=url,
            link_id=result.link_id,
            status=result.status,
            submitter=message.author.display_name,
        )


async def setup(bot: commands.Bot) -> None:
    settings: Settings = bot.chatbot_settings  # type: ignore[attr-defined]
    await bot.add_cog(ShareListener(bot, settings))
