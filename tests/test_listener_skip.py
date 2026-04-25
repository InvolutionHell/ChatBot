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
        # 这次实事故的 klipy GIF
        "https://klipy.com/gifs/hello-8126--k01KQ1SBY07FP9N8QRABJGVNGQC",
        # Tenor（Discord 贴纸面板默认）
        "https://tenor.com/view/cat-cute-gif-1234567",
        "https://media.tenor.com/AbCdEfGhIj/cat.gif",
        # Giphy（也常见）
        "https://giphy.com/gifs/cat-cute-AbCdEfGhIj",
        "https://media2.giphy.com/media/AbCdEfGhIj/giphy.gif",
        # Klipy CDN
        "https://media.klipy.com/some.gif",
    ],
)
def test_should_skip_sticker_gif_aggregators(url: str) -> None:
    assert _should_skip(url) is True


@pytest.mark.parametrize(
    "url",
    [
        # 裸图片（WeChat 图床、随便哪个 host 的图片直链）
        "https://mmbiz.qpic.cn/mmbiz_jpg/abc/640.jpg",
        "https://example.com/path/photo.PNG",
        "https://i.example.com/cat.gif",
        "https://example.com/foo.webp",
        # 视频/音频直链
        "https://example.com/clip.mp4",
        "https://example.com/audio.mp3",
        # SVG（即便 host 不在黑名单也拦，配合服务端 SVG 上传黑名单）
        "https://example.com/icon.svg",
    ],
)
def test_should_skip_bare_media_files(url: str) -> None:
    assert _should_skip(url) is True


@pytest.mark.parametrize(
    "url",
    [
        # path 不带媒体扩展，但 query 里出现 .jpg —— 不该误命中
        "https://example.com/api?file=foo.jpg",
        # 微信公众号文章 URL（典型分享）
        "https://mp.weixin.qq.com/s/abc",
        # 小红书帖子（path 没扩展名）
        "https://www.xiaohongshu.com/explore/abc123",
    ],
)
def test_should_not_skip_normal_articles_with_media_query(url: str) -> None:
    assert _should_skip(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://arxiv.org/abs/2501.00001",
        "https://mp.weixin.qq.com/s/abc",
        # 自家仓库主页是合法分享（"看看我们的新工具"），允许
        "https://github.com/InvolutionHell/ChatBot",
        "https://github.com/InvolutionHell/ChatBot/",
        # 第三方仓库的任何路径都允许
        "https://github.com/torvalds/linux/commit/abc123",
        "https://github.com/openai/openai-python/pull/42",
        "https://scholar.google.com/scholar?q=rag",
        # 只有 host 相似但不完全匹配就不该 skip（防范未来新域名放行策略）
        "https://not-discord.com/x",
        "https://discord.com.evil.com/phishing",
    ],
)
def test_should_not_skip_other_urls(url: str) -> None:
    assert _should_skip(url) is False


@pytest.mark.parametrize(
    "url",
    [
        # 实际事故：bot 自己的 PR 通告被自己捕获成 #19
        "https://github.com/InvolutionHell/ChatBot/pull/2",
        # 各种 dev 子路径都该 skip
        "https://github.com/InvolutionHell/ChatBot/issues/5",
        "https://github.com/InvolutionHell/ChatBot/commit/abc123",
        "https://github.com/InvolutionHell/ChatBot/compare/main...feature",
        "https://github.com/InvolutionHell/ChatBot/actions/runs/123",
        "https://github.com/InvolutionHell/ChatBot/releases/tag/v1.0",
        "https://github.com/InvolutionHell/ChatBot/discussions/10",
        "https://github.com/InvolutionHell/ChatBot/blob/main/README.md",
        "https://github.com/InvolutionHell/ChatBot/tree/main/src",
        # 大小写漂移也要拦
        "https://github.com/INVOLUTIONHELL/ChatBot/pull/2",
        "https://www.github.com/InvolutionHell/involutionhell/pull/320",
    ],
)
def test_should_skip_self_org_github_dev_chatter(url: str) -> None:
    assert _should_skip(url) is True


def test_should_skip_handles_bad_url_gracefully() -> None:
    # 坏 URL 不应抛异常；当前 urlparse 对大多数输入都不抛，兜底返回 False
    assert _should_skip("not-a-url") is False
    assert _should_skip("") is False


def test_should_skip_is_case_insensitive() -> None:
    # 大小写混杂也要 skip（URL host 实际上总是小写但保险起见）
    assert _should_skip("https://DISCORD.com/channels/x/y/z") is True
    assert _should_skip("https://Cdn.DiscordApp.com/file.png") is True
