"""搜索登录态标记与状态文件（不启动浏览器）。"""

from __future__ import annotations

import pytest

from app.errors import DiscoveryError, SiteIssueCode, TemporaryAccessError
from app.models import Platform
from app.search import session as session_module
from app.search.backends.browser import BrowserSearchBackend
from app.search.session import (
    logged_in,
    login_with_browser,
    profile_lock,
    read_login_status,
    record_login,
)


def test_login_status_roundtrip(tmp_path):
    profile_dir = tmp_path / "data" / "search-profile"
    status = read_login_status(profile_dir)
    assert status == {"douyin": False, "kuaishou": False, "xiaohongshu": False}
    record_login(profile_dir, Platform.DOUYIN, True)
    status = read_login_status(profile_dir)
    assert status["douyin"] is True
    assert status["kuaishou"] is False


def test_logged_in_marker_detection():
    douyin = [{"name": "sessionid", "value": "x", "domain": ".douyin.com"}]
    kuaishou = [{"name": "passToken", "value": "y", "domain": ".kuaishou.com"}]
    xhs = [{"name": "web_session", "value": "z", "domain": ".xiaohongshu.com"}]
    assert logged_in(Platform.DOUYIN, douyin)
    assert logged_in(Platform.KUAISHOU, kuaishou)
    assert logged_in(Platform.XIAOHONGSHU, xhs)
    # 域不匹配或值为空都不算登录
    assert not logged_in(Platform.KUAISHOU, douyin)
    assert not logged_in(Platform.DOUYIN, [{"name": "sessionid", "value": "", "domain": ".douyin.com"}])
    # 仿冒域不通过
    assert not logged_in(Platform.DOUYIN, [{"name": "sessionid", "value": "x", "domain": ".douyin.com.evil.com"}])


def test_profile_lock_shared_by_login_and_search(tmp_path, monkeypatch):
    """同一 Profile 目录的登录窗口与搜索后端必须互斥。"""

    profile_dir = tmp_path / "data" / "search-profile"
    lock = profile_lock(profile_dir)
    assert profile_lock(str(profile_dir)) is lock  # Path / str 两种写法共用一把锁

    assert lock.acquire(timeout=0)
    try:
        monkeypatch.setattr(session_module, "PROFILE_LOCK_TIMEOUT_SECONDS", 0.01)
        with pytest.raises(DiscoveryError) as login_info:
            login_with_browser(Platform.DOUYIN, profile_dir, timeout_seconds=0.01)
        assert login_info.value.issue_code is SiteIssueCode.LOCAL_CONFIGURATION
        assert "正在使用搜索 Profile" in str(login_info.value)

        backend = BrowserSearchBackend(persistent_profile_dir=profile_dir)
        monkeypatch.setattr(
            backend, "_launch_browser", lambda: pytest.fail("Profile 被占用时不应启动 Chrome")
        )
        with pytest.raises(TemporaryAccessError) as search_info:
            backend.search_many([(Platform.DOUYIN, "猫")], 3)
        assert search_info.value.issue_code is SiteIssueCode.LOCAL_CONFIGURATION
        assert "登录窗口正在使用搜索 Profile" in str(search_info.value)
    finally:
        lock.release()

    # 释放后可再次获取，锁不会被遗留为已占用。
    assert lock.acquire(timeout=0)
    lock.release()


def test_login_launch_failure_stops_playwright(monkeypatch, tmp_path):
    """launch_persistent_context 失败时必须 stop Playwright，避免遗留驱动进程。"""

    import sys
    import types

    stopped: list[bool] = []

    class FakeError(Exception):
        pass

    class FakeChromium:
        def launch_persistent_context(self, **kwargs):
            raise FakeError("launch failed")

    class FakePlaywright:
        chromium = FakeChromium()

        def stop(self):
            stopped.append(True)

    class Manager:
        def start(self):
            return FakePlaywright()

    fake_module = types.ModuleType("playwright.sync_api")
    fake_module.Error = FakeError
    fake_module.sync_playwright = lambda: Manager()
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)

    profile_dir = tmp_path / "data" / "search-profile"
    with pytest.raises(DiscoveryError) as info:
        login_with_browser(Platform.DOUYIN, profile_dir, timeout_seconds=1)
    assert info.value.issue_code is SiteIssueCode.LOCAL_CONFIGURATION
    assert stopped == [True]
