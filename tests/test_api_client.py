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


# ── fetch_link ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_link_success(monkeypatch):
    async def fake_get(self, url, **kw):  # noqa: ARG001
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": 7,
                    "status": "APPROVED",
                    "host": "arxiv.org",
                    "url": "https://arxiv.org/abs/1",
                    "ogTitle": "t",
                    "ogDescription": None,
                    "ogCover": None,
                    "recommendation": "来自 Discord @x",
                }
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    from chat_bot.api_client import fetch_link

    r = await fetch_link(
        base_url="http://127.0.0.1:8080/api/community/links/internal",
        internal_key="k",
        link_id=7,
    )
    assert r is not None
    assert r.status == "APPROVED"
    assert r.host == "arxiv.org"


@pytest.mark.asyncio
async def test_fetch_link_404_returns_none(monkeypatch):
    async def fake_get(self, url, **kw):  # noqa: ARG001
        return httpx.Response(404, text="nope", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    from chat_bot.api_client import fetch_link

    r = await fetch_link(
        base_url="http://127.0.0.1:8080/api/community/links/internal",
        internal_key="k",
        link_id=999,
    )
    assert r is None


@pytest.mark.asyncio
async def test_fetch_link_5xx_raises(monkeypatch):
    async def fake_get(self, url, **kw):  # noqa: ARG001
        return httpx.Response(500, text="boom", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    from chat_bot.api_client import fetch_link

    with pytest.raises(InternalAPIError) as exc:
        await fetch_link(
            base_url="http://127.0.0.1:8080/api/community/links/internal",
            internal_key="k",
            link_id=1,
        )
    assert exc.value.status == 500


# ── fetch_summary ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_summary_success(monkeypatch):
    async def fake_get(self, url, **kw):  # noqa: ARG001
        return httpx.Response(
            200,
            json={
                "data": {
                    "pendingManual": 3,
                    "flagged": 1,
                    "approvedLast24h": 12,
                    "pendingSamples": [{"id": 1, "host": "h", "url": "u"}],
                }
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    from chat_bot.api_client import fetch_summary

    r = await fetch_summary(
        base_url="http://127.0.0.1:8080/api/community/links/internal",
        internal_key="k",
    )
    assert r.pending_manual == 3
    assert r.flagged == 1
    assert r.approved_last_24h == 12
    assert len(r.pending_samples) == 1


@pytest.mark.asyncio
async def test_fetch_summary_empty_body_defaults(monkeypatch):
    # 后端若返回 {"data": null}，client 应该降级成空 summary，不崩
    async def fake_get(self, url, **kw):  # noqa: ARG001
        return httpx.Response(
            200,
            json={"data": None},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    from chat_bot.api_client import fetch_summary

    r = await fetch_summary(
        base_url="http://127.0.0.1:8080/api/community/links/internal",
        internal_key="k",
    )
    assert r.pending_manual == 0
    assert r.flagged == 0
    assert r.pending_samples == []
