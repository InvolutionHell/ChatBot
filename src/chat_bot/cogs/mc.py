"""/mc 命令 Cog：查 Involution Hell MC 服务器在线状态。

用 mcstatus 的 Server List Ping——和游戏客户端服务器列表同款协议，
公开信息，不需要 RCON 密码等任何凭证。
"""

from __future__ import annotations

import discord
import structlog
from discord import app_commands
from discord.ext import commands
from mcstatus import JavaServer

from ..config import Settings

log = structlog.get_logger(__name__)


class McStatus(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings

    @app_commands.command(name="mc", description="看看 MC 服务器现在有谁在线")
    async def mc(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        host = self.settings.mc_server_host
        try:
            server = await JavaServer.async_lookup(host)
            status = await server.async_status()
        except Exception as e:
            log.warning("mc_status_failed", host=host, error=str(e))
            await interaction.followup.send(
                f"😿 `{host}` 没有响应……服务器可能在打盹，也可能真出事了。"
                "艾露猫建议过会儿再来看看喵。"
            )
            return

        players = status.players
        names = ""
        if players.sample:
            names = "\n在线的有：" + "、".join(p.name for p in players.sample)
        await interaction.followup.send(
            f"🎮 **{host}** 在线中！\n"
            f"当前玩家 **{players.online} / {players.max}**{names}\n"
            f"-# {status.version.name} · 延迟 {round(status.latency)} ms\n"
            f"艾露猫也想进去挖矿喵～"
        )


async def setup(bot: commands.Bot) -> None:
    settings: Settings = bot.chatbot_settings  # type: ignore[attr-defined]
    await bot.add_cog(McStatus(bot, settings))
