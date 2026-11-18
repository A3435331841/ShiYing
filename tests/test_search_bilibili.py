"""B站搜索后端：响应解析、风控/登录/结构变化分类（离线 fake session）。"""

from __future__ import annotations

import json

import pytest

from app.errors import AuthenticationRequiredError, DiscoveryError, SiteIssueCode, TemporaryAccessError
from app.search.backends import bilibili as module
from app.search.backends.bilibili import BilibiliSearchBackend, hit_from_item


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content or (json.dumps(payload or {}).encode("utf-8") if payload else b"")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, search_response=None, warm_response=None):
        self.search_response = search_response
        self.warm_response = warm_response or FakeResponse(
            payload={"code": 0}, content=b"<html></html>"
        )
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == module.BILIBILI_SEARCH_API:
            return self.search_response
        return self.warm_response


def _payload(items):
    return {"code": 0, "data": {"result": items}}


ITEM = {
    "bvid": "BV1GJ411x7h7",
    "title": '【<em class="keyword">猫</em>】测试视频',
    "author": "作者A",
    "pic": "//i0.hdslb.com/bfs/archive/cover.jpg",
    "duration": "12:34",
    "pubdate": 1700000000,
}


def test_hit_from_item_cleans_title_and_cover():
    hit = hit_from_item(ITEM, "猫")
    assert hit is not None
    assert hit.source_url == "https://www.bilibili.com/video/BV1GJ411x7h7"
    assert hit.media_id == "BV1GJ411x7h7"
    assert "猫" in hit.title and "<em" not in hit.title
    assert hit.author == "作者A"
    assert hit.cover_url.startswith("https://i0.hdslb.com/")
    assert hit.duration_seconds == 12 * 60 + 34


def test_hit_requires_valid_bvid():
    assert hit_from_item({"title": "x", "bvid": "bad"}, "kw") is None
    assert hit_from_item({"aid": 123, "title": "aid only"}, "kw") is None


def test_search_returns_hits_and_warms_cookies():
    session = FakeSession(FakeResponse(payload=_payload([ITEM, ITEM, {**ITEM, "bvid": "BV1xx411c7mD"}])))
    backend = BilibiliSearchBackend(session=session)
    hits = backend.search("猫", 5)
    assert [hit.media_id for hit in hits] == ["BV1GJ411x7h7", "BV1xx411c7mD"]
    assert session.calls[0][0] == module.BILIBILI_HOME  # 预热 buvid3


def test_http_412_maps_to_rate_limited():
    session = FakeSession(FakeResponse(status_code=412, payload={}))
    backend = BilibiliSearchBackend(session=session)
    with pytest.raises(TemporaryAccessError) as info:
        backend.search("猫", 5)
    assert info.value.issue_code is SiteIssueCode.RATE_LIMITED


def test_code_minus_101_maps_to_login_required():
    session = FakeSession(FakeResponse(payload={"code": -101, "message": "账号未登录"}))
    backend = BilibiliSearchBackend(session=session)
    with pytest.raises(AuthenticationRequiredError) as info:
        backend.search("猫", 5)
    assert info.value.issue_code is SiteIssueCode.LOGIN_REQUIRED


def test_unexpected_payload_maps_to_response_changed():
    session = FakeSession(FakeResponse(payload={"code": 0, "data": {}}))
    backend = BilibiliSearchBackend(session=session)
    with pytest.raises(DiscoveryError) as info:
        backend.search("猫", 5)
    assert info.value.issue_code is SiteIssueCode.SITE_RESPONSE_CHANGED


def test_empty_keyword_returns_empty_without_request():
    session = FakeSession(FakeResponse(payload=_payload([])))
    backend = BilibiliSearchBackend(session=session)
    assert backend.search("   ", 5) == []
    assert session.calls == []
