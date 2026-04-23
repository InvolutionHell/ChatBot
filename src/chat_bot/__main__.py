"""入口：uv run chat-bot 或 python -m chat_bot。

做两件事：
1. 读配置（复用 involution-hell 后端 .env）
2. 加载 Cog → run

所有 OG 抓取 / 审核 / 入库 都在 involution-hell 后端里，Bot 只是 Discord ↔ API 的搬运工。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

import discord
import structlog
from discord.ext import commands

from .config import Settings, load_settings


def _setup_logging() -> None:
    """把 discord.py 的日志和 structlog 合到一起，全部走 stdout，systemd 收进 journalctl。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    # structlog 走 stdlib logging，StreamHandler 每条 emit 后 flush，journalctl 实时可见
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.stdlib.render_to_log_kwargs,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


class ChatBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        # 必须在开发者后台勾 Message Content Intent，否则 on_message 拿到的 content 是空的
        intents.message_content = True
        # 用 slash command，prefix 基本没用但 commands.Bot 构造必须传
        super().__init__(command_prefix="!", intents=intents)
        self.chatbot_settings = settings

    async def setup_hook(self) -> None:
        await self.load_extension("chat_bot.cogs.listener")
        await self.load_extension("chat_bot.cogs.commands")
        await self.load_extension("chat_bot.cogs.digest")
        await self.load_extension("chat_bot.cogs.alerts")

        # Slash command 同步：配了 guild_id 走 guild 同步（秒生效），否则全局（最长 1h 扩散）
        log = structlog.get_logger(__name__)
        try:
            if self.chatbot_settings.discord_guild_id:
                guild = discord.Object(id=self.chatbot_settings.discord_guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
            else:
                synced = await self.tree.sync()
            log.info(
                "slash_commands_synced",
                count=len(synced),
                names=[c.name for c in synced],
            )
        except Exception as e:
            # sync 失败不阻塞 bot 启动，记日志让运维知道去重试
            log.error("slash_commands_sync_failed", error=str(e))

    async def on_ready(self) -> None:
        log = structlog.get_logger(__name__)
        log.info(
            "bot_ready",
            user=str(self.user),
            watch_channels=list(self.chatbot_settings.watch_channel_ids),
            backend=self.chatbot_settings.internal_submit_url,
        )


async def _amain() -> None:
    _setup_logging()
    settings = load_settings()
    bot = ChatBot(settings)
    async with bot:
        await bot.start(settings.discord_bot_token.get_secret_value())


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_amain())


if __name__ == "__main__":
    main()
