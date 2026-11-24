"""搜索专用浏览器登录态：持久化 Profile + 扫码登录窗口。

新版 Chrome（127+）对 Cookie 启用 App-Bound Encryption，第三方进程用 DPAPI
无法解密（yt-dlp #10927），因此“从 Chrome 导入”并不可靠。这里改用拾影
自己的持久化浏览器 Profile：用户在弹出的真实窗口里扫码登录一次，之后所有
搜索复用该 Profile，不再依赖 Chrome 进程或 Cookie 导出。
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import subprocess
import sys
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
# 搜索后端为隐藏窗口会把位置写成 -32000；登录必须显式指定屏幕内坐标，
# 否则登录窗口继承坏掉的 Preferences 后会开在屏幕外，用户只看到进程一闪。
LOGIN_WINDOW_ARGS = (
    "--window-position=120,80",
    "--window-size=1180,820",
    "--no-first-run",
    "--no-default-browser-check",
)
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


def _reset_window_placement(profile_dir: Path) -> None:
    """复位屏幕外窗口位置与崩溃会话，保证登录窗口在屏幕内出现。"""

    # 上一次强制回收会让 Chrome 标记 Crashed，下次启动按旧会话恢复屏幕外窗口。
    sessions = profile_dir / "Default" / "Sessions"
    if sessions.is_dir():
        for item in sessions.iterdir():
            if item.is_file():
                with contextlib.suppress(OSError):
                    item.unlink()

    preferences = profile_dir / "Default" / "Preferences"
    if not preferences.is_file():
        return
    try:
        payload = json.loads(preferences.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    changed = False
    browser = payload.get("browser")
    placement = browser.get("window_placement") if isinstance(browser, dict) else None
    if isinstance(placement, dict) and (
        placement.get("left", 0) < 0 or placement.get("top", 0) < 0
    ):
        placement.update(
            {"left": 120, "top": 80, "right": 1300, "bottom": 900, "maximized": False}
        )
        changed = True
    profile = payload.get("profile")
    if isinstance(profile, dict) and profile.get("exit_type") == "Crashed":
        profile["exit_type"] = "Normal"
        profile["exited_cleanly"] = True
        changed = True
    if not changed:
        return
    try:
        temporary = preferences.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(preferences)
    except OSError:
        pass  # Chrome 正在写入时保持原样；命令行参数仍会覆盖位置。


def _profile_chrome_pids(profile_dir: Path) -> set[int]:
    """列出使用本项目 Profile 的 Chrome 进程号（按命令行精确匹配）。"""

    if sys.platform != "win32":
        return set()
    marker = f"--user-data-dir={profile_dir}"
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$marker='{marker.replace(chr(39), chr(39) * 2)}';"
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($marker) } | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                base64.b64encode(script.encode("utf-16le")).decode("ascii"),
            ],
            capture_output=True,
            text=True,
            # 中文 Windows 下 PowerShell 可能输出 GBK/区位字节；UTF-8 模式下
            # 严格解码会让 subprocess 的读取线程抛 UnicodeDecodeError 而崩溃。
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return {int(line) for line in result.stdout.splitlines() if line.strip().isdigit()}


def _sweep_stale_chrome(profile_dir: Path) -> None:
    """按 user-data-dir 精确回收本项目遗留的 Chrome，绝不触碰用户自己的浏览器。"""

    if sys.platform != "win32":
        return
    marker = f"--user-data-dir={profile_dir}"
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$marker='{marker.replace(chr(39), chr(39) * 2)}';"
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($marker) } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                base64.b64encode(script.encode("utf-16le")).decode("ascii"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass  # 清理失败不应阻断登录；Playwright 自己也会关闭浏览器。


def _activate_login_window(profile_dir: Path) -> bool:
    """把登录窗口切到前台；Playwright 的 bring_to_front 只激活标签页。"""

    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            pids = _profile_chrome_pids(profile_dir)
            windows: list[int] = []
            if pids:

                def collect(hwnd: int, _: int) -> bool:
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value in pids and user32.IsWindowVisible(hwnd):
                        length = user32.GetWindowTextLengthW(hwnd)
                        if length:
                            buffer = ctypes.create_unicode_buffer(length + 1)
                            user32.GetWindowTextW(hwnd, buffer, length + 1)
                            if buffer.value:
                                windows.append(hwnd)
                    return True

                user32.EnumWindows(enum_proc(collect), 0)
            if windows:
                # Windows 的前台锁会拒绝后台进程的 SetForegroundWindow；
                # 一次 ALT 按键可合法解除该限制。
                user32.keybd_event(0x12, 0, 0, 0)
                user32.keybd_event(0x12, 0, 2, 0)
                for hwnd in windows:
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    user32.SetForegroundWindow(hwnd)
                    # 前台锁可能拒绝置顶，至少让扫码窗口浮在其他窗口之上。
                    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)
                    if user32.GetForegroundWindow() == hwnd:
                        return True
                return True
            time.sleep(0.3)
    except Exception:
        pass  # 前台激活失败不影响扫码本身，窗口仍在屏幕内可见。
    return False


def _login_window_closed(page: Any) -> bool:
    """用户手动关掉登录窗口时立即结束等待，而不是空等 5 分钟。"""

    try:
        if page.is_closed():
            return True
        page.evaluate("1")
        return False
    except Exception:
        return True


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
        _reset_window_placement(profile_dir)
        _sweep_stale_chrome(profile_dir)
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
                args=list(LOGIN_WINDOW_ARGS),
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
            try:
                page.goto(LOGIN_URLS[platform], wait_until="domcontentloaded", timeout=30000)
            except PlaywrightError as exc:
                return {
                    "ok": False,
                    "platform": platform.value,
                    "message": f"登录页面打开失败：{str(exc)[:160]}",
                }
            with contextlib.suppress(Exception):
                page.bring_to_front()
            _activate_login_window(profile_dir)
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                if _login_window_closed(page):
                    return {
                        "ok": False,
                        "platform": platform.value,
                        "message": "登录窗口已关闭，未完成登录；请重新点击平台登录",
                    }
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
            _sweep_stale_chrome(profile_dir)
    finally:
        lock.release()
