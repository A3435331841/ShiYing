"""搜索编排：部分失败隔离、去重、AI 降级、Agent-Reach 优先。"""

from __future__ import annotations

import json

import pytest

from app.errors import DiscoveryError, SiteIssueCode, TemporaryAccessError
from app.models import Platform
from app.search.models import SearchHit, SearchRequest
from app.search.service import SearchConfig, SearchService


class FakeKeyStore:
    def __init__(self, key: str = ""):
        self.key = key

    def load(self) -> str:
        return self.key

    def configured(self) -> bool:
        return bool(self.key)


class FakeBilibili:
    def __init__(self, hits=None, error=None):
        self.hits = hits or []
        self.error = error
        self.keywords = []

    def search(self, keyword, limit=10):
        self.keywords.append(keyword)
        if self.error:
            raise self.error
        return self.hits[:limit]


class FakeYtdlp:
    def __init__(self, hits=None, error=None):
        self.hits = hits or []
        self.error = error
        self.keywords = []

    def search(self, platform, keyword, limit=10):
        self.keywords.append((platform, keyword))
        if self.error:
            raise self.error
        return self.hits[:limit]


class FakeBrowser:
    def __init__(self, results=None, issues=None):
        self.results = results or {}
        self.last_issues = issues or {}
        self.tasks = []

    def search_many(self, tasks, limit=10):
        self.tasks.append(list(tasks))
        return {
            platform: self.results.get(platform, [])[:limit] for platform, _ in tasks
        }


class FakeAgent:
    def __init__(self, hits=None, available=False):
        self.hits = hits or {}
        self._available = available
        self.calls = []

    def available(self):
        return self._available

    def search(self, platform, keyword, limit=10):
        self.calls.append((platform, keyword))
        return self.hits.get(platform, [])[:limit]


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _hit(platform, media_id, title):
    url = {
        Platform.BILIBILI: f"https://www.bilibili.com/video/{media_id}",
        Platform.YOUTUBE: f"https://www.youtube.com/watch?v={media_id}",
        Platform.DOUYIN: f"https://www.douyin.com/video/{media_id}",
        Platform.KUAISHOU: f"https://www.kuaishou.com/short-video/{media_id}",
        Platform.XIAOHONGSHU: f"https://www.xiaohongshu.com/explore/{media_id}",
    }[platform]
    return SearchHit(platform=platform, source_url=url, media_id=media_id, title=title)


def _service(**kwargs):
    defaults = dict(
        config_provider=lambda: SearchConfig(),
        key_store=FakeKeyStore(),
        engine_factory=lambda: None,
        bilibili_backend_factory=lambda: FakeBilibili(),
        ytdlp_backend_factory=lambda: FakeYtdlp(),
        browser_backend_factory=lambda config: FakeBrowser(),
        agent_reach_backend=FakeAgent(),
    )
    defaults.update(kwargs)
    return SearchService(**defaults)


def test_partial_failure_is_isolated_per_platform():
    bilibili = FakeBilibili(error=TemporaryAccessError("limited", issue_code=SiteIssueCode.RATE_LIMITED))
    youtube = FakeYtdlp(hits=[_hit(Platform.YOUTUBE, "dQw4w9WgXcQ", "Y")])
    service = _service(
        bilibili_backend_factory=lambda: bilibili,
        ytdlp_backend_factory=lambda: youtube,
    )
    response = service.search(
        SearchRequest(query="猫", platforms=[Platform.BILIBILI, Platform.YOUTUBE], ai_mode=False)
    )
    by_platform = {result.platform: result for result in response.results}
    assert by_platform[Platform.BILIBILI].issue is not None
    assert by_platform[Platform.BILIBILI].issue.issue_code == SiteIssueCode.RATE_LIMITED.value
    assert len(by_platform[Platform.YOUTUBE].hits) == 1
    assert [hit.platform for hit in response.hits] == [Platform.YOUTUBE]


def test_results_are_deduplicated_by_media_id():
    bilibili = FakeBilibili(
        hits=[
            _hit(Platform.BILIBILI, "BV1GJ411x7h7", "A"),
            _hit(Platform.BILIBILI, "BV1GJ411x7h7", "A duplicate"),
            _hit(Platform.BILIBILI, "BV1xx411c7mD", "B"),
        ]
    )
    service = _service(bilibili_backend_factory=lambda: bilibili)
    response = service.search(
        SearchRequest(query="猫", platforms=[Platform.BILIBILI], ai_mode=False, limit=5)
    )
    assert [hit.title for hit in response.hits] == ["A", "B"]


def test_raw_plan_used_without_ai_key():
    bilibili = FakeBilibili(hits=[_hit(Platform.BILIBILI, "BV1GJ411x7h7", "A")])
    service = _service(bilibili_backend_factory=lambda: bilibili)
    response = service.search(
        SearchRequest(query="原始描述", platforms=[Platform.BILIBILI], ai_mode=True)
    )
    assert response.plan.source == "raw"
    assert response.plan.fallback_reason
    assert bilibili.keywords == ["原始描述"]


def test_ai_plan_keywords_are_used_per_platform():
    plan_json = json.dumps(
        {"keywords": {"bilibili": ["橘猫 键盘"], "youtube": ["orange cat keyboard"]}},
        ensure_ascii=False,
    )
    llm = FakeLLM([plan_json])
    bilibili = FakeBilibili(hits=[_hit(Platform.BILIBILI, "BV1GJ411x7h7", "A")])
    youtube = FakeYtdlp(hits=[_hit(Platform.YOUTUBE, "dQw4w9WgXcQ", "Y")])
    service = _service(
        key_store=FakeKeyStore("sk-test"),
        llm_client_factory=lambda config, key: llm,
        bilibili_backend_factory=lambda: bilibili,
        ytdlp_backend_factory=lambda: youtube,
    )
    response = service.search(
        SearchRequest(query="橘猫睡觉", platforms=[Platform.BILIBILI, Platform.YOUTUBE])
    )
    assert response.plan.source == "ai"
    assert bilibili.keywords == ["橘猫 键盘"]
    assert youtube.keywords == [(Platform.YOUTUBE, "orange cat keyboard")]


def test_image_only_without_key_is_rejected():
    service = _service()
    with pytest.raises(DiscoveryError) as info:
        service.search(SearchRequest(image="https://example.com/a.jpg", platforms=[Platform.BILIBILI]))
    assert info.value.issue_code is SiteIssueCode.LOCAL_CONFIGURATION


def test_agent_reach_preferred_when_available():
    agent = FakeAgent(hits={Platform.BILIBILI: [_hit(Platform.BILIBILI, "BV1GJ411x7h7", "agent")]}, available=True)
    bilibili = FakeBilibili(hits=[_hit(Platform.BILIBILI, "BV1xx411c7mD", "internal")])
    service = _service(agent_reach_backend=agent, bilibili_backend_factory=lambda: bilibili)
    response = service.search(
        SearchRequest(query="猫", platforms=[Platform.BILIBILI], ai_mode=False)
    )
    assert [hit.title for hit in response.hits] == ["agent"]
    assert bilibili.keywords == []


def test_browser_platform_failures_reported_individually():
    douyin = FakeBrowser(
        results={Platform.DOUYIN: [_hit(Platform.DOUYIN, "7300000000000000001", "D")]},
        issues={Platform.KUAISHOU: TemporaryAccessError("limited", issue_code=SiteIssueCode.RATE_LIMITED)},
    )
    service = _service(browser_backend_factory=lambda config: douyin)
    response = service.search(
        SearchRequest(
            query="猫",
            platforms=[Platform.DOUYIN, Platform.KUAISHOU],
            ai_mode=False,
        )
    )
    by_platform = {result.platform: result for result in response.results}
    assert len(by_platform[Platform.DOUYIN].hits) == 1
    assert by_platform[Platform.KUAISHOU].issue.issue_code == SiteIssueCode.RATE_LIMITED.value
    assert douyin.tasks and len(douyin.tasks[0]) == 2


def test_disabled_search_raises():
    service = _service(config_provider=lambda: SearchConfig(enabled=False))
    with pytest.raises(DiscoveryError) as info:
        service.search(SearchRequest(query="猫", platforms=[Platform.BILIBILI]))
    assert info.value.issue_code is SiteIssueCode.LOCAL_CONFIGURATION
