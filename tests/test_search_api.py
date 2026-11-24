"""搜索 API：配置门控、状态、密钥只写、缩略图白名单（TestClient）。"""

from __future__ import annotations

import base64
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main
from app.errors import DiscoveryError, SiteIssueCode
from app.models import Platform
from app.search.models import (
    MAX_IMAGE_DATA_URI_CHARS,
    SearchHit,
    SearchPlan,
    SearchRequest,
    SearchResponse,
)
from app.search.routes import PLATFORM_LABELS, trusted_thumbnail_url


@pytest.fixture
def client():
    test_client = TestClient(main.app, base_url="http://localhost")
    try:
        yield test_client
    finally:
        test_client.close()


def test_status_lists_platforms_and_unsearchable(client):
    response = client.get("/api/search/status")
    assert response.status_code == 200
    data = response.json()
    values = {item["value"] for item in data["platforms"]}
    assert values == {"bilibili", "youtube", "xiaohongshu", "douyin", "kuaishou"}
    assert "selected-douyin" in data["unsearchable"]
    assert "wechat" in data["unsearchable"]
    assert data["unsearchable"]["wechat"]["label"] == "视频号"
    assert "agent_reach" in data


def test_disabled_config_allows_keyword_search(client, monkeypatch):
    monkeypatch.setattr(main, "config", main.AppConfig(ai_search_enabled=False))

    def fake_search(self, request):
        assert request.query == "猫"
        assert request.ai_mode is False
        return SearchResponse(
            plan=SearchPlan(keywords={"bilibili": ["猫"]}, fallback_reason="AI 搜索已在设置中关闭，使用原始关键词"),
            results=[],
            hits=[],
        )

    from app.search.service import SearchService

    monkeypatch.setattr(SearchService, "search", fake_search)
    response = client.post(
        "/api/search",
        json={"query": "猫", "platforms": ["bilibili"], "ai_mode": False},
    )
    assert response.status_code == 200
    assert response.json()["plan"]["fallback_reason"]


def test_search_returns_payload_with_results(client, monkeypatch):
    def fake_search(self, request):
        hit = SearchHit(
            platform=Platform.BILIBILI,
            source_url="https://www.bilibili.com/video/BV1GJ411x7h7",
            media_id="BV1GJ411x7h7",
            title="测试结果",
        )
        return SearchResponse(
            plan=SearchPlan(keywords={"bilibili": ["猫"]}),
            results=[],
            hits=[hit],
        )

    from app.search.service import SearchService

    monkeypatch.setattr(SearchService, "search", fake_search)
    response = client.post(
        "/api/search",
        json={"query": "猫", "platforms": ["bilibili"], "ai_mode": False, "limit": 5},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["hits"][0]["title"] == "测试结果"
    assert data["plan"]["keywords"]["bilibili"] == ["猫"]


def test_search_rejects_empty_query_and_unsupported_platform(client):
    assert client.post("/api/search", json={"query": None, "image": None}).status_code == 422
    response = client.post(
        "/api/search",
        json={"query": "猫", "platforms": ["wechat"]},
    )
    assert response.status_code == 422


def test_search_rejects_empty_request_body(client):
    response = client.post(
        "/api/search", json={"platforms": ["bilibili"], "ai_mode": False}
    )
    assert response.status_code == 422
    assert "描述或图片" in response.json()["detail"]["message"]


def test_search_accepts_image_data_uri_larger_than_8m_chars(client, monkeypatch):
    # 7MB 原图 base64 后约 9.8MB 字符；旧的 8Mi 字符上限会误拒合法图片。
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * (7 * 1024 * 1024)
    data_uri = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    assert len(data_uri) > 8 * 1024 * 1024

    def fake_search(self, request):
        assert request.image == data_uri
        return SearchResponse(plan=SearchPlan(keywords={}), results=[], hits=[])

    from app.search.service import SearchService

    monkeypatch.setattr(SearchService, "search", fake_search)
    response = client.post(
        "/api/search", json={"image": data_uri, "platforms": ["bilibili"]}
    )
    assert response.status_code == 200


def test_search_rejects_oversize_image_with_chinese_message():
    data_uri = "data:image/png;base64," + "A" * MAX_IMAGE_DATA_URI_CHARS
    with pytest.raises(ValidationError) as info:
        SearchRequest(image=data_uri)
    assert "图片超过 8MB 限制" in str(info.value)


@pytest.mark.skipif(sys.platform != "win32", reason="密钥存储依赖 Windows DPAPI")
def test_key_is_write_only_and_never_echoed(client, tmp_path, monkeypatch):
    monkeypatch.setattr(main.SEARCH_KEY_STORE, "path", tmp_path / "llm-api-key.txt")
    saved = client.post("/api/search/key", json={"api_key": "sk-test-secret"})
    assert saved.status_code == 200
    assert saved.json() == {"configured": True}
    status = client.get("/api/search/status").json()
    assert status["key_configured"] is True
    config = client.get("/api/config").json()
    assert "sk-test-secret" not in str(config)
    cleared = client.delete("/api/search/key")
    assert cleared.json() == {"configured": False}
    assert client.get("/api/search/status").json()["key_configured"] is False


def test_thumbnail_host_whitelist():
    assert trusted_thumbnail_url(Platform.BILIBILI, "https://i0.hdslb.com/a.jpg")
    assert trusted_thumbnail_url(Platform.YOUTUBE, "https://i.ytimg.com/vi/x/hqdefault.jpg")
    assert trusted_thumbnail_url(Platform.DOUYIN, "https://p3.douyinpic.com/a.jpg")
    assert not trusted_thumbnail_url(Platform.BILIBILI, "http://i0.hdslb.com/a.jpg")
    assert not trusted_thumbnail_url(Platform.BILIBILI, "https://i0.hdslb.com.evil.com/a.jpg")
    assert not trusted_thumbnail_url(Platform.BILIBILI, "https://127.0.0.1/a.jpg")
    assert not trusted_thumbnail_url(Platform.BILIBILI, "https://user:pass@i0.hdslb.com/a.jpg")
    assert not trusted_thumbnail_url(Platform.BILIBILI, "https://i0.hdslb.com:8080/a.jpg")


@pytest.mark.skipif(sys.platform != "win32", reason="登录态存储依赖 Windows DPAPI")
def test_cookies_import_and_clear(client, tmp_path, monkeypatch):
    import app.search.routes as routes

    monkeypatch.setattr(main.SEARCH_COOKIE_STORE, "path", tmp_path / "search-cookies.txt")
    monkeypatch.setattr(
        routes,
        "import_from_chrome",
        lambda profile=None: {
            "douyin": [{"name": "sessionid", "value": "x", "domain": ".douyin.com"}],
            "kuaishou": [],
            "xiaohongshu": [],
        },
    )
    imported = client.post("/api/search/cookies/import", json={})
    assert imported.status_code == 200
    assert imported.json()["imported"]["douyin"] == 1
    status = client.get("/api/search/status").json()
    assert status["cookies"]["douyin"]["count"] == 1
    assert status["cookies"]["kuaishou"]["count"] == 0
    assert client.delete("/api/search/cookies").json() == {"cleared": True}
    assert client.get("/api/search/status").json()["cookies"]["douyin"]["count"] == 0


@pytest.mark.skipif(sys.platform != "win32", reason="登录态存储依赖 Windows DPAPI")
def test_cookies_import_lock_error_is_actionable(client, monkeypatch):
    import app.search.routes as routes

    def fail(profile=None):
        raise DiscoveryError(
            "无法读取 Chrome 登录态：请完全退出 Chrome（包括后台进程）后重试",
            issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
        )

    monkeypatch.setattr(routes, "import_from_chrome", fail)
    response = client.post("/api/search/cookies/import", json={})
    assert response.status_code == 422
    assert "完全退出 Chrome" in response.json()["detail"]["message"]


def test_thumbnail_endpoint_rejects_untrusted_and_proxies_trusted(client, monkeypatch):
    response = client.get(
        "/api/search/thumbnail",
        params={"url": "https://evil.example/a.jpg", "platform": "bilibili"},
    )
    assert response.status_code == 422

    class FakeUpstream:
        status_code = 200
        headers = {"content-type": "image/jpeg", "content-length": "4"}

        def iter_content(self, size):
            yield b"\xff\xd8\xff\xd9"

        def close(self):
            pass

    import app.search.routes as routes

    monkeypatch.setattr(routes.requests, "get", lambda *args, **kwargs: FakeUpstream())
    response = client.get(
        "/api/search/thumbnail",
        params={"url": "https://i0.hdslb.com/a.jpg", "platform": "bilibili"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content == b"\xff\xd8\xff\xd9"
