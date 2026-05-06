"""Slash 命令 Cog。

/share <url>：手动提交链接到内卷地狱主站，**并在频道里公开展示**。

设计思路（用户反馈：原版"已收录"立即回复让用户去 /feed 看不到 PENDING
链接，体验黑盒）：
- Discord 本身会对任意 URL 自动 unfurl 出 OG 预览卡片（标题/描述/封面图）
- 所以 /share 的回复 **把 URL 当纯文本** 发到频道，让 Discord 直接渲染
- 状态尾标（`-# ` Discord subtext 语法）实时反映审核进度：
  - 提交瞬间：⏳ 已提交，AI 审核中... (status=PENDING)
  - 后台 polling /internal/{id}，拿到终态后 message.edit 替换尾标
  - APPROVED        → ✅ 已收录到分享库 + 站内链接
  - PENDING_MANUAL  → 🟡 非白名单，等待人工复核
  - FLAGGED         → 🟡 AI 标记需复核
  - REJECTED        → ❌ 审核未通过
  - ARCHIVED        → 📦 系统归档

跟 listener cog 同款机制：先告知正在审核，拿到终态再展示真实结果。
轮询超时（30s）静默——用户继续看到"⏳ AI 审核中"，可去 /u/<id>/shares
查进度。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Coroutine
from typing import Any

import discord
import structlog
from discord import app_commands
from discord.ext import commands

from ..api_client import DuplicateURL, InternalAPIError, fetch_link, submit_internal
from ..config import Settings

_URL_RE = re.compile(r"^https?://[^\s<>\"'\]\)]+$", re.IGNORECASE)

_FEED_URL = "https://involutionhell.com/zh/feed"

# 轮询参数（与 listener cog 同款；常量重复定义避免 cog 间循环 import）
_POLL_INTERVAL_SEC = 2.0
_POLL_TIMEOUT_SEC = 30.0
_TERMINAL_STATUSES = {"APPROVED", "PENDING_MANUAL", "FLAGGED", "REJECTED", "ARCHIVED"}

log = structlog.get_logger(__name__)


def _render_share_message(
    *,
    url: str,
    link_id: int,
    user_display_name: str,
    recommendation: str | None,
    status: str,
) -> str:
    """渲染 /share 公开消息内容（status 不同尾标不同）。

    URL 作为消息正文 → Discord 自动 unfurl OG 卡。
    `-# ` subtext 行展示 link_id / 提交人 / 状态。
    同一 link_id 在 PENDING → terminal 演进时多次重新渲染，格式保持一致。
    """
    if status == "PENDING":
        caption = (
            f"-# ⏳ 已提交分享 · `#{link_id}` · by {user_display_name} "
            f"· AI 审核中（约 30s）"
        )
    elif status == "APPROVED":
        caption = (
            f"-# ✅ 已收录到 [内卷地狱分享库]({_FEED_URL}) "
            f"· `#{link_id}` · by {user_display_name}"
        )
    elif status == "PENDING_MANUAL":
        caption = (
            f"-# 🟡 已提交 · `#{link_id}` · by {user_display_name} "
            f"· 非白名单域名，等待人工复核"
        )
    elif status == "FLAGGED":
        caption = (
            f"-# 🟡 已提交 · `#{link_id}` · by {user_display_name} "
            f"· AI 标记需复核（如误判可私信管理员 appeal）"
        )
    elif status == "REJECTED":
        caption = (
            f"-# ❌ 已提交 · `#{link_id}` · by {user_display_name} "
            f"· 审核未通过"
        )
    elif status == "ARCHIVED":
        caption = (
            f"-# 📦 已提交 · `#{link_id}` · by {user_display_name} "
            f"· 系统已归档（原文失效）"
        )
    else:
        # 未知状态兜底，不让 caption 渲染崩
        caption = (
            f"-# 已提交 · `#{link_id}` · by {user_display_name} · 状态: {status}"
        )

    content = f"{url}\n{caption}"
    if recommendation:
        content = f"> {recommendation}\n{content}"
    return content


async def _safe(coro: Coroutine[Any, Any, Any], *, name: str) -> None:
    """包装 background task：异常打 log 不让 fire-and-forget 静默失败。

    CancelledError 不会被 except Exception 捕获（3.12 起继承自
    BaseException），bot 优雅退出取消 task 不会被这里吞掉。
    """
    try:
        await coro
    except Exception:
        log.exception("share_command_background_task_failed", task=name)


class ShareCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings

    @app_commands.command(
        name="share", description="把链接收录到内卷地狱分享库（频道内公开）"
    )
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

        # 不 thinking（不显示"bot is thinking..."），让首条消息尽快出来
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
            await interaction.followup.send(
                "提交失败，已通知开发者。", ephemeral=True
            )
            return

        # 第一条消息：以 PENDING 状态渲染发出。后台 polling 拿到终态后 edit。
        initial_content = _render_share_message(
            url=url,
            link_id=result.link_id,
            user_display_name=interaction.user.display_name,
            recommendation=recommendation,
            status="PENDING",
        )
        sent = await interaction.followup.send(
            content=initial_content, wait=True
        )

        # 启动后台轮询任务（fire-and-forget，_safe 兜底）
        task_name = f"share_command_poll_{result.link_id}"
        asyncio.create_task(
            _safe(
                self._poll_and_edit(
                    sent_message=sent,
                    link_id=result.link_id,
                    url=url,
                    user_display_name=interaction.user.display_name,
                    recommendation=recommendation,
                ),
                name=task_name,
            ),
            name=task_name,
        )

    async def _poll_and_edit(
        self,
        *,
        sent_message: discord.WebhookMessage,
        link_id: int,
        url: str,
        user_display_name: str,
        recommendation: str | None,
    ) -> None:
        """轮询 /internal/{id} 拿终态，命中后 edit 第一条消息显示真实状态。

        与 listener cog 同款逻辑：每 2s 查一次，最多 30s 后超时静默。
        超时仍 PENDING：不动消息（继续显示"⏳ AI 审核中"），用户可去
        /u/<id>/shares 主动查进度。
        """
        elapsed = 0.0
        while elapsed < _POLL_TIMEOUT_SEC:
            await asyncio.sleep(_POLL_INTERVAL_SEC)
            elapsed += _POLL_INTERVAL_SEC
            try:
                detail = await fetch_link(
                    base_url=self.settings.internal_submit_url,
                    internal_key=self.settings.internal_api_key.get_secret_value(),
                    link_id=link_id,
                    timeout=self.settings.chatbot_api_timeout,
                )
            except Exception as e:
                log.warning(
                    "share_command_poll_error", link_id=link_id, error=str(e)
                )
                return
            if detail is None:
                # 404 不该发生（刚 submit 完就消失？），出现了也只能放弃
                log.warning("share_command_poll_404", link_id=link_id)
                return
            if detail.status in _TERMINAL_STATUSES:
                new_content = _render_share_message(
                    url=url,
                    link_id=link_id,
                    user_display_name=user_display_name,
                    recommendation=recommendation,
                    status=detail.status,
                )
                try:
                    await sent_message.edit(content=new_content)
                except discord.HTTPException as e:
                    log.warning(
                        "share_command_edit_failed",
                        link_id=link_id,
                        error=str(e),
                    )
                return

        log.info("share_command_poll_timeout", link_id=link_id)


async def setup(bot: commands.Bot) -> None:
    settings: Settings = bot.chatbot_settings  # type: ignore[attr-defined]
    await bot.add_cog(ShareCommands(bot, settings))
