"""搜索登录态：从 Chrome 一次性导入，DPAPI 加密持久化。

抖音/快手/小红书的匿名搜索会被验证码/风控拦截；浏览器后端需要登录态。
Chrome 运行时会独占锁住 Cookie 数据库，因此导入必须由用户在关闭 Chrome 后
手动触发一次；导入后搜索直接使用本机加密副本，不再依赖 Chrome 进程。
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any

from ..errors import DiscoveryError, SiteIssueCode
from ..models import Platform
from ..wechat_secret import load_secret, save_secret

MAX_COOKIE_BYTES = 4 * 1024 * 1024

COOKIE_DOMAINS: dict[Platform, tuple[str, ...]] = {
    Platform.DOUYIN: ("douyin.com",),
    Platform.KUAISHOU: ("kuaishou.com", "gifshow.com"),
    Platform.XIAOHONGSHU: ("xiaohongshu.com",),
}
COOKIE_LABELS = {
    Platform.DOUYIN: "抖音",
    Platform.KUAISHOU: "快手",
    Platform.XIAOHONGSHU: "小红书",
}
COOKIE_LABEL_BY_VALUE = {platform.value: label for platform, label in COOKIE_LABELS.items()}


class SearchCookieStore:
    """平台 Cookie 的加密存储（明文 JSON → DPAPI 密文文件）。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def save(self, payload: dict[str, list[dict[str, Any]]]) -> None:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(text.encode("utf-8")) > MAX_COOKIE_BYTES:
            raise DiscoveryError(
                "登录态数据超过安全大小限制",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )
        save_secret(self.path, text)

    def load(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.is_file():
            return {}
        try:
            text = load_secret(self.path)
        except OSError:
            return {}
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except ValueError:
            return {}
        if not isinstance(payload, dict):
            return {}
        result: dict[str, list[dict[str, Any]]] = {}
        for key, value in payload.items():
            if isinstance(key, str) and isinstance(value, list):
                result[key] = [item for item in value if isinstance(item, dict)]
        return result

    def cookies_for(self, platform: Platform) -> list[dict[str, Any]]:
        return list(self.load().get(platform.value, []))

    def status(self) -> dict[str, int]:
        payload = self.load()
        return {
            platform.value: len(payload.get(platform.value, []))
            for platform in COOKIE_DOMAINS
        }

    def clear(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        with contextlib.suppress(FileNotFoundError):
            self.path.with_name(self.path.name + ".corrupt").unlink()


def import_from_chrome(profile: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """从 Chrome 读取三个平台的 Cookie。

    Chrome 运行时会锁住 Cookie 数据库；此时返回可行动的错误，提示用户完全
    退出 Chrome（含后台驻留进程）后重试。
    """

    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except ImportError as exc:  # pragma: no cover - yt-dlp 是运行时依赖
        raise DiscoveryError(
            "当前环境缺少 yt-dlp，无法读取 Chrome 登录态",
            issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
        ) from exc

    class _QuietLogger:
        def debug(self, message: str) -> None: ...
        def info(self, message: str) -> None: ...
        def warning(self, message: str) -> None: ...
        def error(self, message: str) -> None: ...

    try:
        jar = extract_cookies_from_browser("chrome", profile=profile, logger=_QuietLogger())
    except Exception as exc:  # noqa: BLE001 - 统一转成用户可行动的错误
        message = str(exc)
        lowered = message.lower()
        if "decrypt" in lowered or "app-bound" in lowered:
            raise DiscoveryError(
                "Chrome 新版启用了 App-Bound 加密，无法导出 Cookie；请改用设置中的平台登录按钮扫码登录",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            ) from exc
        if "copy" in lowered or "database" in lowered:
            raise DiscoveryError(
                "无法读取 Chrome 登录态：请完全退出 Chrome（包括后台进程）后重试，或改用平台登录按钮扫码登录",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            ) from exc
        raise DiscoveryError(
            "无法读取 Chrome 登录态，请确认已安装 Chrome 并使用默认用户配置",
            issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
        ) from exc

    now = time.time()
    payload: dict[str, list[dict[str, Any]]] = {platform.value: [] for platform in COOKIE_DOMAINS}
    for cookie in jar:
        domain = (cookie.domain or "").lower().lstrip(".")
        for platform, roots in COOKIE_DOMAINS.items():
            if not any(domain == root or domain.endswith("." + root) for root in roots):
                continue
            if cookie.expires and cookie.expires <= now:
                continue
            payload[platform.value].append(
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
            break
    return payload
