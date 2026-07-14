"""精华墙 Cog：普通频道消息的 ⭐ 反应达到阈值，转贴进精华频道。

事件驱动（on_raw_reaction_add），零 LLM token。已上墙的消息记在
.state/starboard.json 防重复。频道 ID / 阈值走 .env（STARBOARD_CHANNEL_ID /
STARBOARD_THRESHOLD），没配频道时本 Cog 整体静默。
"""

from __future__ import annotations

import discord
import structlog
from discord.ext import commands

from .. import state, stats
from ..config import Settings

log = structlog.get_logger(__name__)

_STAR = "⭐"
_EMBED_COLOR = 0xFFCC4D  # 星星黄


class Starboard(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings
        # {源消息 id: 精华墙消息 id}
        self._posted: dict = state.load("starboard")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        board_id = self.settings.starboard_channel_id
        if not board_id:
            return
        if str(payload.emoji) != _STAR:
            return
        if str(payload.message_id) in self._posted:
            return
        # 墙上的转贴消息本身不再上墙，防套娃。按消息而非频道排除，
        # 这样精华墙可以直接设在普通频道（当前贴在 #common）
        if payload.message_id in set(self._posted.values()):
            return

        channel = self.bot.get_channel(payload.channel_id)
        board = self.bot.get_channel(board_id)
        if channel is None or board is None:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException as e:
            log.warning("starboard_fetch_failed", message_id=payload.message_id, error=str(e))
            return

        stars = next((r.count for r in message.reactions if str(r.emoji) == _STAR), 0)
        if stars < self.settings.starboard_threshold:
            return

        # fetch 是 await 点，并发的反应事件可能同时走到这里：
        # 重查并立刻占位（本段无 await，同一事件循环内不会双贴）
        if str(message.id) in self._posted:
            return
        self._posted[str(message.id)] = 0

        embed = discord.Embed(
            description=(message.content or "（无文字内容）")[:3900]
            + f"\n\n[跳转到原消息]({message.jump_url})",
            timestamp=message.created_at,
            color=_EMBED_COLOR,
        )
        embed.set_author(
            name=message.author.display_name,
            icon_url=message.author.display_avatar.url,
        )
        # 首个图片附件直接铺进 embed，图片梗被星上来很常见
        if message.attachments and (message.attachments[0].content_type or "").startswith("image/"):
            embed.set_image(url=message.attachments[0].url)
        embed.set_footer(text=f"⭐ {stars} · #{message.channel.name}")

        try:
            board_msg = await board.send(embed=embed)
        except discord.HTTPException as e:
            log.warning("starboard_post_failed", message_id=message.id, error=str(e))
            self._posted.pop(str(message.id), None)  # 发失败允许下次星触发重试
            return
        self._posted[str(message.id)] = board_msg.id
        state.save("starboard", self._posted)
        stats.bump("star")  # 周报计数
        log.info("starboard_posted", message_id=message.id, stars=stars)


async def setup(bot: commands.Bot) -> None:
    settings: Settings = bot.chatbot_settings  # type: ignore[attr-defined]
    await bot.add_cog(Starboard(bot, settings))
