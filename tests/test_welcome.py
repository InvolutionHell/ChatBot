"""welcome.RookieWelcome 的过滤逻辑测试。

不起 Discord.Client，用假 message 对象直接调 on_message，
验证欢迎 / Boost 感谢 / 周年贺词各自的触发与不触发。
"""

from __future__ import annotations

import asyncio
import datetime
from types import SimpleNamespace

import discord

from chat_bot.cogs.welcome import RookieWelcome, _anniversary_years

_GUILD_ID = 1243830688315342860
_UTC = datetime.UTC


def _make_cog() -> RookieWelcome:
    settings = SimpleNamespace(discord_guild_id=_GUILD_ID)
    return RookieWelcome(bot=None, settings=settings)  # type: ignore[arg-type]


def _make_message(
    *,
    msg_type: discord.MessageType = discord.MessageType.new_member,
    author_is_bot: bool = False,
    guild_id: int | None = _GUILD_ID,
    joined_at: datetime.datetime | None = None,
) -> tuple[SimpleNamespace, list]:
    """返回 (假 message, reply 调用记录)。"""
    calls: list = []

    async def reply(content: str, **kwargs: object) -> None:
        calls.append(content)

    author = SimpleNamespace(bot=author_is_bot, mention="<@42>", id=42)
    if joined_at is not None:
        author.joined_at = joined_at
    message = SimpleNamespace(
        type=msg_type,
        author=author,
        guild=SimpleNamespace(id=guild_id) if guild_id else None,
        reply=reply,
    )
    return message, calls


def test_welcomes_new_member() -> None:
    cog = _make_cog()
    message, calls = _make_message()
    asyncio.run(cog.on_message(message))  # type: ignore[arg-type]
    assert len(calls) == 1
    assert "<@42>" in calls[0]


def test_ignores_normal_message() -> None:
    cog = _make_cog()
    message, calls = _make_message(msg_type=discord.MessageType.default)
    asyncio.run(cog.on_message(message))  # type: ignore[arg-type]
    assert calls == []


def test_ignores_joining_bot() -> None:
    cog = _make_cog()
    message, calls = _make_message(author_is_bot=True)
    asyncio.run(cog.on_message(message))  # type: ignore[arg-type]
    assert calls == []


def test_ignores_other_guild() -> None:
    cog = _make_cog()
    message, calls = _make_message(guild_id=999)
    asyncio.run(cog.on_message(message))  # type: ignore[arg-type]
    assert calls == []


def test_thanks_booster() -> None:
    cog = _make_cog()
    message, calls = _make_message(msg_type=discord.MessageType.premium_guild_subscription)
    asyncio.run(cog.on_message(message))  # type: ignore[arg-type]
    assert len(calls) == 1
    assert "Boost" in calls[0]


def test_anniversary_years() -> None:
    joined = datetime.datetime(2024, 7, 14, 8, 0, tzinfo=_UTC)
    now = datetime.datetime(2026, 7, 14, 8, 0, tzinfo=_UTC)
    assert _anniversary_years(joined, now) == 2
    # 月/日不同 → 不是周年
    assert _anniversary_years(joined, now.replace(day=15)) is None
    # 当年刚加入 → 不算周年
    assert _anniversary_years(now, now) is None


def test_anniversary_congratulated_once(tmp_path, monkeypatch) -> None:
    from chat_bot import state as state_mod

    monkeypatch.setattr(state_mod, "_STATE_DIR", tmp_path)
    fixed_now = datetime.datetime(2026, 7, 14, 8, 0, tzinfo=_UTC)
    monkeypatch.setattr(discord.utils, "utcnow", lambda: fixed_now)

    cog = _make_cog()
    message, calls = _make_message(
        msg_type=discord.MessageType.default,
        joined_at=datetime.datetime(2025, 7, 14, 3, 0, tzinfo=_UTC),
    )
    asyncio.run(cog.on_message(message))  # type: ignore[arg-type]
    asyncio.run(cog.on_message(message))  # type: ignore[arg-type]
    # 同一天发言两次，只贺一次
    assert len(calls) == 1
    assert "1 周年" in calls[0]
