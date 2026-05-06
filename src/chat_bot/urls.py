"""统一管理对外暴露的官网链接 + UTM 参数。

为什么集中管：
- 每条链接的 UTM 含义跟"这条消息从哪个渠道发出去"绑死，散到 4 个 cog 里
  改一次 source/medium 命名要 grep 全仓，容易漏。
- GA Source/Medium 维度依赖参数命名稳定，硬编码字符串容易拼错（utm_souce 之类）。

UTM 命名约定（参考 GA4 推荐做法）：
- utm_source = 流量入口（discord / email_alert / email_digest / github / ...）
- utm_medium = 入口类型（community / admin / readme / social / ...）
- utm_campaign = 触发场景（share_command / share_approved / ...），用于细分同一来源的不同消息
"""

from __future__ import annotations

from urllib.parse import urlencode

_BASE = "https://involutionhell.com"


def _with_utm(path: str, *, source: str, medium: str, campaign: str) -> str:
    """给指定路径拼上标准化 UTM。"""
    qs = urlencode(
        {
            "utm_source": source,
            "utm_medium": medium,
            "utm_campaign": campaign,
        }
    )
    return f"{_BASE}{path}?{qs}"


# ── Discord 用户可见（三个分享流程的不同阶段）──────────────────────────
def feed_url_share_command() -> str:
    """/share 命令成功后展示给提交者的链接。"""
    return _with_utm("/feed", source="discord", medium="community", campaign="share_command")


def feed_url_share_listener() -> str:
    """监听到分享、首条 reply 告知正在审核。"""
    return _with_utm("/feed", source="discord", medium="community", campaign="share_listener")


def feed_url_share_approved() -> str:
    """审核通过后第二条 reply 通知已上架。"""
    return _with_utm("/feed", source="discord", medium="community", campaign="share_approved")


# ── 管理员邮件正文（区分告警邮件 vs 摘要邮件）───────────────────────────
def admin_review_url_email_alert() -> str:
    """单条命中告警邮件里的复核入口。"""
    return _with_utm(
        "/admin/community", source="email_alert", medium="admin", campaign="flagged_review"
    )


def admin_review_url_email_digest() -> str:
    """日报摘要邮件里的统一处理入口。"""
    return _with_utm(
        "/admin/community", source="email_digest", medium="admin", campaign="daily_review"
    )
