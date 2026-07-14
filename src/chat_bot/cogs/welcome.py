"""成员生命周期 Cog：新人欢迎 / Boost 感谢 / 入服周年，全部事件驱动零 token。

为什么不用 on_member_join：那需要 GUILD_MEMBERS 特权 intent（开发者后台
开关 + Intents.members）。而 Discord 在系统频道发的"xx 加入了服务器"
消息（MessageType.new_member）本身就是加入事件，默认 intent 就能收到，
直接 reply 它即可。Boost 的系统消息（premium_guild_*）同理。
周年检测借道日常消息：成员当天发言时用 author.joined_at 判断，同样零特权。
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import discord
import structlog
from discord.ext import commands

from .. import state, stats
from ..config import Settings

log = structlog.get_logger(__name__)

_TZ = ZoneInfo("Asia/Shanghai")

# 文案语气遵循艾露猫 persona（~/.hermes/SOUL.md）：自称艾露猫、每条最多一个
# "喵"、✨🐾 少量点缀；艾露猫是随从性格，对社区里每个人都叫"老大"。
_WELCOME = (
    "✨ 新来的老大出现了！欢迎 {mention} 加入 **Involution Hell**！\n"
    "艾露猫带老大逛逛：这里有大家一起攒的分享库、每天都在聊的技术话题，"
    "还有一群超友好的大佬 🐾\n"
    "有什么想问的直接在频道里喊一声就好，艾露猫也一直都在喵～"
)

_BOOST_THANKS = (
    "🚀 哇！！感谢 {mention} 老大给 **Involution Hell** Boost 充能！\n"
    "服务器变得更厉害了，艾露猫的尾巴都要翘上天了喵～✨"
)

_ANNIVERSARY = (
    "🎂 咦！今天是 {mention} 老大加入 **Involution Hell** 满 {years} 周年的日子！\n"
    "艾露猫一直偷偷记着呢，谢谢老大陪了大家这么久喵～✨"
)

# Boost 的四种系统消息：裸 boost + 冲到 1/2/3 级
_BOOST_TYPES = frozenset(
    {
        discord.MessageType.premium_guild_subscription,
        discord.MessageType.premium_guild_tier_1,
        discord.MessageType.premium_guild_tier_2,
        discord.MessageType.premium_guild_tier_3,
    }
)


def _anniversary_years(joined_at: datetime.datetime, now: datetime.datetime) -> int | None:
    """入服整周年返回年数（>=1），否则 None。按北京时间的月/日口径比对。"""
    j = joined_at.astimezone(_TZ)
    n = now.astimezone(_TZ)
    if (j.month, j.day) != (n.month, n.day):
        return None
    years = n.year - j.year
    return years if years >= 1 else None


class RookieWelcome(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings
        # {user_id: 已庆祝过的周年所在年份}，防同一天发言多次被贺多次
        self._congratulated: dict = state.load("anniversary")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        # 配置了 guild 就只在自己服务器出声，防止 bot 被拉进别的服乱发
        if self.settings.discord_guild_id and message.guild.id != self.settings.discord_guild_id:
            return

        if message.type in _BOOST_TYPES:
            await self._reply(message, _BOOST_THANKS.format(mention=message.author.mention))
            log.info("boost_thanked", user=str(message.author))
            return

        if message.type is discord.MessageType.new_member:
            # 新加入的是 bot（被人拉进来的应用）就不欢迎了
            if message.author.bot:
                return
            await self._reply(message, _WELCOME.format(mention=message.author.mention))
            stats.bump("join")  # 周报计数
            log.info(
                "rookie_welcomed",
                user=str(message.author),
                user_id=message.author.id,
            )
            return

        if message.type in (discord.MessageType.default, discord.MessageType.reply):
            await self._maybe_congratulate_anniversary(message)

    async def _maybe_congratulate_anniversary(self, message: discord.Message) -> None:
        """成员当天首次发言时，如果恰逢入服整周年就贺一句。"""
        if message.author.bot:
            return
        joined_at = getattr(message.author, "joined_at", None)
        if joined_at is None:
            return
        now = discord.utils.utcnow()
        years = _anniversary_years(joined_at, now)
        if years is None:
            return
        uid = str(message.author.id)
        year_now = now.astimezone(_TZ).year
        if self._congratulated.get(uid) == year_now:
            return
        self._congratulated[uid] = year_now
        state.save("anniversary", self._congratulated)
        await self._reply(message, _ANNIVERSARY.format(mention=message.author.mention, years=years))
        log.info("anniversary_congratulated", user=str(message.author), years=years)

    @staticmethod
    async def _reply(message: discord.Message, content: str) -> None:
        # 文案里已带 mention（会推送提醒），reply 头就不再重复 @ 一次；
        # 发失败不值得炸掉事件循环，记日志即可
        try:
            await message.reply(content, mention_author=False)
        except Exception as e:
            log.warning("welcome_reply_failed", error=str(e))


async def setup(bot: commands.Bot) -> None:
    settings: Settings = bot.chatbot_settings  # type: ignore[attr-defined]
    await bot.add_cog(RookieWelcome(bot, settings))
