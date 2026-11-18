"""搜索 API 路由（挂载到 app.main，自动受同源/Host 中间件保护）。

对外契约：
- POST /api/search           搜索
- GET  /api/search/status    配置与平台可用性（不含密钥）
- POST /api/search/key       保存/清除 API Key（只写）
- GET  /api/search/thumbnail 平台 CDN 缩略图本机代理
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..engine.common import safe_external_error_message
from ..errors import DownloaderCoreError, SiteIssueCode
from ..models import Platform
from .backends import agent_reach as agent_reach_backend
from .backends.browser import BrowserSearchBackend
from .cookies import COOKIE_LABEL_BY_VALUE, SearchCookieStore, import_from_chrome
from .key_store import SearchKeyStore
from .session import LABELS as LOGIN_LABELS
from .session import login_with_browser, read_login_status
from .models import (
    SEARCHABLE_PLATFORMS,
    UNSEARCHABLE_PLATFORMS,
    SearchRequest,
    SearchResponse,
)
from .service import SearchConfig, SearchService

PLATFORM_LABELS: dict[Platform, str] = {
    Platform.BILIBILI: "B站",
    Platform.YOUTUBE: "YouTube",
    Platform.XIAOHONGSHU: "小红书",
    Platform.DOUYIN: "抖音",
    Platform.KUAISHOU: "快手",
    Platform.SELECTED_DOUYIN: "抖音精选",
    Platform.WECHAT: "视频号",
}

# 缩略图只允许来自平台各自的 CDN 域族，杜绝 SSRF。
THUMBNAIL_HOSTS: dict[Platform, tuple[str, ...]] = {
    Platform.BILIBILI: ("hdslb.com", "biliimg.com", "bilivideo.com"),
    Platform.YOUTUBE: ("ytimg.com", "ggpht.com", "googleusercontent.com"),
    Platform.XIAOHONGSHU: ("xhscdn.com", "xiaohongshu.com"),
    Platform.DOUYIN: ("douyinpic.com", "douyinstatic.com", "byteimg.com", "bytedance.com"),
    Platform.KUAISHOU: ("kwaicdn.com", "yximgs.com", "kwimgs.com", "gifshow.com"),
}
PLATFORM_REFERERS: dict[Platform, str] = {
    Platform.BILIBILI: "https://www.bilibili.com/",
    Platform.YOUTUBE: "https://www.youtube.com/",
    Platform.XIAOHONGSHU: "https://www.xiaohongshu.com/",
    Platform.DOUYIN: "https://www.douyin.com/",
    Platform.KUAISHOU: "https://www.kuaishou.com/",
}
MAX_THUMBNAIL_BYTES = 5 * 1024 * 1024
THUMBNAIL_TIMEOUT = (5.0, 15.0)
THUMBNAIL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class KeyRequest(BaseModel):
    api_key: str = Field(default="", max_length=4096)


class CookieImportRequest(BaseModel):
    profile: str | None = Field(default=None, max_length=40)


def _status_for(exc: BaseException) -> int:
    if not isinstance(exc, DownloaderCoreError):
        return 500
    code = exc.issue_code
    if code is SiteIssueCode.LOCAL_CONFIGURATION:
        return 422
    if code is SiteIssueCode.RATE_LIMITED:
        return 429
    if code in (SiteIssueCode.SITE_UNAVAILABLE, SiteIssueCode.NETWORK_ERROR):
        return 503
    return 400


def _error_detail(exc: BaseException) -> dict[str, str]:
    issue = exc.issue_code.value if isinstance(exc, DownloaderCoreError) and exc.issue_code else "unknown"
    return {"message": safe_external_error_message(str(exc))[:800], "issue_code": issue}


def trusted_thumbnail_url(platform: Platform, url: str) -> bool:
    """仅允许 https、标准端口、无账密、平台 CDN 域族的图片地址。"""

    roots = THUMBNAIL_HOSTS.get(platform)
    if not roots:
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    if parsed.port not in (None, 443):
        return False
    host = (parsed.hostname or "").lower()
    return any(host == root or host.endswith("." + root) for root in roots)


def build_search_router(
    *,
    config_provider: Callable[[], SearchConfig],
    key_store: SearchKeyStore,
    engine_factory: Callable[[], Any],
    service: SearchService | None = None,
    cookie_store: SearchCookieStore | None = None,
    profile_dir: Path | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/search", tags=["search"])
    state: dict[str, Any] = {"service": service}
    lock = threading.Lock()

    def get_service() -> SearchService:
        with lock:
            if state["service"] is None:
                browser_factory = None
                if cookie_store is not None or profile_dir is not None:
                    browser_factory = lambda config: BrowserSearchBackend(  # noqa: E731
                        use_cookies=config.use_chrome_cookies,
                        profile=config.chrome_profile,
                        cookie_provider=(
                            cookie_store.cookies_for if cookie_store is not None else None
                        ),
                        persistent_profile_dir=profile_dir,
                    )
                state["service"] = SearchService(
                    config_provider=config_provider,
                    key_store=key_store,
                    engine_factory=engine_factory,
                    browser_backend_factory=browser_factory,
                )
            return state["service"]

    @router.post("", response_model=SearchResponse)
    def search(request: SearchRequest) -> SearchResponse:
        try:
            return get_service().search(request)
        except DownloaderCoreError as exc:
            raise HTTPException(status_code=_status_for(exc), detail=_error_detail(exc)) from exc

    @router.get("/status")
    def search_status() -> dict[str, Any]:
        config = config_provider()
        try:
            key_configured = key_store.configured()
        except OSError:
            key_configured = False
        return {
            "enabled": config.enabled,
            "key_configured": key_configured,
            "model": config.model,
            "base_url": config.base_url,
            "agent_reach": agent_reach_backend.status(),
            "login": (
                read_login_status(profile_dir) if profile_dir is not None else {}
            ),
            "cookies": (
                {
                    platform: {
                        "label": COOKIE_LABEL_BY_VALUE.get(platform, platform),
                        "count": count,
                    }
                    for platform, count in cookie_store.status().items()
                }
                if cookie_store is not None
                else {}
            ),
            "platforms": [
                {"value": platform.value, "label": PLATFORM_LABELS[platform], "searchable": True}
                for platform in SEARCHABLE_PLATFORMS
            ],
            "unsearchable": {
                platform.value: {
                    "label": PLATFORM_LABELS[platform],
                    "reason": reason,
                }
                for platform, reason in UNSEARCHABLE_PLATFORMS.items()
            },
        }

    @router.post("/key")
    def save_key(request: KeyRequest) -> dict[str, bool]:
        try:
            key_store.save(request.api_key)
        except OSError as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": "无法保存 API Key（DPAPI 不可用或文件被占用）", "issue_code": "local_configuration"},
            ) from exc
        return {"configured": key_store.configured()}

    @router.delete("/key")
    def clear_key() -> dict[str, bool]:
        key_store.clear()
        return {"configured": False}

    @router.post("/cookies/import")
    def import_cookies(request: CookieImportRequest | None = None) -> dict[str, Any]:
        if cookie_store is None:
            raise HTTPException(status_code=422, detail={"message": "登录态存储未启用"})
        profile = request.profile if request is not None else None
        try:
            payload = import_from_chrome(profile)
        except DownloaderCoreError as exc:
            raise HTTPException(status_code=_status_for(exc), detail=_error_detail(exc)) from exc
        try:
            cookie_store.save(payload)
        except OSError as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": "无法加密保存登录态（DPAPI 不可用）", "issue_code": "local_configuration"},
            ) from exc
        return {
            "imported": {platform: len(values) for platform, values in payload.items()},
        }

    @router.delete("/cookies")
    def clear_cookies() -> dict[str, bool]:
        if cookie_store is not None:
            cookie_store.clear()
        return {"cleared": True}

    @router.post("/login/{platform}")
    def login_platform(platform: str) -> dict[str, Any]:
        if profile_dir is None:
            raise HTTPException(status_code=422, detail={"message": "搜索登录态存储未启用"})
        try:
            target = Platform(platform)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": f"不支持的平台：{platform}", "issue_code": "local_configuration"},
            ) from exc
        if target not in LOGIN_LABELS:
            raise HTTPException(
                status_code=422,
                detail={"message": f"{LOGIN_LABELS.get(target, platform)} 不支持搜索登录"},
            )
        try:
            result = login_with_browser(target, profile_dir)
        except DownloaderCoreError as exc:
            raise HTTPException(status_code=_status_for(exc), detail=_error_detail(exc)) from exc
        if not result.get("ok"):
            raise HTTPException(
                status_code=408,
                detail={"message": result.get("message", "等待登录超时"), "issue_code": "verification_required"},
            )
        return result

    @router.get("/thumbnail")
    def thumbnail(
        url: str = Query(..., min_length=8, max_length=2048),
        platform: Platform = Query(...),
    ) -> Response:
        if not trusted_thumbnail_url(platform, url):
            raise HTTPException(
                status_code=422,
                detail={"message": "缩略图地址不在平台 CDN 白名单内", "issue_code": "security_blocked"},
            )
        headers = {
            "User-Agent": THUMBNAIL_UA,
            "Referer": PLATFORM_REFERERS.get(platform, ""),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        try:
            upstream = requests.get(
                url, headers=headers, stream=True, timeout=THUMBNAIL_TIMEOUT, allow_redirects=False
            )
        except requests.RequestException as exc:
            raise HTTPException(
                status_code=502,
                detail={"message": "缩略图拉取失败", "issue_code": "network_error"},
            ) from exc
        try:
            if upstream.status_code != 200:
                raise HTTPException(
                    status_code=404,
                    detail={"message": "缩略图不可用", "issue_code": "content_unavailable"},
                )
            content_type = (upstream.headers.get("content-type") or "").split(";")[0].strip().lower()
            if not content_type.startswith("image/"):
                raise HTTPException(
                    status_code=415,
                    detail={"message": "上游不是图片响应", "issue_code": "security_blocked"},
                )
            declared = upstream.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > MAX_THUMBNAIL_BYTES:
                raise HTTPException(status_code=413, detail={"message": "缩略图超过大小限制"})
            chunks: list[bytes] = []
            size = 0
            for chunk in upstream.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_THUMBNAIL_BYTES:
                    raise HTTPException(status_code=413, detail={"message": "缩略图超过大小限制"})
                chunks.append(chunk)
        finally:
            upstream.close()
        return Response(
            content=b"".join(chunks),
            media_type=content_type,
            headers={"Cache-Control": "private, max-age=3600"},
        )

    return router
