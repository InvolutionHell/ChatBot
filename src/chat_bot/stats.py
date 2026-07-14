"""社区周报的轻量事件计数：join / share / star 各记一串时间戳。

welcome / milestones / starboard 在事件发生时 bump 一下，weekly cog 周五
汇总。只留最近 14 天的时间戳（周报窗口 7 天，留一倍余量），文件不会膨胀。
"""

from __future__ import annotations

import time

from . import state

_RETENTION_SEC = 14 * 86400


def bump(kind: str) -> None:
    """记一次事件（kind: join / share / star），顺手修剪过期时间戳。"""
    data = state.load("weekly_stats")
    now = int(time.time())
    timestamps = [t for t in data.get(kind, []) if now - t < _RETENTION_SEC]
    timestamps.append(now)
    data[kind] = timestamps
    state.save("weekly_stats", data)


def counts_since(seconds: int) -> dict:
    """返回最近 seconds 秒内各类事件的次数，如 {"join": 3, "share": 5}。"""
    data = state.load("weekly_stats")
    cutoff = int(time.time()) - seconds
    return {k: sum(1 for t in v if t >= cutoff) for k, v in data.items()}
