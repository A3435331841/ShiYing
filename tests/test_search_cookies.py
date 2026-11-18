"""搜索登录态：导入、DPAPI 存储与错误映射。"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.errors import DiscoveryError, SiteIssueCode
from app.models import Platform
from app.search import cookies as module
from app.search.cookies import SearchCookieStore, import_from_chrome


class FakeCookie:
    def __init__(self, name, value, domain, expires=None):
        self.name = name
        self.value = value
        self.domain = domain
        self.path = "/"
        self.secure = True
        self.expires = expires


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI 仅 Windows 可用")
def test_store_roundtrip_and_status(tmp_path):
    store = SearchCookieStore(tmp_path / "search-cookies.txt")
    payload = {
        "douyin": [{"name": "sessionid", "value": "x", "domain": ".douyin.com"}],
        "kuaishou": [{"name": "did", "value": "y", "domain": ".kuaishou.com"}],
        "xiaohongshu": [],
    }
    store.save(payload)
    assert (tmp_path / "search-cookies.txt").read_bytes().startswith(b"SHIYING-DPAPI-1")
    status = store.status()
    assert status["douyin"] == 1
    assert status["kuaishou"] == 1
    assert status["xiaohongshu"] == 0
    assert store.cookies_for(Platform.DOUYIN)[0]["name"] == "sessionid"
    store.clear()
    assert store.status() == {"douyin": 0, "kuaishou": 0, "xiaohongshu": 0}


def test_import_from_chrome_filters_domains_and_expired(monkeypatch):
    jar = [
        FakeCookie("sessionid", "a", ".douyin.com"),
        FakeCookie("did", "b", ".kuaishou.com"),
        FakeCookie("web_session", "c", ".xiaohongshu.com"),
        FakeCookie("other", "d", ".example.com"),
        FakeCookie("expired", "e", ".douyin.com", expires=1),
    ]

    class FakeYdlCookies:
        @staticmethod
        def extract_cookies_from_browser(browser, profile=None, logger=None):
            assert browser == "chrome"
            return jar

    import yt_dlp.cookies

    monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", FakeYdlCookies.extract_cookies_from_browser)
    payload = import_from_chrome()
    assert [item["name"] for item in payload["douyin"]] == ["sessionid"]
    assert [item["name"] for item in payload["kuaishou"]] == ["did"]
    assert [item["name"] for item in payload["xiaohongshu"]] == ["web_session"]


def test_import_lock_error_maps_to_actionable_message(monkeypatch):
    import yt_dlp.cookies

    def fail(*args, **kwargs):
        raise RuntimeError("Could not copy Chrome cookie database")

    monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", fail)
    with pytest.raises(DiscoveryError) as info:
        import_from_chrome()
    assert info.value.issue_code is SiteIssueCode.LOCAL_CONFIGURATION
    assert "完全退出 Chrome" in str(info.value)
