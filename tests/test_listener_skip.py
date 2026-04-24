"""listener._should_skip 过滤 Discord 自身链接的测试。

不起 Discord.Client，只测这个纯函数就够了——Cog 层的集成测试价值低（见 README）。
"""

from __future__ import annotations

import pytest

from chat_bot.cogs.listener import _should_skip


@pytest.mark.parametrize(
    "url",
    [
        # 深链消息
        "https://discord.com/channels/1243830688315342860/1243830688998752279/1496945561784549386",
        # 邀请
        "https://discord.gg/invitecode",
        # 主站其它
        "https://www.discord.com/",
        "https://canary.discord.com/channels/foo/bar",
        "https://ptb.discord.com/channels/foo/bar",
        # CDN / 附件
        "https://cdn.discordapp.com/attachments/xxx/yyy/file.png",
        "https://media.discordapp.net/attachments/xxx/yyy/image.jpg",
        "https://discordapp.com/something",
    ],
)
def test_should_skip_discord_urls(url: str) -> None:
    assert _should_skip(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://arxiv.org/abs/2501.00001",
        "https://mp.weixin.qq.com/s/abc",
        "https://github.com/InvolutionHell/ChatBot",
        "https://scholar.google.com/scholar?q=rag",
        # 只有 host 相似但不完全匹配就不该 skip（防范未来新域名放行策略）
        "https://not-discord.com/x",
        "https://discord.com.evil.com/phishing",
    ],
)
def test_should_not_skip_other_urls(url: str) -> None:
    assert _should_skip(url) is False


def test_should_skip_handles_bad_url_gracefully() -> None:
    # 坏 URL 不应抛异常；当前 urlparse 对大多数输入都不抛，兜底返回 False
    assert _should_skip("not-a-url") is False
    assert _should_skip("") is False


def test_should_skip_is_case_insensitive() -> None:
    # 大小写混杂也要 skip（URL host 实际上总是小写但保险起见）
    assert _should_skip("https://DISCORD.com/channels/x/y/z") is True
    assert _should_skip("https://Cdn.DiscordApp.com/file.png") is True
