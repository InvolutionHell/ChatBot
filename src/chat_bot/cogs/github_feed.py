"""GitHub org 动态播报 Cog：轮询 InvolutionHell org 的公开事件流，
新 PR / PR 合并 / Release 发布 → 艾露猫播报到社区频道。

两个来自实测的坑（2026-07-14）：
1. 事件 ID **不是**全局单调的——PR/issue 类事件和 push/create 类事件在
   不同 ID 段，按"id > 水位线"去重会永远漏掉 PR 事件。所以用已见 ID
   集合去重（state 里存最近 500 个）。
2. 公开事件流的 pull_request payload 被精简过（只剩 number/base/head/
   api url，没有 title / merged / html_url），需要补拉一次 PR 详情；
   拉不到就降级成无标题链接（merged 判不出来则跳过，宁缺毋错）。

无凭证轮询（限额 60 req/h；5 分钟一轮 = 12 次 + 少量 PR 详情），
首次运行只记水位不把历史刷进频道。
"""

from __future__ import annotations

import httpx
import structlog
from discord.ext import commands, tasks

from .. import state
from ..config import Settings

log = structlog.get_logger(__name__)

_ORG = "InvolutionHell"
_API = f"https://api.github.com/orgs/{_ORG}/events?per_page=30"
_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "ih-chatbot"}
_POLL_SECONDS = 300
_MAX_POSTS_PER_POLL = 5  # 一次轮询最多播 5 条，防止批量操作刷屏
_MAX_SEEN_IDS = 500


def _format_event(
    ev: dict, pr_detail: dict | None = None, actor_mention: str | None = None
) -> str | None:
    """GitHub 事件 → 艾露猫播报文案；不关心的事件返回 None。

    pr_detail 是补拉的 PR 详情（可缺）；actor_mention 是解析出的
    Discord @mention（可缺，缺了用 GitHub 登录名）。链接用 <> 包裹
    禁止 Discord unfurl，动态播报不需要大卡片。
    """
    etype = ev.get("type")
    payload = ev.get("payload") or {}
    repo_full = (ev.get("repo") or {}).get("name") or ""
    repo_short = repo_full.split("/")[-1]
    actor = actor_mention or (ev.get("actor") or {}).get("login") or "某位"

    if etype == "PullRequestEvent":
        # 事件流里的 pull_request 是精简版，补拉的详情覆盖在上面
        pr = {**(payload.get("pull_request") or {}), **(pr_detail or {})}
        number = pr.get("number") or payload.get("number")
        if not number:
            return None
        title = str(pr.get("title") or "").strip()
        html = pr.get("html_url") or f"https://github.com/{repo_full}/pull/{number}"
        label = f"#{number}" + (f" {title[:80]}" if title else "")
        link = f"[{label}](<{html}>)"
        if payload.get("action") == "opened":
            return f"🐙 {actor} 老大在 `{repo_short}` 开了新 PR：{link}"
        if payload.get("action") == "closed" and pr.get("merged"):
            return f"🎉 `{repo_short}` 合并了 {link}，感谢 {actor} 老大喵～"
        return None

    if etype == "ReleaseEvent" and payload.get("action") == "published":
        rel = payload.get("release") or {}
        tag = rel.get("tag_name") or "新版本"
        html = rel.get("html_url") or f"https://github.com/{repo_full}/releases"
        return f"📦 `{repo_short}` 发布新版本 [{tag}](<{html}>)！"

    return None


def _needs_pr_detail(ev: dict) -> str | None:
    """需要补拉 PR 详情的事件返回其 API url，否则 None。"""
    if ev.get("type") != "PullRequestEvent":
        return None
    payload = ev.get("payload") or {}
    if payload.get("action") not in ("opened", "closed"):
        return None
    return (payload.get("pull_request") or {}).get("url")


class GithubFeed(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings
        self._seen: list = list(state.load("github_feed").get("seen_ids", []))
        self._loop = tasks.loop(seconds=_POLL_SECONDS)(self._run)
        self._loop.before_loop(self._before)

    def cog_load(self) -> None:
        self._loop.start()

    def cog_unload(self) -> None:
        self._loop.cancel()

    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    async def _resolve_actor_mention(
        self, client: httpx.AsyncClient, login: str
    ) -> str | None:
        """GitHub 登录名 → Discord @mention；解析不出就返回 None（不艾特）。

        Discord 不向 bot 暴露任何成员邮箱，按邮箱对账号走不通。两层解析：
        1. 人工映射表 .state/github_discord_map.json
           {github_login 小写: discord_user_id}——GitHub 和 Discord 名字
           对不上的人手工补一条即可
        2. 成员搜索接口按名字精确匹配（大小写不敏感，比对 username /
           global_name / 服务器昵称）。该接口实测不需要 GUILD_MEMBERS
           特权 intent。前缀匹配命中但名字不完全一致 → 宁可不艾特
        """
        if not login:
            return None
        overrides = state.load("github_discord_map")
        uid = overrides.get(login.lower())
        if uid:
            return f"<@{uid}>"
        guild_id = self.settings.discord_guild_id
        if not guild_id:
            return None
        try:
            r = await client.get(
                f"https://discord.com/api/v10/guilds/{guild_id}/members/search",
                params={"query": login, "limit": 5},
                headers={
                    "Authorization": (
                        f"Bot {self.settings.discord_bot_token.get_secret_value()}"
                    )
                },
            )
            if r.status_code != 200:
                return None
            for m in r.json():
                u = m.get("user") or {}
                names = {
                    str(u.get("username") or "").lower(),
                    str(u.get("global_name") or "").lower(),
                    str(m.get("nick") or "").lower(),
                }
                if login.lower() in names:
                    return f"<@{u.get('id')}>"
        except Exception as e:
            log.warning("github_actor_resolve_failed", login=login, error=str(e))
        return None

    async def _run(self) -> None:
        if not self.settings.community_feed_channel_id:
            return
        try:
            async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
                resp = await client.get(_API)
                if resp.status_code != 200:
                    log.warning("github_poll_bad_status", status=resp.status_code)
                    return
                events = resp.json()
                if not isinstance(events, list) or not events:
                    return

                first_run = not self._seen
                seen = set(self._seen)
                fresh = [e for e in events if e.get("id") not in seen]
                # 已见集合滚动更新：新 ID 在前，截断到上限
                self._seen = ([e["id"] for e in events if e.get("id")] + self._seen)[
                    :_MAX_SEEN_IDS
                ]
                state.save("github_feed", {"seen_ids": self._seen})
                if first_run:
                    # 首次运行只记水位，不把历史事件刷进频道
                    return
                if not fresh:
                    return

                # 事件流是新→旧，倒过来按时间顺序播；PR 事件补拉详情
                texts = []
                for ev in reversed(fresh):
                    detail = None
                    detail_url = _needs_pr_detail(ev)
                    if detail_url:
                        try:
                            d = await client.get(detail_url)
                            if d.status_code == 200:
                                detail = d.json()
                        except Exception as e:
                            log.warning("github_pr_detail_failed", error=str(e))
                    # 先判断可播，再花一次请求解析作者的 Discord 账号
                    if _format_event(ev, detail) is None:
                        continue
                    mention = await self._resolve_actor_mention(
                        client, (ev.get("actor") or {}).get("login") or ""
                    )
                    texts.append(_format_event(ev, detail, mention))
        except Exception as e:
            log.warning("github_poll_failed", error=str(e))
            return

        if len(texts) > _MAX_POSTS_PER_POLL:
            log.info("github_feed_truncated", dropped=len(texts) - _MAX_POSTS_PER_POLL)
            texts = texts[-_MAX_POSTS_PER_POLL:]
        if not texts:
            return

        channel = self.bot.get_channel(self.settings.community_feed_channel_id)
        if channel is None:
            log.warning("github_feed_channel_not_found")
            return
        for text in texts:
            try:
                await channel.send(text)
            except Exception as e:
                log.error("github_feed_send_failed", error=str(e))
        log.info("github_feed_posted", count=len(texts))


async def setup(bot: commands.Bot) -> None:
    settings: Settings = bot.chatbot_settings  # type: ignore[attr-defined]
    await bot.add_cog(GithubFeed(bot, settings))
