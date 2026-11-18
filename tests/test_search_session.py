"""搜索登录态标记与状态文件（不启动浏览器）。"""

from __future__ import annotations

from app.models import Platform
from app.search.session import logged_in, read_login_status, record_login


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
