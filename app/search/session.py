"""搜索专用浏览器登录态：持久化 Profile + 扫码登录窗口。

新版 Chrome（127+）对 Cookie 启用 App-Bound Encryption，第三方进程用 DPAPI
无法解密（yt-dlp #10927），因此“从 Chrome 导入”并不可靠。这里改用拾影
自己的持久化浏览器 Profile：用户在弹出的真实窗口里扫码登录一次，之后所有
搜索复用该 Profile，不再依赖 Chrome 进程或 Cookie 导出。
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from ..errors import DiscoveryError, SiteIssueCode
from ..models import Platform

LOGIN_URLS: dict[Platform, str] = {
    Platform.DOUYIN: "https://www.douyin.com/",
    Platform.KUAISHOU: "https://www.kuaishou.com/",
    Platform.XIAOHONGSHU: "https://www.xiaohongshu.com/",
}
LOGIN_COOKIES: dict[Platform, set[str]] = {
    Platform.DOUYIN: {"sessionid", "sessionid_ss"},
    Platform.KUAISHOU: {"passToken", "userId"},
    Platform.XIAOHONGSHU: {"web_session"},
}
COOKIE_ROOTS: dict[Platform, tuple[str, ...]] = {
    Platform.DOUYIN: ("douyin.com",),
    Platform.KUAISHOU: ("kuaishou.com", "gifshow.com"),
    Platform.XIAOHONGSHU: ("xiaohongshu.com",),
}
LABELS = {
    Platform.DOUYIN: "抖音",
    Platform.KUAISHOU: "快手",
    Platform.XIAOHONGSHU: "小红书",
}
DEFAULT_LOGIN_TIMEOUT_SECONDS = 300.0
_STATUS_FILE = "search-login.json"
# 同一 Profile 不能被两个 Playwright 实例同时打开：登录窗口与搜索后端共用一把锁。
PROFILE_LOCK_TIMEOUT_SECONDS = 5.0
_PROFILE_LOCKS: dict[str, threading.Lock] = {}
_PROFILE_LOCKS_GUARD = threading.Lock()


def profile_lock(profile_dir: Path | str) -> threading.Lock:
    """按 Profile 目录取进程内共享锁（同一目录的搜索与登录互斥）。"""

    key = os.path.normcase(os.path.abspath(str(profile_dir)))
    with _PROFILE_LOCKS_GUARD:
        lock = _PROFILE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PROFILE_LOCKS[key] = lock
        return lock


def _marker_path(profile_dir: Path) -> Path:
    return Path(profile_dir).with_name(_STATUS_FILE)


def read_login_status(profile_dir: Path) -> dict[str, bool]:
    path = _marker_path(profile_dir)
    if not path.is_file():
        return {platform.value: False for platform in LOGIN_URLS}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {platform.value: False for platform in LOGIN_URLS}
    return {
        platform.value: bool(payload.get(platform.value))
        for platform in LOGIN_URLS
    }


def record_login(profile_dir: Path, platform: Platform, value: bool) -> None:
    path = _marker_path(profile_dir)
    payload = read_login_status(profile_dir)
    payload[platform.value] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def logged_in(platform: Platform, cookies: list[dict[str, Any]]) -> bool:
    markers = LOGIN_COOKIES[platform]
    roots = COOKIE_ROOTS[platform]
    for cookie in cookies:
        domain = str(cookie.get("domain") or "").lower().lstrip(".")
        if not any(domain == root or domain.endswith("." + root) for root in roots):
            continue
        if cookie.get("name") in markers and cookie.get("value"):
            return True
    return False


def login_with_browser(
    platform: Platform,
    profile_dir: Path,
    *,
    timeout_seconds: float = DEFAULT_LOGIN_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """打开真实登录窗口，等待扫码/登录完成（按 Cookie 标记判定）。"""

    if platform not in LOGIN_URLS:
        raise DiscoveryError(
            f"{LABELS.get(platform, platform)} 不支持搜索登录",
            issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
        )
    lock = profile_lock(profile_dir)
    if not lock.acquire(timeout=PROFILE_LOCK_TIMEOUT_SECONDS):
        raise DiscoveryError(
            "搜索或另一个登录窗口正在使用搜索 Profile，请稍后再试",
            issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
        )
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        profile_dir = Path(profile_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)
        playwright = None
        context = None
        try:
            playwright = sync_playwright().start()
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel="chrome",
                headless=False,
                viewport={"width": 1180, "height": 820},
                locale="zh-CN",
            )
        except PlaywrightError as exc:
            # 启动失败（Chrome 缺失/Profile 被占用）时必须回收已启动的驱动进程。
            if playwright is not None:
                with contextlib.suppress(Exception):
                    playwright.stop()
            raise DiscoveryError(
                "无法启动登录窗口，请确认已安装 Google Chrome，且没有其他登录窗口占用搜索 Profile",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            ) from exc
        try:
            pages = context.pages or []
            page = pages[0] if pages else context.new_page()
            with contextlib.suppress(PlaywrightError):
                page.goto(LOGIN_URLS[platform], wait_until="domcontentloaded", timeout=30000)
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                with contextlib.suppress(Exception):
                    if logged_in(platform, context.cookies()):
                        record_login(profile_dir, platform, True)
                        return {"ok": True, "platform": platform.value}
                page.wait_for_timeout(1000)
            return {
                "ok": False,
                "platform": platform.value,
                "message": "等待登录超时，请重试并在窗口内完成扫码/登录",
            }
        finally:
            with contextlib.suppress(Exception):
                context.close()
            with contextlib.suppress(Exception):
                playwright.stop()
    finally:
        lock.release()
