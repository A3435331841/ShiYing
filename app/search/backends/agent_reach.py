"""Agent-Reach CLI 可选后端（MIT）。

Agent-Reach 已把 B站 / YouTube / 小红书等平台的搜索封装为 CLI，输出统一 JSON：
``{schema_version, ok, channel, action, backend, items, meta, error}``，
错误码包括 NEED_SETUP / NEED_LOGIN / UPSTREAM_BROKEN / TIMEOUT / RATE_LIMITED /
NOT_FOUND / BAD_INPUT。本文件只做“探测、调用、解析、错误码映射”，不复制其代码。

未安装 agent-reach 时 available() 返回 False，服务层自动回退内置后端。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ...errors import (
    AuthenticationRequiredError,
    DiscoveryError,
    SiteIssueCode,
    TemporaryAccessError,
)
from ...models import Platform
from ...platforms import identify_url
from ..models import SearchHit

MAX_OUTPUT_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 40.0
# doctor 是无网络的本地探测，但状态接口可能被频繁请求，缓存一小段时间。
_STATUS_CACHE_TTL_SECONDS = 120.0
_channel_status_cache: tuple[float, dict[str, dict[str, Any]]] | None = None
# doctor 认为这些状态代表渠道已接入（unverified = 已接入但未实测）。
_READY_CHANNEL_STATUSES = frozenset({"ready", "verified", "unverified"})

CHANNEL_BY_PLATFORM: dict[Platform, str] = {
    Platform.BILIBILI: "bilibili",
    Platform.YOUTUBE: "youtube",
    Platform.XIAOHONGSHU: "xiaohongshu",
}

_ERROR_CODES = {
    "NEED_SETUP": SiteIssueCode.LOCAL_CONFIGURATION,
    "NEED_LOGIN": SiteIssueCode.LOGIN_REQUIRED,
    "RATE_LIMITED": SiteIssueCode.RATE_LIMITED,
    "TIMEOUT": SiteIssueCode.NETWORK_ERROR,
    "UPSTREAM_BROKEN": SiteIssueCode.SITE_RESPONSE_CHANGED,
    "BAD_INPUT": SiteIssueCode.REQUEST_REJECTED,
    "NOT_FOUND": SiteIssueCode.CONTENT_UNAVAILABLE,
}


def find_agent_reach() -> str | None:
    """定位 agent-reach 可执行文件（PATH 或 uv tool 默认 bin 目录）。"""

    found = shutil.which("agent-reach")
    if found:
        return found
    candidates = [
        Path.home() / ".local" / "bin" / "agent-reach",
        Path.home() / ".local" / "bin" / "agent-reach.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _parse_json_output(stdout: str) -> dict[str, Any] | None:
    """CLI 可能混入日志行：优先整体解析，其次截取第一个 JSON 对象。"""

    text = (stdout or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except ValueError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            return value if isinstance(value, dict) else None
        except ValueError:
            return None
    return None


def _string_field(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            # 常见嵌套：{"title": {"text": ...}} / {"user": {"name": ...}}
            for nested in ("text", "name", "title", "url"):
                nested_value = value.get(nested)
                if isinstance(nested_value, str) and nested_value.strip():
                    return nested_value.strip()
    return None


def _media_id_from_url(platform: Platform, url: str) -> str | None:
    from urllib.parse import parse_qsl, urlsplit

    parsed = urlsplit(url)
    path = parsed.path
    if platform is Platform.YOUTUBE:
        video_id = dict(parse_qsl(parsed.query)).get("v")
        if video_id:
            return video_id
        if path.startswith("/shorts/"):
            return path.rsplit("/", 1)[-1]
        return None
    if platform is Platform.BILIBILI:
        match = re.search(r"/video/(BV[0-9A-Za-z]{10}|av\d+)", path)
        return match.group(1) if match else None
    if platform is Platform.XIAOHONGSHU:
        match = re.search(r"/(?:explore|discovery/item)/([0-9a-zA-Z]+)", path)
        return match.group(1) if match else None
    if platform is Platform.DOUYIN:
        match = re.search(r"/(?:video|note)/(\d+)", path)
        return match.group(1) if match else None
    if platform is Platform.KUAISHOU:
        match = re.search(r"/short-video/([0-9A-Za-z_-]+)", path)
        return match.group(1) if match else None
    return None


def hit_from_item(platform: Platform, item: dict[str, Any], keyword: str) -> SearchHit | None:
    """把 Agent-Reach 的条目宽容映射为 SearchHit；不匹配平台域名的条目直接丢弃。"""

    url = _string_field(item, "url", "webpage_url", "link", "source_url")
    if not url:
        return None
    try:
        info = identify_url(url)
    except Exception:  # noqa: BLE001 - 非法/不支持的链接一律丢弃
        return None
    if info.platform is not platform:
        return None
    title = _string_field(item, "title", "name", "desc", "description") or "未命名作品"
    author = _string_field(item, "author", "uploader", "user", "channel", "creator")
    cover = _string_field(item, "cover", "cover_url", "thumbnail", "pic")
    duration = item.get("duration") or item.get("duration_seconds")
    try:
        duration_seconds = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_seconds = None
    media_id = _string_field(item, "id", "bvid", "aweme_id", "note_id") or _media_id_from_url(
        platform, info.url
    )
    return SearchHit(
        platform=platform,
        source_url=info.url,
        media_id=media_id,
        title=title[:200],
        author=author[:80] if author else None,
        cover_url=cover if cover and cover.startswith(("http://", "https://")) else None,
        duration_seconds=duration_seconds,
        published_at=_string_field(item, "published_at", "upload_date", "date"),
        keyword=keyword,
    )


class AgentReachBackend:
    """可选增强：已安装 agent-reach 时用于 bilibili / youtube / xiaohongshu。"""

    platforms = tuple(CHANNEL_BY_PLATFORM)

    def __init__(
        self,
        *,
        executable: str | None = None,
        runner: Any = subprocess.run,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._executable = executable if executable is not None else find_agent_reach()
        self._runner = runner
        self.timeout_seconds = timeout_seconds

    def available(self) -> bool:
        return bool(self._executable)

    def channel_ready(self, platform: Platform) -> bool:
        """Whether this platform's Agent-Reach channel is actually usable.

        The executable can exist while a channel is still not_added/needs_setup;
        calling such a channel only burns timeouts before falling back. doctor is
        local-only and cached, so this check is cheap.
        """

        channel = CHANNEL_BY_PLATFORM.get(platform)
        if not channel or not self._executable:
            return False
        channels = _probe_channel_status(self._executable)
        if not channels:
            return True  # 探测失败时保守允许，由实际调用决定
        return channels.get(channel, {}).get("status") in _READY_CHANNEL_STATUSES

    def search(self, platform: Platform, keyword: str, limit: int = 10) -> list[SearchHit]:
        channel = CHANNEL_BY_PLATFORM.get(platform)
        if channel is None:
            raise DiscoveryError(
                f"{platform.value} 不在 Agent-Reach 渠道内",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )
        if not self._executable:
            raise DiscoveryError(
                "未检测到 agent-reach",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )
        command = [self._executable, channel, "search", keyword, "--limit", str(max(1, min(int(limit), 30)))]
        try:
            completed = self._runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise TemporaryAccessError(
                "Agent-Reach 搜索超时，请稍后重试",
                issue_code=SiteIssueCode.NETWORK_ERROR,
            ) from exc
        except OSError as exc:
            raise DiscoveryError(
                "无法执行 agent-reach，请检查安装",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            ) from exc
        stdout = completed.stdout or ""
        if len(stdout) > MAX_OUTPUT_BYTES:
            raise DiscoveryError(
                "Agent-Reach 输出超过安全大小限制",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            )
        payload = _parse_json_output(stdout)
        if payload is None:
            detail = (completed.stderr or "").strip()[:300]
            raise DiscoveryError(
                f"Agent-Reach 输出不是合法 JSON{'：' + detail if detail else ''}",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            )
        error = payload.get("error")
        if payload.get("ok") is False or error:
            code = ""
            message = ""
            if isinstance(error, dict):
                code = str(error.get("code") or "")
                message = str(error.get("message") or "")
            elif isinstance(error, str):
                code = error
            issue = _ERROR_CODES.get(code, SiteIssueCode.UNKNOWN)
            if code == "NOT_FOUND":
                return []
            if issue is SiteIssueCode.LOGIN_REQUIRED:
                raise AuthenticationRequiredError(
                    f"Agent-Reach 渠道 {channel} 需要登录：先运行 agent-reach login {channel}",
                    issue_code=issue,
                )
            if issue is SiteIssueCode.LOCAL_CONFIGURATION:
                raise DiscoveryError(
                    f"Agent-Reach 尚未就绪（{code or 'NEED_SETUP'}），请运行 agent-reach setup"
                    + (f"：{message}" if message else ""),
                    issue_code=issue,
                )
            raise TemporaryAccessError(
                f"Agent-Reach 搜索失败（{code or 'UNKNOWN'}）{'：' + message if message else ''}",
                issue_code=issue,
            )
        items = payload.get("items")
        if not isinstance(items, list):
            raise DiscoveryError(
                "Agent-Reach 响应缺少 items 列表",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            )
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            hit = hit_from_item(platform, item, keyword)
            if hit is None:
                continue
            key = hit.media_id or hit.source_url
            if key in seen:
                continue
            seen.add(key)
            hits.append(hit)
            if len(hits) >= limit:
                break
        return hits


def _probe_channel_status(executable: str) -> dict[str, dict[str, Any]] | None:
    """Read per-channel readiness via `agent-reach doctor` (no network)."""

    global _channel_status_cache
    now = time.monotonic()
    if (
        _channel_status_cache is not None
        and now - _channel_status_cache[0] < _STATUS_CACHE_TTL_SECONDS
    ):
        return _channel_status_cache[1]
    try:
        completed = subprocess.run(
            [executable, "doctor"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, OSError):
        _channel_status_cache = (now, {})
        return {}
    stdout = completed.stdout or ""
    if len(stdout) > MAX_OUTPUT_BYTES:
        _channel_status_cache = (now, {})
        return {}
    payload = _parse_json_output(stdout)
    result = payload.get("result") if isinstance(payload, dict) else None
    channels = result.get("channels") if isinstance(result, dict) else None
    if not isinstance(channels, list):
        _channel_status_cache = (now, {})
        return {}
    parsed: dict[str, dict[str, Any]] = {}
    for entry in channels:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("channel") or "").strip()
        if not name:
            continue
        problems = entry.get("problems")
        parsed[name] = {
            "status": str(entry.get("status") or ""),
            "problems": [str(p) for p in problems if p] if isinstance(problems, list) else [],
            "hint": str(entry.get("hint") or ""),
        }
    _channel_status_cache = (now, parsed)
    return parsed


def status() -> dict[str, Any]:
    """供设置页展示：安装情况与各搜索渠道的真实就绪状态。"""

    executable = find_agent_reach()
    result: dict[str, Any] = {
        "available": bool(executable),
        "executable": os.path.basename(executable) if executable else None,
        "install_hint": (
            "uv tool install "
            "https://github.com/Panniantong/Agent-Reach/archive/refs/tags/v1.6.0.zip"
            " 然后运行 agent-reach setup"
        ),
    }
    if not executable:
        return result
    channels = _probe_channel_status(executable)
    if not channels:
        return result  # 探测失败时保守地只报告安装情况
    result["channels"] = channels
    ready = sorted(
        channel
        for channel in CHANNEL_BY_PLATFORM.values()
        if channels.get(channel, {}).get("status") in _READY_CHANNEL_STATUSES
    )
    result["ready_channels"] = ready
    # 状态接口反映真实可用的搜索渠道，而不是「可执行文件存在」。
    result["available"] = bool(ready)
    missing = [channel for channel in CHANNEL_BY_PLATFORM.values() if channel not in ready]
    if missing:
        result["install_hint"] = (
            "运行 agent-reach add " + "、".join(missing) + " 安装缺失的渠道后端"
        )
    return result
