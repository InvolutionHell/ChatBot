"""AlertServer aiohttp 端点测试。

拆开 cog 本体和 aiohttp app 的逻辑，这样不用启动真实 discord.Bot 就能测。
用 aiohttp 的 TestClient 直接走 HTTP 栈，覆盖：
- X-Internal-Key 校验（无/错/对）
- payload JSON 解析
- type 必须是 flagged
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from pydantic import SecretStr

from chat_bot.cogs.alerts import AlertServer


class _FakeSettings:
    """最小化的 Settings mock，只提供 AlertServer 用到的字段。"""

    def __init__(self, key: str = "test-secret") -> None:
        self.internal_api_key = SecretStr(key)
        self.discord_admin_channel_id = None  # 不推 Discord
        self.gmail_user = ""
        self.gmail_app_password = SecretStr("")
        self.digest_email_to = ""

    @property
    def email_configured(self) -> bool:  # 跳过邮件发送
        return False


@pytest_asyncio.fixture
async def client():
    """构一个只带 alert endpoint 的 aiohttp app，不拉 discord.Bot 起来。"""
    settings = _FakeSettings()
    # 不走 cog_load 的 bot 依赖，直接 new AlertServer + 手搭 app
    server = AlertServer.__new__(AlertServer)
    server.bot = MagicMock()
    server.bot.get_channel = lambda _id: None  # 无频道
    server.settings = settings
    server._runner = None
    # 关掉真实发邮件和真实 Discord（_push_* 方法里已用 email_configured / channel_id 早退）

    app = web.Application()
    app.router.add_post("/alert/flagged", server._handle_flagged)
    async with TestClient(TestServer(app)) as c:
        yield c


@pytest.mark.asyncio
async def test_alert_missing_key_returns_403(client):
    resp = await client.post(
        "/alert/flagged",
        json={"type": "flagged", "id": 1, "flags": {"ad": True}},
    )
    assert resp.status == 403
    body = await resp.json()
    assert body["ok"] is False


@pytest.mark.asyncio
async def test_alert_wrong_key_returns_403(client):
    resp = await client.post(
        "/alert/flagged",
        headers={"X-Internal-Key": "WRONG"},
        json={"type": "flagged"},
    )
    assert resp.status == 403


@pytest.mark.asyncio
async def test_alert_correct_key_returns_ok(client):
    resp = await client.post(
        "/alert/flagged",
        headers={"X-Internal-Key": "test-secret"},
        json={
            "type": "flagged",
            "id": 42,
            "url": "https://example.com",
            "host": "example.com",
            "title": "测试",
            "recommendation": "",
            "flags": {"nsfw": False, "ad": True, "flame": False},
        },
    )
    assert resp.status == 200
    body = await resp.json()
    assert body == {"ok": True}


@pytest.mark.asyncio
async def test_alert_rejects_wrong_type(client):
    resp = await client.post(
        "/alert/flagged",
        headers={"X-Internal-Key": "test-secret"},
        json={"type": "other"},
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_alert_rejects_bad_json(client):
    resp = await client.post(
        "/alert/flagged",
        headers={"X-Internal-Key": "test-secret"},
        data="not-json",
    )
    assert resp.status == 400
