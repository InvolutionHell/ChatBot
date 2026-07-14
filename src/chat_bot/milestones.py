"""分享过审计数与里程碑：第 1 / 10 / 50 / 100 条过审时值得庆祝一下。

只统计经 Discord 渠道（listener / /share 命令）过审的分享——网页端投稿
不经过 bot，Discord 侧的庆祝也只关心 Discord 侧的贡献。
listener 和 commands 两个 cog 都调这里，避免 cog 间互相 import。
"""

from __future__ import annotations

from . import state, stats

_MILESTONES = frozenset({1, 10, 50, 100})


def record_approval(user_id: int) -> int | None:
    """记一条过审；恰好踩中里程碑时返回累计数，否则返回 None。"""
    stats.bump("share")  # 周报计数：过审记录本身就是"分享上架"事件
    counts = state.load("milestones")
    n = counts.get(str(user_id), 0) + 1
    counts[str(user_id)] = n
    state.save("milestones", counts)
    return n if n in _MILESTONES else None


def milestone_message(mention: str, n: int) -> str:
    """艾露猫的里程碑贺词（persona 见 ~/.hermes/SOUL.md，对每个人都叫老大）。"""
    if n == 1:
        return f"🏅 {mention} 老大的第一条分享上架啦！万事开头难，艾露猫给老大记了一功喵～"
    return f"🏅 {mention} 老大已经有 **{n}** 条分享上架了！受艾露猫一拜 🐾"
