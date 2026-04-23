"""email_sender 模块单测（纯函数 + smtp mock）。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from chat_bot.email_sender import SmtpConfig, _split_addrs, send_email


# ── _split_addrs 纯函数 ─────────────────────────────────────────────────
def test_split_addrs_empty():
    assert _split_addrs("") == []


def test_split_addrs_whitespace_only():
    assert _split_addrs("   ") == []


def test_split_addrs_single():
    assert _split_addrs("alice@example.com") == ["alice@example.com"]


def test_split_addrs_multi_with_spaces():
    # 用户手写 env 时会混乱 whitespace / 尾逗号，都要兜住
    raw = " alice@x.com , bob@y.com,  carol@z.com ,"
    assert _split_addrs(raw) == ["alice@x.com", "bob@y.com", "carol@z.com"]


# ── send_email 发送路径（mock aiosmtplib） ────────────────────────────────
@pytest.mark.asyncio
async def test_send_email_no_recipients_returns_false():
    cfg = SmtpConfig(
        host="smtp.gmail.com",
        port=587,
        user="u",
        password="p",
        from_addr="u@x",
        to_addr="",  # 空 → 跳过发送
    )
    ok = await send_email(cfg, "subj", "body")
    assert ok is False


@pytest.mark.asyncio
async def test_send_email_success(monkeypatch):
    fake_send = AsyncMock()
    monkeypatch.setattr("aiosmtplib.send", fake_send)

    cfg = SmtpConfig(
        host="smtp.gmail.com",
        port=587,
        user="u@x",
        password="p",
        from_addr="u@x",
        to_addr="a@b.com, c@d.com",
    )
    ok = await send_email(cfg, "subj", "body")
    assert ok is True
    # 验证多收件人被正确拆成 list 传给 aiosmtplib
    kwargs = fake_send.call_args.kwargs
    assert kwargs["recipients"] == ["a@b.com", "c@d.com"]
    assert kwargs["username"] == "u@x"
    assert kwargs["start_tls"] is True


@pytest.mark.asyncio
async def test_send_email_swallows_exceptions(monkeypatch):
    # aiosmtplib 抛异常时 send_email 必须返回 False 不上抛，否则会干扰 bot 主流程
    async def _fail(*args, **kwargs):
        raise ConnectionError("boom")

    monkeypatch.setattr("aiosmtplib.send", _fail)
    cfg = SmtpConfig(
        host="smtp.gmail.com",
        port=587,
        user="u",
        password="p",
        from_addr="u@x",
        to_addr="a@b.com",
    )
    ok = await send_email(cfg, "s", "b")
    assert ok is False
