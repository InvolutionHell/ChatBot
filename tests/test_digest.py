"""digest Cog 的纯函数单测（_parse_hhmm）。"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from chat_bot.cogs.digest import _parse_hhmm


def test_parse_hhmm_valid():
    t = _parse_hhmm("09:00")
    assert t.hour == 9
    assert t.minute == 0
    assert t.tzinfo == ZoneInfo("Asia/Shanghai")


def test_parse_hhmm_valid_evening():
    t = _parse_hhmm("21:30")
    assert t.hour == 21
    assert t.minute == 30


def test_parse_hhmm_invalid_falls_back_to_9am():
    # 配置错误时不该让 cog_load 崩，降级到 09:00 继续跑
    t = _parse_hhmm("bad-format")
    assert t.hour == 9
    assert t.minute == 0


def test_parse_hhmm_empty_falls_back():
    t = _parse_hhmm("")
    assert t.hour == 9
    assert t.minute == 0
