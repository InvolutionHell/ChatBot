"""api_client 单元测试（用 httpx mock，不碰真后端）。"""

from __future__ import annotations

import httpx
import pytest

from chat_bot.api_client import DuplicateURL, InternalAPIError, submit_internal


def _handler_factory(status_code: int, body: dict | str):
    """构造 httpx MockTransport 的 handler。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, dict):
            return httpx.Response(status_code, json=body)
        return httpx.Response(status_code, text=body)

    return handler


@pytest.mark.asyncio
async def test_submit_internal_success(monkeypatch):
    async def fake_post(self, url, **kw):  # noqa: ARG001
        return httpx.Response(
            200,
            json={
                "success": True,
                "message": "ok",
                "data": {
                    "id": 42,
                    "host": "mp.weixin.qq.com",
                    "status": "APPROVED",
                    "ogTitle": "测试标题",
                },
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    r = await submit_internal(
        base_url="http://127.0.0.1:8080/api/community/links/internal",
        internal_key="k",
        url="https://mp.weixin.qq.com/s/abc",
        submitter_label="alice",
    )
    assert r.link_id == 42
    assert r.status == "APPROVED"
    assert r.og_title == "测试标题"


@pytest.mark.asyncio
async def test_submit_internal_duplicate(monkeypatch):
    async def fake_post(self, url, **kw):  # noqa: ARG001
        return httpx.Response(409, text="dup", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(DuplicateURL):
        await submit_internal(
            base_url="http://127.0.0.1:8080/api/community/links/internal",
            internal_key="k",
            url="https://example.com",
            submitter_label="alice",
        )


@pytest.mark.asyncio
async def test_submit_internal_forbidden(monkeypatch):
    async def fake_post(self, url, **kw):  # noqa: ARG001
        return httpx.Response(403, text="nope", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(InternalAPIError) as exc:
        await submit_internal(
            base_url="http://127.0.0.1:8080/api/community/links/internal",
            internal_key="wrong",
            url="https://example.com",
            submitter_label="alice",
        )
    assert exc.value.status == 403
