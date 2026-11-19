"""抖音 / 快手 / 小红书搜索解析与浏览器后端（fake 浏览器，不启动 Chrome）。"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app.errors import AuthenticationRequiredError, DiscoveryError, SiteIssueCode
from app.models import Platform
from app.search.backends import browser as module
from app.search.backends.browser import (
    BrowserSearchBackend,
    is_target_response,
    parse_douyin_payload,
    parse_hits,
    parse_kuaishou_payload,
    parse_xhs_payload,
    search_url,
)


# ---------------------------------------------------------------------------
# 纯解析函数
# ---------------------------------------------------------------------------


def test_search_urls():
    assert search_url(Platform.DOUYIN, "猫 猫").endswith("/search/%E7%8C%AB%20%E7%8C%AB")
    assert "searchKey=%E7%8C%AB" in search_url(Platform.KUAISHOU, "猫")
    assert "keyword=%E7%8C%AB" in search_url(Platform.XIAOHONGSHU, "猫")


def test_douyin_parser_and_hit():
    payload = {
        "data": [
            {
                "aweme_info": {
                    "aweme_id": "7300000000000000001",
                    "desc": "橘猫在键盘上睡觉",
                    "author": {"nickname": "猫猫作者"},
                    "video": {
                        "cover": {"url_list": ["https://p3.douyinpic.com/cover.jpg"]},
                        "duration": 15300,
                    },
                    "create_time": 1700000000,
                }
            },
            {"aweme_info": {"desc": "缺少 id"}},
        ]
    }
    hits = parse_hits(Platform.DOUYIN, payload, "猫", 5)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.source_url == "https://www.douyin.com/video/7300000000000000001"
    assert hit.title == "橘猫在键盘上睡觉"
    assert hit.author == "猫猫作者"
    assert hit.duration_seconds == 15
    assert hit.cover_url == "https://p3.douyinpic.com/cover.jpg"


def test_kuaishou_parser_walks_graphql_payload():
    payload = {
        "data": {
            "visionSearchPhoto": {
                "feeds": [
                    {
                        "photo": {
                            "id": "3xabcdefg",
                            "caption": "快手测试",
                            "coverUrl": "https://p2.a.yximgs.com/cover.jpg",
                            "duration": 42000,
                            "timestamp": 1700000000000,
                            "user": {"name": "快手作者"},
                        }
                    }
                ]
            }
        }
    }
    hits = parse_hits(Platform.KUAISHOU, payload, "测试", 5)
    assert len(hits) == 1
    assert hits[0].source_url == "https://www.kuaishou.com/short-video/3xabcdefg"
    assert hits[0].author == "快手作者"
    assert hits[0].duration_seconds == 42


def test_xhs_parser_keeps_xsec_token_and_rejects_bad_ids():
    payload = {
        "data": {
            "items": [
                {
                    "id": "64a1b2c3d4e5f60718293a4b",
                    "model_type": "note",
                    "xsec_token": "ABC123",
                    "note_card": {
                        "display_title": "小红书笔记",
                        "user": {"nickname": "小红薯"},
                        "cover": {"url_default": "https://sns-webpic-qc.xhscdn.com/a.jpg"},
                    },
                },
                {"id": "../../etc/passwd", "note_card": {"display_title": "bad"}},
            ]
        }
    }
    hits = parse_hits(Platform.XIAOHONGSHU, payload, "笔记", 5)
    assert len(hits) == 1
    assert "xsec_token=ABC123" in hits[0].source_url
    assert hits[0].source_url.startswith("https://www.xiaohongshu.com/explore/64a1b2c3")


def test_xhs_empty_items_is_a_valid_empty_result():
    # items=[] 是平台正常返回的“没有结果”，不是解析失败或页面改版。
    assert parse_hits(Platform.XIAOHONGSHU, {"data": {"items": []}}, "空", 5) == []


def test_target_response_filters():
    assert is_target_response(Platform.DOUYIN, "https://www.douyin.com/aweme/v1/web/general/search/single/", {})
    assert not is_target_response(Platform.DOUYIN, "https://www.douyin.com/aweme/v1/web/aweme/detail/", {})
    assert is_target_response(Platform.XIAOHONGSHU, "https://edith.xiaohongshu.com/api/sns/web/v1/search/notes", {})
    assert is_target_response(Platform.KUAISHOU, "https://www.kuaishou.com/graphql", {"operationName": "visionSearchPhoto"})
    assert not is_target_response(Platform.KUAISHOU, "https://www.kuaishou.com/graphql", {"operationName": "visionVideoDetail"})
    # 实测：网页搜索走 /rest/v/search/feed（不是 GraphQL）
    assert is_target_response(Platform.KUAISHOU, "https://www.kuaishou.com/rest/v/search/feed", {})


def test_kuaishou_feed_payload_parsed():
    payload = {"data": {"result": {"feeds": [{"photo": {"id": "3xfeed", "caption": "feed 结果"}}]}}}
    hits = parse_hits(Platform.KUAISHOU, payload, "猫", 3)
    assert [hit.media_id for hit in hits] == ["3xfeed"]


def test_kuaishou_anonymous_rejection_maps_to_login_required():
    page = FakePage(
        FakeResponse(
            "https://www.kuaishou.com/rest/v/search/feed",
            {"result": 2, "error_msg": None},
        )
    )
    backend = BrowserSearchBackend(browser_factory=lambda: FakeBrowser(page), timeout_seconds=0.05)
    with pytest.raises(AuthenticationRequiredError) as info:
        backend.search(Platform.KUAISHOU, "猫", 3)
    assert info.value.issue_code is SiteIssueCode.LOGIN_REQUIRED
    assert "登录" in str(info.value)


# ---------------------------------------------------------------------------
# 浏览器后端（fake browser）
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, url, payload, request=None):
        self.url = url
        self._payload = payload
        self.headers = {}
        self.request = request or SimpleNamespace(post_data_json={})

    def body(self):
        import json

        return json.dumps(self._payload).encode("utf-8")


class FakePage:
    def __init__(self, response=None):
        self.handlers = {}
        self.goto_url = None
        self.response = response

    def on(self, event, handler):
        self.handlers[event] = handler

    def goto(self, url, **kwargs):
        self.goto_url = url
        if self.response is not None:
            self.handlers["response"](self.response)

    def wait_for_timeout(self, ms):
        pass


class FakeContext:
    def __init__(self, page):
        self.page = page
        self.cookies = []

    def add_cookies(self, cookies):
        self.cookies.extend(cookies)

    def new_page(self):
        return self.page

    def close(self):
        pass


class FakeBrowser:
    def __init__(self, page):
        self.page = page
        self.context = None

    def new_context(self, **kwargs):
        self.context = FakeContext(self.page)
        return self.context

    def close(self):
        pass


def test_browser_backend_parses_captured_response():
    page = FakePage(
        FakeResponse(
            "https://www.douyin.com/aweme/v1/web/general/search/single/",
            {"data": [{"aweme_info": {"aweme_id": "7300000000000000002", "desc": "捕获成功"}}]},
        )
    )
    backend = BrowserSearchBackend(browser_factory=lambda: FakeBrowser(page), timeout_seconds=0.2)
    hits = backend.search(Platform.DOUYIN, "捕获", 3)
    assert [hit.media_id for hit in hits] == ["7300000000000000002"]
    assert page.goto_url.startswith("https://www.douyin.com/search/")


def test_browser_backend_uses_injected_cookie_provider():
    page = FakePage(
        FakeResponse(
            "https://www.douyin.com/aweme/v1/web/general/search/single/",
            {"data": [{"aweme_info": {"aweme_id": "7300000000000000004", "desc": "带登录态"}}]},
        )
    )
    browser = FakeBrowser(page)
    backend = BrowserSearchBackend(
        browser_factory=lambda: browser,
        cookie_provider=lambda platform: [{"name": "sessionid", "value": "x", "domain": ".douyin.com"}],
        timeout_seconds=0.05,
    )
    hits = backend.search(Platform.DOUYIN, "登录", 3)
    assert len(hits) == 1
    assert browser.context.cookies[0]["name"] == "sessionid"


def test_browser_backend_without_capture_raises_response_changed():
    page = FakePage(None)
    backend = BrowserSearchBackend(browser_factory=lambda: FakeBrowser(page), timeout_seconds=0.05)
    with pytest.raises(DiscoveryError) as info:
        backend.search(Platform.KUAISHOU, "无结果", 3)
    assert info.value.issue_code is SiteIssueCode.SITE_RESPONSE_CHANGED


def test_empty_page_classified_as_verification_or_login():
    class BlockedPage(FakePage):
        def title(self):
            return "验证码中间页"

        def evaluate(self, script):
            return "请完成安全验证"

    backend = BrowserSearchBackend(
        browser_factory=lambda: FakeBrowser(BlockedPage(None)), timeout_seconds=0.05
    )
    with pytest.raises(Exception) as info:
        backend.search(Platform.DOUYIN, "猫", 3)
    assert info.value.issue_code is SiteIssueCode.VERIFICATION_REQUIRED
    assert "登录按钮" in str(info.value)

    class RiskPage(FakePage):
        def title(self):
            return "安全限制"

        def evaluate(self, script):
            return "当前网络存在风险，请切换可靠网络后重试"

    backend = BrowserSearchBackend(
        browser_factory=lambda: FakeBrowser(RiskPage(None)), timeout_seconds=0.05
    )
    with pytest.raises(Exception) as info:
        backend.search(Platform.XIAOHONGSHU, "猫", 3)
    assert info.value.issue_code is SiteIssueCode.LOGIN_REQUIRED


def test_browser_backend_shares_one_browser_for_many_platforms():
    class DynamicPage(FakePage):
        def goto(self, url, **kwargs):
            self.goto_url = url
            if "douyin.com/search/" in url:
                payload = {
                    "data": [
                        {"aweme_info": {"aweme_id": "7300000000000000003", "desc": "多平台"}}
                    ]
                }
                response_url = "https://www.douyin.com/aweme/v1/web/general/search/single/"
            else:
                # 这里要验证的是“单平台失败不阻塞其他平台”，必须用明确不是
                # 搜索结果的响应；合法空结果（{"data": {"items": []}}）不能被
                # 当成页面改版/登录失败的夹具。
                payload = {"code": -100, "success": False}
                response_url = "https://edith.xiaohongshu.com/api/sns/web/v1/search/notes"
            self.handlers["response"](FakeResponse(response_url, payload))

    class MultiBrowser:
        def __init__(self):
            self.contexts = 0

        def new_context(self, **kwargs):
            self.contexts += 1
            return FakeContext(DynamicPage(None))

        def close(self):
            pass

    browser = MultiBrowser()
    backend = BrowserSearchBackend(browser_factory=lambda: browser, timeout_seconds=0.05)
    results = backend.search_many(
        [(Platform.DOUYIN, "多平台"), (Platform.XIAOHONGSHU, "多平台")], 3
    )
    assert len(results[Platform.DOUYIN]) == 1
    assert results[Platform.XIAOHONGSHU] == []
    issue = backend.last_issues[Platform.XIAOHONGSHU]
    assert isinstance(issue, DiscoveryError)
    assert issue.issue_code is SiteIssueCode.SITE_RESPONSE_CHANGED
    assert browser.contexts == 2  # 一个浏览器实例，逐平台复用（每个平台一个上下文）


def test_browser_backend_resets_last_issues_between_batches():
    pages = [
        FakePage(
            FakeResponse(
                "https://edith.xiaohongshu.com/api/sns/web/v1/search/notes",
                {"code": -100, "success": False},
            )
        ),
        FakePage(
            FakeResponse(
                "https://www.douyin.com/aweme/v1/web/general/search/single/",
                {"data": [{"aweme_info": {"aweme_id": "7300000000000000005", "desc": "第二批"}}]},
            )
        ),
    ]

    class SequenceBrowser:
        def new_context(self, **kwargs):
            return FakeContext(pages.pop(0))

        def close(self):
            pass

    backend = BrowserSearchBackend(browser_factory=SequenceBrowser, timeout_seconds=0.05)
    backend.search_many([(Platform.XIAOHONGSHU, "第一批")], 3)
    assert Platform.XIAOHONGSHU in backend.last_issues

    second = backend.search_many([(Platform.DOUYIN, "第二批")], 3)
    assert [hit.media_id for hit in second[Platform.DOUYIN]] == ["7300000000000000005"]
    assert backend.last_issues == {}  # 新批次不能带上一批的失败记录


def test_browser_backend_serializes_concurrent_searches():
    guard = threading.Lock()
    active = 0
    peak = 0
    entered = threading.Event()
    release = threading.Event()

    class GatedPage(FakePage):
        def goto(self, url, **kwargs):
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            entered.set()
            assert release.wait(2), "并发搜索测试未能释放页面"
            with guard:
                active -= 1
            self.goto_url = url
            if "douyin.com/search/" in url:
                response_url = "https://www.douyin.com/aweme/v1/web/general/search/single/"
                payload = {
                    "data": [{"aweme_info": {"aweme_id": "7300000000000000010", "desc": "并发抖音"}}]
                }
            else:
                response_url = "https://www.kuaishou.com/rest/v/search/feed"
                payload = {
                    "data": {"visionSearchPhoto": {"feeds": [{"photo": {"id": "3xconcurrent", "caption": "并发快手"}}]}}
                }
            self.handlers["response"](FakeResponse(response_url, payload))

    class ConcurrentBrowser:
        def new_context(self, **kwargs):
            return FakeContext(GatedPage(None))

        def close(self):
            pass

    backend = BrowserSearchBackend(browser_factory=ConcurrentBrowser, timeout_seconds=0.2)
    results = {}

    def run(platform):
        results[platform] = backend.search_many([(platform, "并发")], 3)

    first = threading.Thread(target=run, args=(Platform.DOUYIN,))
    second = threading.Thread(target=run, args=(Platform.KUAISHOU,))
    first.start()
    assert entered.wait(2), "第一次搜索没有进入页面"
    second.start()
    time.sleep(0.1)
    try:
        assert peak == 1, "search_many 没有串行化，两个浏览器会话发生了重叠"
    finally:
        release.set()
    first.join(3)
    second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert [hit.media_id for hit in results[Platform.DOUYIN][Platform.DOUYIN]] == [
        "7300000000000000010"
    ]
    assert [hit.media_id for hit in results[Platform.KUAISHOU][Platform.KUAISHOU]] == [
        "3xconcurrent"
    ]


def test_persistent_profile_reuses_one_context_for_many_platforms():
    """持久化 Profile 整批共用一个 Context，单平台结束只能关页面。"""

    class PersistentPage(FakePage):
        def __init__(self):
            super().__init__(None)
            self.closed = False

        def goto(self, url, **kwargs):
            self.goto_url = url
            if "douyin.com/search/" in url:
                response_url = "https://www.douyin.com/aweme/v1/web/general/search/single/"
                payload = {
                    "data": [
                        {"aweme_info": {"aweme_id": "7300000000000000009", "desc": "持久化抖音"}}
                    ]
                }
            else:
                response_url = "https://www.kuaishou.com/rest/v/search/feed"
                payload = {
                    "data": {
                        "feeds": [{"photo": {"id": "3xpersistent", "caption": "持久化快手"}}]
                    }
                }
            self.handlers["response"](FakeResponse(response_url, payload))

        def close(self):
            self.closed = True

    class PersistentContext:
        def __init__(self):
            self.closed = False
            self.pages = []

        def new_page(self):
            if self.closed:
                raise RuntimeError("Target page, context or browser has been closed")
            page = PersistentPage()
            self.pages.append(page)
            return page

        def add_cookies(self, cookies):
            pass

        def close(self):
            self.closed = True

    class FakePlaywright:
        def stop(self):
            pass

    context = PersistentContext()
    port = module._PersistentPort(context, FakePlaywright())
    backend = BrowserSearchBackend(browser_factory=lambda: port, timeout_seconds=0.05)
    results = backend.search_many(
        [(Platform.DOUYIN, "持久化"), (Platform.KUAISHOU, "持久化")], 3
    )
    assert [hit.media_id for hit in results[Platform.DOUYIN]] == ["7300000000000000009"]
    assert [hit.media_id for hit in results[Platform.KUAISHOU]] == ["3xpersistent"]
    assert backend.last_issues == {}
    assert len(context.pages) == 2
    assert all(page.closed for page in context.pages)  # 每个平台结束只关自己的页面
    assert context.closed  # 整批结束后才释放共享 Context


def test_is_valid_empty_result_matrix():
    assert module.is_valid_empty_result(Platform.XIAOHONGSHU, {"data": {"items": []}})
    assert module.is_valid_empty_result(Platform.KUAISHOU, {"data": {"result": {"feeds": []}}})
    assert module.is_valid_empty_result(Platform.KUAISHOU, {"result": 1, "data": {"result": None}})
    assert module.is_valid_empty_result(Platform.DOUYIN, {"status_code": 0, "data": []})
    # 风控码/结构异常都不能算合法空结果
    assert not module.is_valid_empty_result(Platform.KUAISHOU, {"result": 2})
    assert not module.is_valid_empty_result(Platform.DOUYIN, {"status_code": 8, "data": []})
    assert not module.is_valid_empty_result(Platform.XIAOHONGSHU, {"code": -100, "success": False})
    assert not module.is_valid_empty_result(Platform.XIAOHONGSHU, {"data": {}})


def test_valid_empty_kuaishou_returns_quickly_without_error():
    page = FakePage(
        FakeResponse(
            "https://www.kuaishou.com/rest/v/search/feed",
            {"data": {"result": {"feeds": []}}},
        )
    )
    backend = BrowserSearchBackend(browser_factory=lambda: FakeBrowser(page), timeout_seconds=5)
    started = time.monotonic()
    hits = backend.search(Platform.KUAISHOU, "空结果", 3)
    elapsed = time.monotonic() - started
    assert hits == []
    assert backend.last_issues == {}
    # 合法空结果应立即收尾，而不是等满 timeout 后被当成页面改版。
    assert elapsed < 1.0


def test_valid_empty_douyin_returns_empty_without_error():
    page = FakePage(
        FakeResponse(
            "https://www.douyin.com/aweme/v1/web/general/search/single/",
            {"status_code": 0, "data": []},
        )
    )
    backend = BrowserSearchBackend(browser_factory=lambda: FakeBrowser(page), timeout_seconds=5)
    assert backend.search(Platform.DOUYIN, "空结果", 3) == []
    assert backend.last_issues == {}
