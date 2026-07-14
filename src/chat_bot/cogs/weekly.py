"""社区周报 Cog：每周五 20:00 (Asia/Shanghai) 向社区频道发一周小结。

数据来自 stats.py 的本地计数（新成员 / 分享上架 / 精华上墙），零 LLM
token。一周全为 0 则静默跳过。频道复用 COMMUNITY_FEED_CHANNEL_ID。
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import structlog
from discord.ext import commands, tasks

from .. import stats
from ..config import Settings

log = structlog.get_logger(__name__)

_CST = ZoneInfo("Asia/Shanghai")
_RUN_AT = time(20, 0, tzinfo=_CST)
_FRIDAY = 4
_WEEK_SEC = 7 * 86400


def _compose(counts: dict, today: str) -> str | None:
    """周报文案（艾露猫口吻，对每个人都叫老大）；全为 0 返回 None。"""
    joins = counts.get("join", 0)
    shares = counts.get("share", 0)
    stars = counts.get("star", 0)
    if not (joins or shares or stars):
        return None
    lines = [f"📮 **艾露猫的一周小报** · {today}", ""]
    if joins:
        lines.append(f"🆕 新来了 **{joins}** 位老大")
    if shares:
        lines.append(f"📚 **{shares}** 条分享上架进了分享库")
    if stars:
        lines.append(f"⭐ **{stars}** 条消息登上精华墙")
    lines += ["", "老大们本周辛苦了，下周也一起玩喵～"]
    return "\n".join(lines)


class WeeklyReport(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings
        # tasks.loop(time=...) 只支持每日触发，_run 里再筛周五
        self._loop = tasks.loop(time=_RUN_AT)(self._run)
        self._loop.before_loop(self._before)

    def cog_load(self) -> None:
        self._loop.start()

    def cog_unload(self) -> None:
        self._loop.cancel()

    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    async def _run(self) -> None:
        now = datetime.now(_CST)
        if now.weekday() != _FRIDAY:
            return
        channel_id = self.settings.community_feed_channel_id
        if not channel_id:
            return
        text = _compose(stats.counts_since(_WEEK_SEC), now.strftime("%Y-%m-%d"))
        if text is None:
            log.info("weekly_skip_empty")
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            log.warning("weekly_channel_not_found", channel_id=channel_id)
            return
        try:
            await channel.send(text)
            log.info("weekly_report_sent")
        except Exception as e:
            log.error("weekly_send_failed", error=str(e))


async def setup(bot: commands.Bot) -> None:
    settings: Settings = bot.chatbot_settings  # type: ignore[attr-defined]
    await bot.add_cog(WeeklyReport(bot, settings))
