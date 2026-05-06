"""频道消息监听 Cog：从消息里抽链接 → 调后端 internal API → 回用户告知结果。

UX 流程：
1. 用户在 #分享 频道发带链接消息
2. Bot 立即 reply @用户：「感谢大佬分享！正在审核」
3. 后端 @Async SharedLinkEnrichmentWorker 跑 OG + DeepSeek，Bot 后台轮询
4. 拿到终态后 Bot 再 reply：
   - APPROVED       → 安静收尾（首条 reply 已经说"感谢"）
   - PENDING_MANUAL → 「需要人工复核，稍后上架。有疑问找管理员」
   - FLAGGED        → 「AI 复核触发 xxx flag，管理员已收到通知」
5. 轮询最多 30s（enrichment 通常 2-5s 内完成），超时静默
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Coroutine
from typing import Any
from urllib.parse import urlparse

import discord
import structlog
from discord.ext import commands

from ..api_client import DuplicateURL, InternalAPIError, fetch_link, submit_internal
from ..config import Settings
from ..urls import feed_url_share_approved, feed_url_share_listener

_URL_RE = re.compile(r"https?://[^\s<>\"'\]\)]+", re.IGNORECASE)

# 跳过的链接源。两层：
#   1. Discord 自身（消息链接 / 附件 CDN）—— 用户复制消息链接时常误粘
#   2. 贴纸 / GIF / meme 聚合站 —— Discord 内置贴纸面板会发 tenor/klipy/giphy
#      链接出来，message.content 里就是裸 URL。这些不是"分享资源"，不该入库
# 静默忽略，不回复不提交，像 bot 没看到一样。
_SKIP_HOSTS = frozenset({
    # Discord 主站
    "discord.com",
    "www.discord.com",
    "canary.discord.com",
    "ptb.discord.com",
    # Discord 邀请短链
    "discord.gg",
    # Discord 附件 / CDN
    "discordapp.com",
    "cdn.discordapp.com",
    "media.discordapp.net",
    # 贴纸 / GIF 聚合（Discord 贴纸面板默认走这些）
    "tenor.com",
    "media.tenor.com",
    "c.tenor.com",
    "giphy.com",
    "media.giphy.com",
    "media0.giphy.com",
    "media1.giphy.com",
    "media2.giphy.com",
    "media3.giphy.com",
    "media4.giphy.com",
    "klipy.com",
    "media.klipy.com",
})

# 兜底：只指向静态媒体文件的 URL（路径以这些扩展名结尾）一律跳过——常见于
# WeChat / 各种图床的裸图片链接，非分享资源。把扩展名匹配做在 path 上避免误伤
# 带 query 的正常链接（query 里出现 .jpg 不算）。
_MEDIA_EXTENSIONS = (
    ".gif",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".svg",
    ".ico",
    ".mp4",
    ".webm",
    ".mov",
    ".m4v",
    ".mp3",
    ".wav",
    ".ogg",
    ".flac",
)


_INTERNAL_GITHUB_ORG = "involutionhell"  # GitHub 路径不区分大小写，统一比小写


def _is_self_org_github_chatter(parsed) -> bool:
    """github.com/InvolutionHell/<repo>/<sub-path> 视为内部 dev 讨论，不入分享库。

    放行 case：
      - github.com/InvolutionHell/<repo>          仓库主页（"安利自家工具"这种正常分享）
      - github.com/InvolutionHell/<repo>/         同上，带尾斜杠
      - github.com/InvolutionHell                 org 主页（罕见，但放行）
      - github.com/<其它 org>/...                 第三方仓库的任何路径

    拦截 case：
      - github.com/InvolutionHell/<repo>/pull/N
      - github.com/InvolutionHell/<repo>/issues/N
      - github.com/InvolutionHell/<repo>/commit/<sha>
      - github.com/InvolutionHell/<repo>/blob/...
      - github.com/InvolutionHell/<repo>/tree/...
      - github.com/InvolutionHell/<repo>/actions/...
      - github.com/InvolutionHell/<repo>/discussions/...
      - github.com/InvolutionHell/<repo>/releases/tag/...
      —— 这些是 PR/issue 自动通知或 dev 联调时贴的，不是给社区"上架"的资源
    """
    host = parsed.netloc.lower().split(":")[0]
    if host not in {"github.com", "www.github.com"}:
        return False
    segs = [s for s in parsed.path.split("/") if s]
    # /<org>/<repo>/<sub-path...>  (>= 3 段才算 dev 子路径)
    return len(segs) >= 3 and segs[0].lower() == _INTERNAL_GITHUB_ORG


def _should_skip(url: str) -> bool:
    """URL 是否属于需要跳过的源：Discord 域、贴纸聚合、自家 GitHub dev 子路径、或裸媒体文件。"""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = parsed.netloc.lower().split(":")[0]
    if host in _SKIP_HOSTS:
        return True
    if _is_self_org_github_chatter(parsed):
        return True
    # path 走小写匹配，跟 query 解耦：?foo=bar.jpg 不会误命中
    return parsed.path.lower().endswith(_MEDIA_EXTENSIONS)

# 轮询最终状态的参数：每 2s 查一次，最多 30s
_POLL_INTERVAL_SEC = 2.0
_POLL_TIMEOUT_SEC = 30.0
_TERMINAL_STATUSES = {"APPROVED", "PENDING_MANUAL", "FLAGGED", "REJECTED", "ARCHIVED"}

# flag → 可读原因（中文）
_FLAG_REASON = {
    "nsfw": "不适内容",
    "ad": "疑似广告",
    "flame": "疑似引战",
}

log = structlog.get_logger(__name__)


async def _safe(coro: Coroutine[Any, Any, Any], *, name: str) -> None:
    """包装 background coroutine：异常打进 log，不让 fire-and-forget task 静默失败。

    注意：except Exception 不会捕到 CancelledError（3.12 起 CancelledError
    继承自 BaseException），所以 bot 优雅退出时取消 task 的行为不会被这里吞掉。
    """
    try:
        await coro
    except Exception:
        log.exception("background_task_failed", task=name)


class ShareListener(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.webhook_id is not None:
            return
        if message.channel.id not in self.settings.watch_channel_ids:
            return

        urls = _URL_RE.findall(message.content)
        if not urls:
            return

        for url in urls:
            await self._handle_one_url(message, url)

    async def _handle_one_url(self, message: discord.Message, url: str) -> None:
        """提交单个 URL，并根据后端响应给用户即时反馈 + 延迟最终状态通知。"""
        if _should_skip(url):
            # Discord 自身链接静默忽略——不提交、不回复、不打扰群聊
            log.debug("share_skip_blocked_host", url=url)
            return
        try:
            result = await submit_internal(
                base_url=self.settings.internal_submit_url,
                internal_key=self.settings.internal_api_key.get_secret_value(),
                url=url,
                submitter_label=message.author.display_name,
                timeout=self.settings.chatbot_api_timeout,
            )
        except DuplicateURL:
            log.info("share_duplicate", url=url, submitter=message.author.display_name)
            # 去重也给个反馈，不让用户发完一头雾水
            await self._safe_reply(
                message,
                f"感谢 {message.author.mention} 分享！这条链接已经在分享库里啦 ✨",
            )
            return
        except InternalAPIError as e:
            log.error("share_api_error", url=url, status=e.status, message=e.message)
            await self._safe_reply(
                message,
                f"{message.author.mention} 提交这条链接时后端返回了 {e.status}，"
                "已通知管理员排查 🙏",
            )
            return
        except Exception as e:
            log.error("share_unexpected_error", url=url, error=str(e))
            await self._safe_reply(
                message,
                f"{message.author.mention} 提交出错了，已通知管理员 🙏",
            )
            return

        log.info(
            "share_ingested",
            url=url,
            link_id=result.link_id,
            status=result.status,
            submitter=message.author.display_name,
        )

        # 第一条 reply：立即感谢，告诉用户已经在审核
        await self._safe_reply(
            message,
            f"感谢 {message.author.mention} 大佬分享！正在过审核，"
            f"通过后会上架 [内卷地狱分享库](<{feed_url_share_listener()}>) #{result.link_id}",
        )

        # 后台轮询拿最终状态，拿到了再发第二条
        task_name = f"poll_{result.link_id}"
        asyncio.create_task(
            _safe(self._notify_final_status(message, result.link_id), name=task_name),
            name=task_name,
        )

    async def _notify_final_status(self, message: discord.Message, link_id: int) -> None:
        """后台任务：轮询 /internal/{id} 拿终态，再 reply 通知用户。"""
        deadline = _POLL_TIMEOUT_SEC
        elapsed = 0.0
        while elapsed < deadline:
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
                log.warning("poll_final_status_failed", link_id=link_id, error=str(e))
                return
            if detail is None:
                return
            if detail.status in _TERMINAL_STATUSES:
                await self._send_status_update(message, detail, link_id)
                return

        log.info("poll_final_status_timeout", link_id=link_id)

    async def _send_status_update(
        self, message: discord.Message, detail, link_id: int
    ) -> None:
        """按终态发对应的 reply。"""
        user = message.author.mention
        status = detail.status

        if status == "APPROVED":
            # 已自动通过——第一条 reply 已经说了，这里短讯收尾即可
            await self._safe_reply(
                message,
                f"🎉 {user} 已上架 · #{link_id} "
                f"[点此查看](<{feed_url_share_approved()}>)",
            )
            return

        if status == "PENDING_MANUAL":
            await self._safe_reply(
                message,
                f"⏳ {user} 非白名单域名（`{detail.host}`），"
                f"需要管理员人工复核 · #{link_id}。若长时间未通过可私信管理员 🙏",
            )
            return

        if status == "FLAGGED":
            # 无法直接从 LinkDetail 拿到 flags，只能给个通用文案
            await self._safe_reply(
                message,
                f"⚠️ {user} AI 审核认为这条命中敏感标签（nsfw / 广告 / 引战 其一），"
                f"已进入人工复核队列 · #{link_id}。"
                f"如果你认为是误判，欢迎私信管理员 appeal 🙏",
            )
            return

        if status == "REJECTED":
            await self._safe_reply(
                message,
                f"❌ {user} 这条已被管理员拒绝 · #{link_id}。"
                f"如有疑问欢迎私信管理员",
            )
            return

        if status == "ARCHIVED":
            await self._safe_reply(
                message,
                f"📦 {user} 这条链接已被系统归档（原文失效）· #{link_id}",
            )
            return

    @staticmethod
    async def _safe_reply(message: discord.Message, content: str) -> None:
        """message.reply 有可能失败（消息被删 / 权限变更），catch 住不让后台任务炸。"""
        try:
            await message.reply(content, mention_author=False)
        except Exception as e:
            log.warning("reply_failed", error=str(e))


async def setup(bot: commands.Bot) -> None:
    settings: Settings = bot.chatbot_settings  # type: ignore[attr-defined]
    await bot.add_cog(ShareListener(bot, settings))
