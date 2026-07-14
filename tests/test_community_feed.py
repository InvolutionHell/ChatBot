"""社区动态三件套的纯逻辑测试：stats 计数 / 周报文案 / GitHub 事件格式化 /
MC 事件格式化。Cog 的 Discord 胶水层不测（见 README）。"""

from __future__ import annotations

from chat_bot.cogs.alerts import _format_mc_event
from chat_bot.cogs.github_feed import _format_event
from chat_bot.cogs.weekly import _compose


def test_stats_bump_and_count(tmp_path, monkeypatch) -> None:
    from chat_bot import state as state_mod
    from chat_bot import stats

    monkeypatch.setattr(state_mod, "_STATE_DIR", tmp_path)
    stats.bump("join")
    stats.bump("join")
    stats.bump("share")
    counts = stats.counts_since(3600)
    assert counts["join"] == 2
    assert counts["share"] == 1
    # 窗口外的不算（负窗口 → cutoff 在未来，一定全部落在窗口外）
    assert stats.counts_since(-1) == {"join": 0, "share": 0}


def test_weekly_compose() -> None:
    assert _compose({}, "2026-07-17") is None
    assert _compose({"join": 0, "share": 0, "star": 0}, "2026-07-17") is None
    text = _compose({"join": 3, "share": 5, "star": 2}, "2026-07-17")
    assert text is not None
    assert "3" in text and "5" in text and "2" in text
    assert "2026-07-17" in text


def _trimmed_pr_event(action: str) -> dict:
    """公开事件流的真实形态：pull_request 被精简，只剩 number/url。"""
    return {
        "type": "PullRequestEvent",
        "actor": {"login": "alice"},
        "repo": {"name": "InvolutionHell/involutionhell"},
        "payload": {
            "action": action,
            "number": 365,
            "pull_request": {
                "number": 365,
                "url": "https://api.github.com/repos/InvolutionHell/involutionhell/pulls/365",
            },
        },
    }


def test_github_format_pr_opened_with_detail() -> None:
    detail = {
        "title": "feat: add mcp server",
        "html_url": "https://github.com/InvolutionHell/involutionhell/pull/365",
        "merged": False,
    }
    text = _format_event(_trimmed_pr_event("opened"), detail)
    assert text is not None
    assert "alice" in text and "#365" in text and "feat: add mcp server" in text
    assert "None" not in text


def test_github_format_pr_opened_without_detail_degrades() -> None:
    """补拉失败：无标题但链接可构造，绝不能渲染出 'None'。"""
    text = _format_event(_trimmed_pr_event("opened"), None)
    assert text is not None
    assert "#365" in text
    assert "github.com/InvolutionHell/involutionhell/pull/365" in text
    assert "None" not in text


def test_github_format_actor_mention() -> None:
    """解析出 Discord 账号时用 @mention 替代 GitHub 登录名。"""
    detail = {"title": "x", "html_url": "https://g/1"}
    text = _format_event(_trimmed_pr_event("opened"), detail, actor_mention="<@1078>")
    assert text is not None
    assert "<@1078>" in text
    assert "alice" not in text  # mention 替代而非并列


def test_github_format_pr_merged_needs_detail() -> None:
    # merged 标志只在详情里；拉到了才播，拉不到宁可跳过
    detail = {"title": "x", "html_url": "https://g/1", "merged": True}
    assert _format_event(_trimmed_pr_event("closed"), detail) is not None
    assert _format_event(_trimmed_pr_event("closed"), None) is None
    # 不关心的事件类型
    assert _format_event({"type": "WatchEvent", "payload": {}}) is None


def test_mc_event_format() -> None:
    text = _format_mc_event({"type": "mc_join", "player": "Abore", "online": 2})
    assert text is not None
    assert "Abore" in text and "2" in text
    adv = _format_mc_event(
        {"type": "mc_advancement", "player": "Abore", "advancement": "How Did We Get Here?"}
    )
    assert adv is not None
    assert "How Did We Get Here?" in adv
    # 未知类型 / 缺玩家名
    assert _format_mc_event({"type": "mc_death", "player": "x"}) is None
    assert _format_mc_event({"type": "mc_join"}) is None
