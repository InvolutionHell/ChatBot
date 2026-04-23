"""调 involution-hell 后端的 internal 提交接口。

不做 OG 抓取、不入库、不分类——这些全在后端 SharedLinkEnrichmentWorker 里。
Bot 只负责「把 URL + 提交人名 从 Discord 搬到后端」这一步。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


class DuplicateURL(Exception):
    """后端返回 409，说明该 URL 已在 shared_links 里。"""


class InternalAPIError(Exception):
    """非 409 的其它错误，带 HTTP 状态码方便上层日志。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message


@dataclass
class SubmitResult:
    link_id: int
    status: str  # 后端返回的 PENDING / APPROVED / ...
    host: str
    og_title: str | None


@dataclass
class LinkDetail:
    """GET /internal/{id} 返回的结构，字段子集。"""

    link_id: int
    status: str
    host: str
    url: str
    og_title: str | None
    og_description: str | None
    og_cover: str | None
    recommendation: str | None


@dataclass
class AdminSummary:
    pending_manual: int
    flagged: int
    approved_last_24h: int
    pending_samples: list[dict]  # {id, host, url}


async def submit_internal(
    base_url: str,
    internal_key: str,
    url: str,
    submitter_label: str,
    recommendation: str | None = None,
    timeout: float = 15.0,
) -> SubmitResult:
    """POST /api/community/links/internal。

    异常：
    - DuplicateURL：后端 409，URL 已被提交过
    - InternalAPIError：其它 4xx/5xx
    - httpx 原生的网络异常不包装，直接向上抛
    """
    payload = {
        "url": url,
        "recommendation": recommendation,
        "submitterLabel": submitter_label,
    }
    headers = {"X-Internal-Key": internal_key, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(base_url, headers=headers, json=payload)

    if resp.status_code == 409:
        raise DuplicateURL(resp.text)
    if resp.status_code >= 400:
        raise InternalAPIError(resp.status_code, resp.text)

    body = resp.json()
    data = body.get("data") or {}
    return SubmitResult(
        link_id=data.get("id", 0),
        status=data.get("status", "UNKNOWN"),
        host=data.get("host", ""),
        og_title=data.get("ogTitle"),
    )


async def fetch_link(
    base_url: str,
    internal_key: str,
    link_id: int,
    timeout: float = 10.0,
) -> LinkDetail | None:
    """GET /api/community/links/internal/{id}。用于轮询 async 富化后的最终状态。

    404 时返回 None；其它错误抛 InternalAPIError。
    """
    url = base_url.rstrip("/") + f"/{link_id}"
    headers = {"X-Internal-Key": internal_key}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise InternalAPIError(resp.status_code, resp.text)
    data = (resp.json() or {}).get("data") or {}
    return LinkDetail(
        link_id=data.get("id", 0),
        status=data.get("status", "UNKNOWN"),
        host=data.get("host", ""),
        url=data.get("url", ""),
        og_title=data.get("ogTitle"),
        og_description=data.get("ogDescription"),
        og_cover=data.get("ogCover"),
        recommendation=data.get("recommendation"),
    )


async def fetch_summary(
    base_url: str,
    internal_key: str,
    sample_limit: int = 5,
    timeout: float = 10.0,
) -> AdminSummary:
    """GET /api/community/links/internal/summary。"""
    url = base_url.rstrip("/") + "/summary"
    headers = {"X-Internal-Key": internal_key}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers, params={"sampleLimit": sample_limit})
    if resp.status_code >= 400:
        raise InternalAPIError(resp.status_code, resp.text)
    data = (resp.json() or {}).get("data") or {}
    return AdminSummary(
        pending_manual=data.get("pendingManual", 0),
        flagged=data.get("flagged", 0),
        approved_last_24h=data.get("approvedLast24h", 0),
        pending_samples=data.get("pendingSamples", []) or [],
    )
