"""B站搜索后端：直接调用公开网页搜索接口（与 yt-dlp bilisearch 同一端点）。

为什么不用 yt-dlp 的扁平结果：bilisearch 的扁平条目只含 id/url，没有标题、
作者与封面；而逐条完整解析会触发 412 风控。这里用与 yt-dlp 相同的
`/x/web-interface/search/type` 接口一次取回完整卡片元数据（非 WBI 路径，
首次请求预热 buvid3 Cookie），不涉及签名逆向。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import requests

from ...errors import (
    AuthenticationRequiredError,
    DiscoveryError,
    SiteIssueCode,
    TemporaryAccessError,
)
from ...models import Platform
from ..models import SearchHit

BILIBILI_HOME = "https://www.bilibili.com/"
BILIBILI_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_TAG_RE = re.compile(r"<[^>]+>")
_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_DURATION_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")


def _clean_title(value: Any) -> str:
    text = _TAG_RE.sub("", str(value or "")).strip()
    return text[:200] or "未命名作品"


def _cover(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("//"):
        text = "https:" + text
    return text if text.startswith(("http://", "https://")) else None


def _duration_seconds(value: Any) -> int | None:
    text = str(value or "").strip()
    match = _DURATION_RE.match(text)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    return hours * 3600 + int(match.group(2)) * 60 + int(match.group(3))


def _published(value: Any) -> str | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).date().isoformat()
    except (ValueError, OSError):
        return None


def hit_from_item(item: dict[str, Any], keyword: str) -> SearchHit | None:
    if not isinstance(item, dict):
        return None
    bvid = str(item.get("bvid") or "").strip()
    if not _BV_RE.fullmatch(bvid):
        return None
    author = str(item.get("author") or "").strip()
    return SearchHit(
        platform=Platform.BILIBILI,
        source_url=f"https://www.bilibili.com/video/{bvid}",
        media_id=bvid,
        title=_clean_title(item.get("title")),
        author=author[:80] if author else None,
        cover_url=_cover(item.get("pic")),
        duration_seconds=_duration_seconds(item.get("duration")),
        published_at=_published(item.get("pubdate")),
        keyword=keyword,
    )


class BilibiliSearchBackend:
    """B站关键词搜索（requests，无浏览器、无签名）。"""

    platform = Platform.BILIBILI

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (5.0, 15.0),
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self._warmed = False

    def _warm(self) -> None:
        if self._warmed:
            return
        self._warmed = True
        try:
            self.session.get(
                BILIBILI_HOME,
                headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
                timeout=self.timeout,
            )
        except requests.RequestException:
            # 预热失败不阻塞：搜索接口可能仍然可用，错误会在下一步暴露。
            pass

    def search(self, keyword: str, limit: int = 10) -> list[SearchHit]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        limit = max(1, min(int(limit), 30))
        self._warm()
        try:
            response = self.session.get(
                BILIBILI_SEARCH_API,
                params={"search_type": "video", "keyword": keyword, "page": 1},
                headers={
                    "User-Agent": UA,
                    "Referer": BILIBILI_HOME,
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.Timeout as exc:
            raise TemporaryAccessError(
                "B站搜索请求超时",
                issue_code=SiteIssueCode.NETWORK_ERROR,
            ) from exc
        except requests.RequestException as exc:
            raise TemporaryAccessError(
                "无法连接 B站搜索接口",
                issue_code=SiteIssueCode.NETWORK_ERROR,
            ) from exc
        if response.status_code == 412:
            raise TemporaryAccessError(
                "B站搜索触发风控（HTTP 412），请稍后再试",
                issue_code=SiteIssueCode.RATE_LIMITED,
            )
        if response.status_code in (401, 403):
            raise AuthenticationRequiredError(
                "B站搜索需要登录，请在设置中开启 Chrome 登录态后重试",
                verification_url=BILIBILI_HOME,
                issue_code=SiteIssueCode.LOGIN_REQUIRED,
            )
        if response.status_code != 200:
            raise TemporaryAccessError(
                f"B站搜索接口返回 HTTP {response.status_code}",
                issue_code=SiteIssueCode.SITE_UNAVAILABLE,
            )
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise DiscoveryError(
                "B站搜索响应超过安全大小限制",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            )
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise DiscoveryError(
                "B站搜索返回的不是合法 JSON",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            ) from exc
        code = payload.get("code") if isinstance(payload, dict) else None
        if code == -412:
            raise TemporaryAccessError(
                "B站搜索触发风控（-412），请稍后再试",
                issue_code=SiteIssueCode.RATE_LIMITED,
            )
        if code == -101:
            raise AuthenticationRequiredError(
                "B站要求登录后才能搜索，请开启 Chrome 登录态",
                verification_url=BILIBILI_HOME,
                issue_code=SiteIssueCode.LOGIN_REQUIRED,
            )
        if code != 0:
            raise DiscoveryError(
                f"B站搜索接口返回错误码 {code}",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            )
        data = payload.get("data") if isinstance(payload, dict) else None
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, list):
            raise DiscoveryError(
                "B站搜索响应缺少 result 列表",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            )
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for item in result:
            hit = hit_from_item(item, keyword)
            if hit is None or hit.media_id in seen:
                continue
            seen.add(hit.media_id or "")
            hits.append(hit)
            if len(hits) >= limit:
                break
        return hits
