"""URL 正则单测。

listener 用它从 Discord 消息正文里提链（可能一条消息里有多个链接+闲聊）。
commands 用它校验 /share 参数（整串必须是单个 URL）。
两套正则不是同一份：listener 贪婪匹配，commands 要锚定 ^...$。
"""

from __future__ import annotations

from chat_bot.cogs.commands import _URL_RE as SLASH_RE
from chat_bot.cogs.listener import _URL_RE as LISTENER_RE


# ── listener 提链：可能嵌在文字里 ────────────────────────────────────────
def test_listener_extracts_url_in_sentence():
    text = "大家看看这篇 https://arxiv.org/abs/2501.00001 不错"
    assert LISTENER_RE.findall(text) == ["https://arxiv.org/abs/2501.00001"]


def test_listener_extracts_multiple_urls():
    text = "对比一下 https://a.com/x 和 https://b.com/y 哪个好"
    assert LISTENER_RE.findall(text) == ["https://a.com/x", "https://b.com/y"]


def test_listener_skips_trailing_punct():
    # 消息末尾常跟中文标点，URL 不应该把标点吃进去
    text = "快看：https://example.com/page"
    urls = LISTENER_RE.findall(text)
    assert urls == ["https://example.com/page"]


def test_listener_skips_non_http():
    text = "别发 magnet:?xt=urn:btih:... 进来 ftp://... 也不行"
    assert LISTENER_RE.findall(text) == []


# ── slash command 校验：整串就是一个 URL ─────────────────────────────────
def test_slash_accepts_pure_url():
    assert SLASH_RE.match("https://arxiv.org/abs/2501.00001")


def test_slash_rejects_trailing_text():
    # 用户填 /share url:"https://x.com 推荐" 时应被拒
    assert SLASH_RE.match("https://x.com 推荐语") is None


def test_slash_rejects_non_http():
    assert SLASH_RE.match("file:///etc/passwd") is None
    assert SLASH_RE.match("javascript:alert(1)") is None


def test_slash_accepts_http_lowercase():
    # 允许 http（Cloudflare Flexible 下的源站）
    assert SLASH_RE.match("http://example.com/")
