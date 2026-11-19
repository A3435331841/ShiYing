"""抖音 / 快手 / 小红书搜索：打开真实搜索页，拦截页面自己发出的已签名请求。

技术路线参考 MediaCrawler 的公开做法（页面内 JS 完成签名 + 登录态复用），
但不复制其代码（Non-Commercial Learning License 1.1 与本项目 GPL-3.0 不兼容）。
这里只做“导航 + 响应观察 + JSON 解析”，不逆向签名算法。

平台响应解析函数保持纯函数，测试可直接注入 fixture，不启动浏览器。
"""

from __future__ import annotations

import contextlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlencode

from ...errors import (
    AuthenticationRequiredError,
    DiscoveryError,
    SiteIssueCode,
    TemporaryAccessError,
)
from ...models import Platform
from .. import session as search_session
from ..models import SearchHit

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
SEARCH_URLS = {
    Platform.DOUYIN: "https://www.douyin.com/search/{keyword}",
    Platform.KUAISHOU: "https://www.kuaishou.com/search/video?{query}",
    Platform.XIAOHONGSHU: "https://www.xiaohongshu.com/search_result?{query}",
}
_COOKIE_ROOTS = {
    Platform.DOUYIN: ("douyin.com",),
    Platform.KUAISHOU: ("kuaishou.com", "gifshow.com"),
    Platform.XIAOHONGSHU: ("xiaohongshu.com",),
}
_XHS_ID_RE = re.compile(r"^[0-9a-zA-Z]{8,64}$")


def search_url(platform: Platform, keyword: str) -> str:
    if platform is Platform.KUAISHOU:
        return SEARCH_URLS[platform].format(query=urlencode({"searchKey": keyword}))
    if platform is Platform.XIAOHONGSHU:
        return SEARCH_URLS[platform].format(query=urlencode({"keyword": keyword}))
    from urllib.parse import quote

    return SEARCH_URLS[platform].format(keyword=quote(keyword, safe=""))


# ---------------------------------------------------------------------------
# 纯解析函数（可离线测试）
# ---------------------------------------------------------------------------


def parse_douyin_payload(payload: Any) -> list[dict[str, Any]]:
    """从 douyin 搜索响应中提取 aweme 字典列表。"""

    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    result: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        aweme = item.get("aweme_info")
        if not isinstance(aweme, dict):
            aweme = item if item.get("aweme_id") else None
        if isinstance(aweme, dict) and aweme.get("aweme_id"):
            result.append(aweme)
    return result


def parse_kuaishou_payload(payload: Any) -> list[dict[str, Any]]:
    """在 GraphQL 响应里找出所有 photo 字典（搜索力搜索操作名可能变化）。"""

    found: list[dict[str, Any]] = []

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(node, dict):
            photo = node.get("photo")
            if isinstance(photo, dict) and photo.get("id"):
                found.append(photo)
                return
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)

    walk(payload)
    return found


def parse_xhs_payload(payload: Any) -> list[dict[str, Any]]:
    """从小红书搜索响应中提取 {id, card, xsec_token} 列表。"""

    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        card = item.get("note_card")
        if not isinstance(card, dict):
            continue
        note_id = item.get("id")
        if not isinstance(note_id, str) or not _XHS_ID_RE.match(note_id):
            continue
        token = item.get("xsec_token") or card.get("xsec_token")
        result.append(
            {"id": note_id, "card": card, "xsec_token": token if isinstance(token, str) else None}
        )
    return result


def _first_cover(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, str) and entry.startswith(("http://", "https://")):
                return entry
            if isinstance(entry, dict):
                url = entry.get("url")
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    return url
    return None


def _douyin_hit(aweme: dict[str, Any], keyword: str) -> SearchHit | None:
    aweme_id = str(aweme.get("aweme_id") or "").strip()
    if not aweme_id.isdigit():
        return None
    author = aweme.get("author")
    author_name = author.get("nickname") if isinstance(author, dict) else None
    video = aweme.get("video") if isinstance(aweme.get("video"), dict) else {}
    cover = None
    for key in ("cover", "origin_cover", "dynamic_cover"):
        group = video.get(key)
        if isinstance(group, dict):
            cover = _first_cover(group.get("url_list"))
            if cover:
                break
    if not cover:
        images = aweme.get("images")
        if isinstance(images, list) and images and isinstance(images[0], dict):
            cover = _first_cover(images[0].get("url_list"))
    duration = None
    duration_ms = video.get("duration") or aweme.get("duration")
    if isinstance(duration_ms, (int, float)) and duration_ms > 0:
        duration = int(duration_ms / 1000)
    published = None
    created = aweme.get("create_time")
    if isinstance(created, (int, float)) and created > 0:
        with contextlib.suppress(ValueError, OSError):
            published = datetime.fromtimestamp(created, timezone.utc).date().isoformat()
    return SearchHit(
        platform=Platform.DOUYIN,
        source_url=f"https://www.douyin.com/video/{aweme_id}",
        media_id=aweme_id,
        title=(str(aweme.get("desc") or "").strip() or "未命名作品")[:200],
        author=str(author_name).strip()[:80] if author_name else None,
        cover_url=cover,
        duration_seconds=duration,
        published_at=published,
        keyword=keyword,
    )


def _kuaishou_hit(photo: dict[str, Any], keyword: str) -> SearchHit | None:
    photo_id = str(photo.get("id") or "").strip()
    if not photo_id:
        return None
    user = photo.get("user") if isinstance(photo.get("user"), dict) else {}
    author_name = user.get("name") or user.get("userName")
    cover = _first_cover(photo.get("coverUrl")) or _first_cover(photo.get("coverUrls"))
    duration = None
    duration_ms = photo.get("duration")
    if isinstance(duration_ms, (int, float)) and duration_ms > 0:
        duration = int(duration_ms / 1000)
    published = None
    timestamp = photo.get("timestamp")
    if isinstance(timestamp, (int, float)) and timestamp > 0:
        seconds = timestamp / 1000 if timestamp > 10**12 else timestamp
        with contextlib.suppress(ValueError, OSError):
            published = datetime.fromtimestamp(seconds, timezone.utc).date().isoformat()
    return SearchHit(
        platform=Platform.KUAISHOU,
        source_url=f"https://www.kuaishou.com/short-video/{photo_id}",
        media_id=photo_id,
        title=(str(photo.get("caption") or "").strip() or "未命名作品")[:200],
        author=str(author_name).strip()[:80] if author_name else None,
        cover_url=cover,
        duration_seconds=duration,
        published_at=published,
        keyword=keyword,
    )


def _xhs_hit(raw: dict[str, Any], keyword: str) -> SearchHit | None:
    note_id = raw.get("id")
    card = raw.get("card") if isinstance(raw.get("card"), dict) else {}
    if not isinstance(note_id, str) or not _XHS_ID_RE.match(note_id):
        return None
    url = f"https://www.xiaohongshu.com/explore/{note_id}"
    token = raw.get("xsec_token")
    if isinstance(token, str) and token:
        url += "?" + urlencode({"xsec_token": token, "xsec_source": "pc_search"})
    user = card.get("user") if isinstance(card.get("user"), dict) else {}
    author_name = user.get("nickname") or user.get("nick_name")
    cover = card.get("cover") if isinstance(card.get("cover"), dict) else {}
    cover_url = _first_cover(cover.get("url_default")) or _first_cover(cover.get("info_list"))
    title = card.get("display_title") or card.get("desc") or ""
    return SearchHit(
        platform=Platform.XIAOHONGSHU,
        source_url=url,
        media_id=note_id,
        title=(str(title).strip() or "未命名笔记")[:200],
        author=str(author_name).strip()[:80] if author_name else None,
        cover_url=cover_url,
        duration_seconds=None,
        published_at=None,
        keyword=keyword,
    )


_PARSERS: dict[Platform, tuple[Callable[[Any], list[dict[str, Any]]], Callable[..., SearchHit | None]]] = {
    Platform.DOUYIN: (parse_douyin_payload, _douyin_hit),
    Platform.KUAISHOU: (parse_kuaishou_payload, _kuaishou_hit),
    Platform.XIAOHONGSHU: (parse_xhs_payload, _xhs_hit),
}


def parse_hits(platform: Platform, payload: Any, keyword: str, limit: int) -> list[SearchHit]:
    """把平台原始响应转换为去重后的 SearchHit 列表。"""

    parser, converter = _PARSERS[platform]
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for raw in parser(payload):
        hit = converter(raw, keyword)
        if hit is None:
            continue
        key = hit.media_id or hit.source_url
        if key in seen:
            continue
        seen.add(key)
        hits.append(hit)
        if len(hits) >= limit:
            break
    return hits


# ---------------------------------------------------------------------------
# 响应过滤与浏览器执行
# ---------------------------------------------------------------------------


def is_target_response(platform: Platform, url: str, request_body: Any) -> bool:
    """判断响应是否为该平台的搜索接口（避免把详情/推荐响应当结果）。"""

    from urllib.parse import urlsplit

    path = urlsplit(url).path
    if platform is Platform.DOUYIN:
        return "/aweme/v1/web/" in path and "search" in path
    if platform is Platform.XIAOHONGSHU:
        return path == "/api/sns/web/v1/search/notes"
    if platform is Platform.KUAISHOU:
        # 实测（2026-11）：网页搜索走 /rest/v/search/feed；旧版/详情走 GraphQL。
        if path == "/rest/v/search/feed":
            return True
        if path != "/graphql":
            return False
        operation = ""
        if isinstance(request_body, dict):
            operation = str(request_body.get("operationName") or "")
        return "search" in operation.lower()
    return False


def _chrome_cookies_for(platform: Platform, profile: str | None) -> list[dict[str, Any]]:
    """用 yt-dlp 的 Chrome Cookie 提取器读取登录态（只取平台相关域）。"""

    try:
        from yt_dlp.cookies import extract_cookies_from_browser

        class _QuietLogger:
            def debug(self, message: str) -> None: ...
            def info(self, message: str) -> None: ...
            def warning(self, message: str) -> None: ...
            def error(self, message: str) -> None: ...

        jar = extract_cookies_from_browser("chrome", profile=profile, logger=_QuietLogger())
    except Exception:  # noqa: BLE001 - Cookie 读取失败时按匿名继续，由解析失败给出提示
        return []
    roots = _COOKIE_ROOTS[platform]
    now = time.time()
    result: list[dict[str, Any]] = []
    for cookie in jar:
        domain = (cookie.domain or "").lower().lstrip(".")
        if not any(domain == root or domain.endswith("." + root) for root in roots):
            continue
        if cookie.expires and cookie.expires <= now:
            continue
        result.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path or "/",
                "secure": bool(cookie.secure),
                "httpOnly": False,
                "expires": float(cookie.expires or -1),
            }
        )
    return result


def _classify_empty_page(page: Any, *, search_url: str, platform: Platform) -> BaseException:
    """没有捕获到搜索响应时，按页面实际内容给出可行动的失败原因。

    实测（2026-11）：抖音匿名搜索跳到「验证码中间页」；小红书跳「安全限制/
    当前网络存在风险」；快手直接返回 ``{"result":2}``。三者都要求登录态，
    笼统的“页面结构变化”会误导用户，因此读取落地页标题/正文分类。
    """

    title = ""
    body = ""
    with contextlib.suppress(Exception):
        title = str(page.title() or "")
    with contextlib.suppress(Exception):
        body = str(
            page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 600)")
            or ""
        )
    text = f"{title}\n{body}"
    lowered = text.lower()
    hint = "请在设置 → AI 搜索 → 搜索登录态中点对应平台的登录按钮，扫码登录后再搜索"
    if any(marker in text for marker in ("验证码", "安全验证", "滑块", "captcha", "验证中间页")) or "captcha" in lowered:
        return AuthenticationRequiredError(
            f"平台要求完成验证码/安全验证。{hint}",
            verification_url=search_url,
            issue_code=SiteIssueCode.VERIFICATION_REQUIRED,
        )
    if any(marker in text for marker in ("安全限制", "网络存在风险", "风控", "请先登录", "登录后")):
        return AuthenticationRequiredError(
            f"平台以安全限制/风控拦截了匿名搜索。{hint}",
            verification_url=search_url,
            issue_code=SiteIssueCode.LOGIN_REQUIRED,
        )
    if '"result":2' in text or '"result": 2' in text:
        return AuthenticationRequiredError(
            f"平台拒绝了匿名搜索（风控码 result=2）。{hint}",
            verification_url=search_url,
            issue_code=SiteIssueCode.LOGIN_REQUIRED,
        )
    if platform is Platform.DOUYIN and "登录" in text:
        return AuthenticationRequiredError(
            f"抖音要求登录后才能搜索。{hint}",
            verification_url=search_url,
            issue_code=SiteIssueCode.LOGIN_REQUIRED,
        )
    return DiscoveryError(
        "未捕获到搜索结果：可能需要登录、完成验证，或页面结构已变化",
        issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
    )


class BrowserContextPort(Protocol):
    def new_page(self) -> Any: ...
    def add_cookies(self, cookies: list[dict[str, Any]]) -> None: ...
    def close(self) -> None: ...


class BrowserPort(Protocol):
    def new_context(self, **kwargs: Any) -> BrowserContextPort: ...
    def close(self) -> None: ...


def is_valid_empty_result(platform: Platform, payload: Any) -> bool:
    """判断“搜索成功但没有结果”的合法空响应（区别于风控/结构变化）。

    - 小红书：``data.items == []``
    - 快手：``data.result`` 为空或其 ``feeds`` 为空
    - 抖音：``status_code`` 为 0 且 ``data == []``
    """

    if not isinstance(payload, dict):
        return False
    if platform is Platform.XIAOHONGSHU:
        data = payload.get("data")
        return (
            isinstance(data, dict)
            and isinstance(data.get("items"), list)
            and len(data["items"]) == 0
        )
    if platform is Platform.KUAISHOU:
        code = payload.get("result")
        if isinstance(code, int) and code not in (0, 1):
            return False
        data = payload.get("data")
        if not isinstance(data, dict):
            return False
        result = data.get("result")
        if result is None:
            return True
        if isinstance(result, dict):
            feeds = result.get("feeds")
            return isinstance(feeds, list) and len(feeds) == 0
        return False
    if platform is Platform.DOUYIN:
        code = payload.get("status_code")
        if isinstance(code, int) and code not in (0, 1):
            return False
        data = payload.get("data")
        return isinstance(data, list) and len(data) == 0
    return False


class _PersistentContextPort:
    """持久化 Profile 的单次搜索句柄：close 只关页面，不关共享 Context。

    ``launch_persistent_context`` 一个 Profile 只有一个 Context，整批平台共用；
    若每个平台结束时关闭它，后续平台会拿到 TargetClosedError。
    """

    def __init__(self, context: Any) -> None:
        self._context = context
        self._pages: list[Any] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)

    def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        self._context.add_cookies(cookies)

    def new_page(self) -> Any:
        page = self._context.new_page()
        self._pages.append(page)
        return page

    def close(self) -> None:
        for page in self._pages:
            with contextlib.suppress(Exception):
                page.close()
        self._pages.clear()


class _PersistentPort:
    """把 launch_persistent_context 包装成 BrowserPort（同一 Profile 一个上下文）。"""

    def __init__(self, context: Any, playwright: Any) -> None:
        self.context = context
        self.playwright = playwright

    def new_context(self, **kwargs: Any) -> Any:
        return _PersistentContextPort(self.context)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.context.close()
        with contextlib.suppress(Exception):
            self.playwright.stop()


class BrowserSearchBackend:
    """抖音 / 快手 / 小红书搜索（共享一个浏览器实例，逐平台串行）。"""

    platforms = (Platform.DOUYIN, Platform.KUAISHOU, Platform.XIAOHONGSHU)

    def __init__(
        self,
        *,
        use_cookies: bool = False,
        profile: str | None = None,
        timeout_seconds: float = 25.0,
        browser_factory: Callable[[], BrowserPort] | None = None,
        cookie_provider: Callable[[Platform], list[dict[str, Any]]] | None = None,
        headless: bool = False,
        persistent_profile_dir: "Path | None" = None,
    ) -> None:
        self.use_cookies = use_cookies
        self.profile = profile
        self.timeout_seconds = timeout_seconds
        self._browser_factory = browser_factory
        self._cookie_provider = cookie_provider
        # 实测（2026-11）：抖音/快手会识别 headless 并跳验证码/result=2；
        # 有头 Chrome（窗口移到屏幕外）能正常加载搜索页。小红书两种模式都要求登录，
        # 由 Agent-Reach 公开搜索或用户登录态兜底。
        self.headless = headless
        # 专用持久化 Profile：扫码登录一次后，搜索直接复用登录态。
        self.persistent_profile_dir = persistent_profile_dir
        self._search_lock = threading.Lock()
        self._last_issues: dict[Platform, BaseException] = {}

    # -- 测试注入点：返回可用的浏览器实例（默认启动系统 Chrome） -------
    def _launch_browser(self) -> BrowserPort:
        if self._browser_factory is not None:
            return self._browser_factory()
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        args = (
            []
            if self.headless
            else ["--window-position=-32000,-32000", "--window-size=1280,900"]
        )
        try:
            if self.persistent_profile_dir is not None:
                profile_dir = Path(self.persistent_profile_dir)
                profile_dir.mkdir(parents=True, exist_ok=True)
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    channel="chrome",
                    headless=self.headless,
                    args=args,
                    viewport={"width": 1280, "height": 900},
                    locale="zh-CN",
                )
                port = _PersistentPort(context, playwright)
                setattr(port, "_search_playwright", playwright)
                return port
            browser = playwright.chromium.launch(
                channel="chrome",
                headless=self.headless,
                args=args,
            )
        except Exception:
            playwright.stop()
            raise TemporaryAccessError(
                "无法启动 Chrome 进行搜索，请确认已安装 Google Chrome",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )
        # 绑定 playwright 生命周期，close 时一起释放。
        setattr(browser, "_search_playwright", playwright)
        return browser

    def search(self, platform: Platform, keyword: str, limit: int = 10) -> list[SearchHit]:
        if platform not in self.platforms:
            raise DiscoveryError(
                f"{platform.value} 不走浏览器搜索",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )
        hits = self.search_many([(platform, keyword)], limit).get(platform, [])
        if not hits and platform in self.last_issues:
            raise self.last_issues[platform]
        return hits

    @property
    def last_issues(self) -> dict[Platform, BaseException]:
        """最近一次 search_many 的单平台失败（失败平台不阻塞其他平台）。"""

        return dict(self._last_issues)

    def search_many(
        self, tasks: list[tuple[Platform, str]], limit: int = 10
    ) -> dict[Platform, list[SearchHit]]:
        # 持久化 Profile 不能被两个 Playwright 实例同时打开：并发搜索串行化。
        with self._search_lock:
            return self._search_many_locked(tasks, limit)

    def _search_many_locked(
        self, tasks: list[tuple[Platform, str]], limit: int = 10
    ) -> dict[Platform, list[SearchHit]]:
        tasks = [(platform, keyword) for platform, keyword in tasks if keyword.strip()]
        self._last_issues = {}
        if not tasks:
            return {}
        with self._profile_guard():
            browser = self._launch_browser()
            try:
                results: dict[Platform, list[SearchHit]] = {}
                for platform, keyword in tasks:
                    try:
                        results[platform] = self._search_one(browser, platform, keyword, limit)
                    except BaseException as exc:  # noqa: BLE001 - 单平台失败记录后继续
                        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                            raise
                        self._last_issues[platform] = exc
                        results[platform] = []
                return results
            finally:
                with contextlib.suppress(Exception):
                    browser.close()
                playwright = getattr(browser, "_search_playwright", None)
                if playwright is not None:
                    with contextlib.suppress(Exception):
                        playwright.stop()

    @contextlib.contextmanager
    def _profile_guard(self):
        """持久化 Profile 与登录窗口互斥（同一目录共用一把进程内锁）。"""

        if self.persistent_profile_dir is None:
            yield
            return
        lock = search_session.profile_lock(Path(self.persistent_profile_dir))
        if not lock.acquire(timeout=search_session.PROFILE_LOCK_TIMEOUT_SECONDS):
            raise TemporaryAccessError(
                "登录窗口正在使用搜索 Profile，请关闭登录窗口后再搜索",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )
        try:
            yield
        finally:
            lock.release()

    def _search_one(
        self, browser: BrowserPort, platform: Platform, keyword: str, limit: int
    ) -> list[SearchHit]:
        from playwright.sync_api import Error as PlaywrightError

        context = browser.new_context(
            viewport={"width": 1280, "height": 800}, locale="zh-CN"
        )
        try:
            cookies: list[dict[str, Any]] = []
            if self._cookie_provider is not None:
                with contextlib.suppress(Exception):
                    cookies = list(self._cookie_provider(platform) or [])
            if not cookies and self.use_cookies:
                cookies = _chrome_cookies_for(platform, self.profile)
            if cookies:
                with contextlib.suppress(Exception):
                    context.add_cookies(cookies)
            page = context.new_page()
            captured: list[SearchHit] = []
            api_error: list[dict[str, Any]] = []
            empty_result: list[bool] = []
            done = threading.Event()
            lock = threading.Lock()

            def observe(response: Any) -> None:
                try:
                    request = response.request
                    body = request.post_data_json or {}
                    if not is_target_response(platform, response.url, body):
                        return
                    size = response.headers.get("content-length", "0")
                    if size.isdigit() and int(size) > MAX_RESPONSE_BYTES:
                        return
                    raw = response.body()
                    if len(raw) > MAX_RESPONSE_BYTES:
                        return
                    payload = json.loads(raw)
                except Exception:  # noqa: BLE001 - 非目标响应/关闭中的响应直接忽略
                    return
                hits = parse_hits(platform, payload, keyword, limit)
                if hits:
                    with lock:
                        captured.extend(hits)
                        done.set()
                    return
                # 合法空结果（搜索成功但 0 条）不应被当成登录/结构错误，也不必等满超时。
                if is_valid_empty_result(platform, payload):
                    with lock:
                        empty_result.append(True)
                        done.set()
                    return
                # 平台用 JSON 错误码拒绝匿名搜索（如快手 result=2）：记录后尽早收尾。
                code = None
                if isinstance(payload, dict):
                    if platform is Platform.KUAISHOU:
                        code = payload.get("result")
                    elif platform is Platform.DOUYIN:
                        code = payload.get("status_code")
                if isinstance(code, int) and code not in (0, 1):
                    with lock:
                        api_error.append({"code": code, "platform": platform.value})
                        done.set()

            page.on("response", observe)
            try:
                page.goto(
                    search_url(platform, keyword),
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
            except PlaywrightError as exc:
                raise TemporaryAccessError(
                    f"打开搜索页失败：{type(exc).__name__}",
                    issue_code=SiteIssueCode.SITE_UNAVAILABLE,
                ) from exc
            deadline = time.monotonic() + self.timeout_seconds
            while not done.is_set() and time.monotonic() < deadline:
                page.wait_for_timeout(200)
            with lock:
                hits = list(captured)
                error_code = api_error[0]["code"] if api_error else None
                empty = bool(empty_result)
            if not hits and error_code is not None:
                raise AuthenticationRequiredError(
                    f"平台拒绝了搜索请求（风控码 {error_code}）。请在设置中点击对应平台的登录按钮完成扫码登录后再搜索",
                    verification_url=search_url(platform, keyword),
                    issue_code=SiteIssueCode.LOGIN_REQUIRED,
                )
            if not hits and not empty:
                # 必须在关闭上下文之前读取落地页（验证码/安全限制/result=2）。
                raise _classify_empty_page(
                    page,
                    search_url=search_url(platform, keyword),
                    platform=platform,
                )
            if not hits:
                # 合法空搜索：返回空列表，由调用方按“无结果”展示，而不是报错。
                return []
        finally:
            with contextlib.suppress(Exception):
                context.close()
        deduped: list[SearchHit] = []
        seen: set[str] = set()
        for hit in hits:
            key = hit.media_id or hit.source_url
            if key in seen:
                continue
            seen.add(key)
            deduped.append(hit)
            if len(deduped) >= limit:
                break
        return deduped
