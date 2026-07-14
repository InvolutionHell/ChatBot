"""starboard.Starboard 的阈值 / 去重 / emoji 过滤测试。

不起 Discord.Client，用假 bot / channel / message 直接调 on_raw_reaction_add。
"""

from __future__ import annotations

import asyncio
import datetime
from types import SimpleNamespace

from chat_bot.cogs.starboard import Starboard

_BOARD_ID = 999
_SRC_ID = 1


class _FakeChannel:
    def __init__(self, cid: int, message=None) -> None:
        self.id = cid
        self.name = "common"
        self._message = message
        self.sent: list = []

    async def fetch_message(self, mid: int):
        return self._message

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=777)


def _make_message(stars: int):
    return SimpleNamespace(
        id=10,
        content="好东西",
        reactions=[SimpleNamespace(emoji="⭐", count=stars)],
        author=SimpleNamespace(
            display_name="u",
            display_avatar=SimpleNamespace(url="https://cdn.example/a.png"),
        ),
        jump_url="https://discord.com/channels/1/2/10",
        created_at=datetime.datetime(2026, 7, 14, tzinfo=datetime.UTC),
        attachments=[],
        channel=SimpleNamespace(name="common"),
    )


def _make_cog(stars: int) -> tuple[Starboard, _FakeChannel]:
    board = _FakeChannel(_BOARD_ID)
    src = _FakeChannel(_SRC_ID, message=_make_message(stars))
    bot = SimpleNamespace(get_channel=lambda cid: {_SRC_ID: src, _BOARD_ID: board}.get(cid))
    settings = SimpleNamespace(starboard_channel_id=_BOARD_ID, starboard_threshold=3)
    return Starboard(bot=bot, settings=settings), board  # type: ignore[arg-type]


def _payload(emoji: str = "⭐", channel_id: int = _SRC_ID, message_id: int = 10):
    return SimpleNamespace(emoji=emoji, channel_id=channel_id, message_id=message_id)


def test_posts_at_threshold_and_dedupes(tmp_path, monkeypatch) -> None:
    from chat_bot import state as state_mod

    monkeypatch.setattr(state_mod, "_STATE_DIR", tmp_path)
    cog, board = _make_cog(stars=3)
    asyncio.run(cog.on_raw_reaction_add(_payload()))  # type: ignore[arg-type]
    asyncio.run(cog.on_raw_reaction_add(_payload()))  # type: ignore[arg-type]
    # 第 4 颗星再触发也不重复上墙
    assert len(board.sent) == 1
    embed = board.sent[0]["embed"]
    assert "好东西" in embed.description
    assert "跳转到原消息" in embed.description


def test_below_threshold_no_post(tmp_path, monkeypatch) -> None:
    from chat_bot import state as state_mod

    monkeypatch.setattr(state_mod, "_STATE_DIR", tmp_path)
    cog, board = _make_cog(stars=2)
    asyncio.run(cog.on_raw_reaction_add(_payload()))  # type: ignore[arg-type]
    assert board.sent == []


def test_other_emoji_ignored(tmp_path, monkeypatch) -> None:
    from chat_bot import state as state_mod

    monkeypatch.setattr(state_mod, "_STATE_DIR", tmp_path)
    cog, board = _make_cog(stars=3)
    asyncio.run(cog.on_raw_reaction_add(_payload(emoji="👍")))  # type: ignore[arg-type]
    assert board.sent == []


def test_board_repost_not_restarred(tmp_path, monkeypatch) -> None:
    """精华墙和源频道是同一个频道时（当前贴在 #common）：
    原消息可以上墙，但墙上的转贴消息（id=777）被星不会再套娃上墙。"""
    from chat_bot import state as state_mod

    monkeypatch.setattr(state_mod, "_STATE_DIR", tmp_path)
    cog, board = _make_cog(stars=3)
    asyncio.run(cog.on_raw_reaction_add(_payload()))  # type: ignore[arg-type]
    asyncio.run(
        cog.on_raw_reaction_add(_payload(message_id=777))  # type: ignore[arg-type]
    )
    assert len(board.sent) == 1
