"""Shared constants, helpers and dataclasses for the download engine."""
from __future__ import annotations
import contextlib
import hashlib
import http.client
import ipaddress
import json
import math
import mimetypes
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit, urlunsplit
from yt_dlp import YoutubeDL
from yt_dlp.dependencies import requests, urllib3
from yt_dlp.extractor.tiktok import DouyinIE
from yt_dlp.networking import Request
from yt_dlp.networking._requests import RequestsRH, RequestsResponseAdapter
from yt_dlp.networking.exceptions import (
    CertificateVerifyError,
    HTTPError,
    IncompleteRead,
    ProxyError,
    RequestError,
    SSLError,
    TransportError,
)
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadCancelled, DownloadError, variadic
from ..douyin import discover_profile as discover_douyin_profile
from ..douyin import discover_item_metadata_from_profile
from ..douyin import is_complete_profile_media_metadata
from ..douyin import quality_floor_dimensions
from ..douyin import verified_aweme_metadata
from ..douyin_signing import fetch_signed_aweme_detail, public_signing_diagnostic_code
from ..errors import (
    AuthenticationRequiredError,
    DiscoveryError,
    DouyinMediaRefreshRequiredError,
    DownloadCancelledError,
    MediaDownloadError,
    SiteIssueCode,
    TemporaryAccessError,
    classify_site_issue,
    is_explicit_rate_limit_message,
)
from ..models import DownloadItem, MediaType, Platform, SourceKind, TransferProgress
from ..kuaishou import discover as discover_kuaishou
from ..kuaishou import is_media_url as is_kuaishou_media_url
from ..kuaishou import source_identity as kuaishou_source_identity
from ..bilibili_opus import is_trusted_bilibili_asset_url
from ..platforms import identify_url
from ..file_publish import publish_no_replace, publish_pair_image, publish_unique
from ..xiaohongshu import RemoteAsset, discover_profile as discover_xhs_profile
from ..xiaohongshu import (
    _XiaohongshuRedirectRejected,
    _open_xiaohongshu_response,
)
from ..xiaohongshu import is_trusted_xiaohongshu_asset_url
from ..xiaohongshu import parse_note as parse_xhs_note

__all__ = [
    "MAX_TITLE_BYTES",
    "MAX_MEDIA_ID_BYTES",
    "MAX_FILENAME_COMPONENT_BYTES",
    "RESERVED_EXTENSION_BYTES",
    "DOUYIN_PROBE_RATIOS",
    "DOUYIN_PROBE_BYTES",
    "DOUYIN_STREAM_CHUNK_BYTES",
    "DOUYIN_PROGRESS_INTERVAL_SECONDS",
    "DOUYIN_LOCK_STATUS_INTERVAL_SECONDS",
    "DOUYIN_DEFAULT_PROBE_HOST",
    "DOUYIN_RATIO_PROBE_HOST",
    "DOUYIN_DEFAULT_ROUTE_LINES",
    "DOUYIN_PROBE_HTTP_TIMEOUT_SECONDS",
    "DOUYIN_PROBE_ATTEMPTS",
    "DOUYIN_PROBE_RETRY_BASE_SECONDS",
    "DOUYIN_TRANSFER_ATTEMPTS",
    "DOUYIN_QUALITY_PROBE_IDLE_TIMEOUT_SECONDS",
    "DOUYIN_TRANSFER_IDLE_TIMEOUT_SECONDS",
    "DOUYIN_TRANSFER_HTTP_TIMEOUT_SECONDS",
    "DOUYIN_MAX_TRANSFER_CANDIDATES",
    "DOUYIN_YTDLP_SOCKET_TIMEOUT_SECONDS",
    "DOUYIN_YTDLP_RETRIES",
    "DOUYIN_FFPROBE_PIPE_TIMEOUT_SECONDS",
    "DOUYIN_FFPROBE_FILE_TIMEOUT_SECONDS",
    "DOUYIN_MAX_PROBE_FILE_BYTES",
    "DOUYIN_PROCESS_POLL_SECONDS",
    "DOUYIN_MEDIA_PROBE_LOCK",
    "DOUYIN_MAX_MEDIA_REDIRECTS",
    "DOUYIN_MAX_DURATION_MS",
    "DOUYIN_DIRECT_MEDIA_DOMAINS",
    "DOUYIN_REGIONAL_MEDIA_DOMAINS",
    "FFPROBE_FALLBACK_PATHS",
    "FFMPEG_FALLBACK_PATHS",
    "KUAISHOU_SAVED_ASSETS_KEY",
    "KUAISHOU_OUTPUT_FOLDER",
    "DOUYIN_MEDIA_HEADERS",
    "OUTPUT_TEMPLATE",
    "AUTH_ERROR_MARKERS",
    "TEMPORARY_ACCESS_MARKERS",
    "COOKIE_LOAD_ERROR_MARKERS",
    "XIAOHONGSHU_COOKIE_BROWSER_ERROR",
    "XIAOHONGSHU_COOKIE_PROFILE_ERROR",
    "COOKIE_FALLBACK_WARNING",
    "COOKIE_ACCESS_MESSAGE",
    "DOUYIN_LIVE_PHOTO_STATIC_FALLBACK_WARNING",
    "DOUYIN_ITEM_EXPANSION_MESSAGE",
    "DOUYIN_EMPTY_DETAIL_MARKERS",
    "_INVALID_FILENAME",
    "_ERROR_URL_RE",
    "_ERROR_SECRET_NAME_PATTERN",
    "_ERROR_SECRET_FIELD_RE",
    "_ERROR_SECRET_TUPLE_RE",
    "_ERROR_COOKIE_CONTAINER_RE",
    "_ERROR_BEARER_RE",
    "_ERROR_HTTP_REASON_RE",
    "_ERROR_IPV4_RE",
    "_ERROR_BRACKETED_IP_RE",
    "_ERROR_HOST_FIELD_RE",
    "_DOUYIN_REDIRECT_ERROR_MARKERS",
    "_DOUYIN_REDIRECT_REASON_RE",
    "_DOUYIN_REDIRECT_HOST_PATTERNS",
    "_DOUYIN_REDIRECT_PORT_PATTERNS",
    "_WINDOWS_RESERVED",
    "FFPROBE_REQUIRED_MESSAGE",
    "FFPROBE_START_MESSAGE",
    "_DouyinProbeRejected",
    "_DouyinRedirectRejected",
    "_DouyinProbeIntegrityChanged",
    "_DouyinProbeResponseChanged",
    "_DouyinNoProgressTimeout",
    "safe_component",
    "platform_output_directory",
    "_safe_douyin_redirect_family",
    "_sanitize_douyin_redirect_diagnostic",
    "safe_external_error_message",
    "_safe_media_id",
    "_normalized_douyin_duration_ms",
    "_filename_title_limit",
    "_SafeFilenamePostProcessor",
    "_item_key",
    "_is_auth_error",
    "_is_temporary_access_error",
    "_is_cookie_load_error",
    "DownloaderConfig",
    "EngineEvent",
    "DiscoveryResult",
    "DownloadOutcome",
    "_DouyinDirectProbeFailure",
    "_DouyinProbeReuseContext",
    "_DouyinNoProgressBudget",
    "_DouyinQualityProbeRun",
    "EventCallback",
    "CancelCallback",
    "_YdlLogger",
    "annotations",
    "contextlib",
    "hashlib",
    "http",
    "ipaddress",
    "json",
    "math",
    "mimetypes",
    "os",
    "re",
    "shutil",
    "stat",
    "subprocess",
    "tempfile",
    "threading",
    "time",
    "unicodedata",
    "dataclass",
    "field",
    "datetime",
    "timedelta",
    "timezone",
    "CookieJar",
    "Path",
    "Any",
    "Callable",
    "Iterable",
    "Iterator",
    "parse_qs",
    "unquote",
    "urlencode",
    "urljoin",
    "urlsplit",
    "urlunsplit",
    "YoutubeDL",
    "requests",
    "urllib3",
    "DouyinIE",
    "Request",
    "RequestsRH",
    "RequestsResponseAdapter",
    "CertificateVerifyError",
    "HTTPError",
    "IncompleteRead",
    "ProxyError",
    "RequestError",
    "SSLError",
    "TransportError",
    "PostProcessor",
    "DownloadCancelled",
    "DownloadError",
    "variadic",
    "discover_douyin_profile",
    "discover_item_metadata_from_profile",
    "is_complete_profile_media_metadata",
    "quality_floor_dimensions",
    "verified_aweme_metadata",
    "fetch_signed_aweme_detail",
    "public_signing_diagnostic_code",
    "AuthenticationRequiredError",
    "DiscoveryError",
    "DouyinMediaRefreshRequiredError",
    "DownloadCancelledError",
    "MediaDownloadError",
    "SiteIssueCode",
    "TemporaryAccessError",
    "classify_site_issue",
    "is_explicit_rate_limit_message",
    "DownloadItem",
    "MediaType",
    "Platform",
    "SourceKind",
    "TransferProgress",
    "discover_kuaishou",
    "is_kuaishou_media_url",
    "kuaishou_source_identity",
    "is_trusted_bilibili_asset_url",
    "identify_url",
    "publish_no_replace",
    "publish_pair_image",
    "publish_unique",
    "RemoteAsset",
    "discover_xhs_profile",
    "_XiaohongshuRedirectRejected",
    "_open_xiaohongshu_response",
    "is_trusted_xiaohongshu_asset_url",
    "parse_xhs_note",
]


MAX_TITLE_BYTES = 180


MAX_MEDIA_ID_BYTES = 64


MAX_FILENAME_COMPONENT_BYTES = 255
# Windows 未启用长路径支持时强制 MAX_PATH=260。为临时工作目录、冲突后缀、
# 扩展名与分隔符预留余量，按 UTF-8 字节估算（对中文比字符数更保守）。
WINDOWS_MAX_PATH = 260
PATH_BUDGET_MARGIN_BYTES = 24


RESERVED_EXTENSION_BYTES = 16


DOUYIN_PROBE_RATIOS = ("default", "4k", "2k", "1080p", "720p")


DOUYIN_PROBE_BYTES = 256 * 1024


DOUYIN_STREAM_CHUNK_BYTES = 64 * 1024


DOUYIN_PROGRESS_INTERVAL_SECONDS = 0.5


DOUYIN_LOCK_STATUS_INTERVAL_SECONDS = 5.0


DOUYIN_DEFAULT_PROBE_HOST = "api-play-hl.amemv.com"


DOUYIN_RATIO_PROBE_HOST = "api-play.amemv.com"


DOUYIN_DEFAULT_ROUTE_LINES = ("0", "1")


DOUYIN_PROBE_HTTP_TIMEOUT_SECONDS = 10.0


DOUYIN_PROBE_ATTEMPTS = 3


DOUYIN_PROBE_RETRY_BASE_SECONDS = 1.0


DOUYIN_TRANSFER_ATTEMPTS = 3


DOUYIN_QUALITY_PROBE_IDLE_TIMEOUT_SECONDS = 120.0


DOUYIN_TRANSFER_IDLE_TIMEOUT_SECONDS = 120.0


DOUYIN_TRANSFER_HTTP_TIMEOUT_SECONDS = 30.0


DOUYIN_MAX_TRANSFER_CANDIDATES = 8


DOUYIN_YTDLP_SOCKET_TIMEOUT_SECONDS = 10


DOUYIN_YTDLP_RETRIES = 2


DOUYIN_FFPROBE_PIPE_TIMEOUT_SECONDS = 3.0


DOUYIN_FFPROBE_FILE_TIMEOUT_SECONDS = 15.0


DOUYIN_MAX_PROBE_FILE_BYTES = 8 * 1024 * 1024 * 1024


DOUYIN_PROCESS_POLL_SECONDS = 0.1


DOUYIN_MEDIA_PROBE_LOCK = threading.Lock()


DOUYIN_MAX_MEDIA_REDIRECTS = 5


DOUYIN_MAX_DURATION_MS = 7 * 24 * 60 * 60 * 1_000


DOUYIN_DIRECT_MEDIA_DOMAINS = (
    "douyin.com",
    "douyinvod.com",
    "amemv.com",
    "zjcdn.com",
)


DOUYIN_REGIONAL_MEDIA_DOMAINS = (
    *DOUYIN_DIRECT_MEDIA_DOMAINS,
    "douyincdn.com",
    "idouyinvod.com",
    "pstatp.com",
)


FFPROBE_FALLBACK_PATHS = (
    "/opt/homebrew/bin/ffprobe",
    "/usr/local/bin/ffprobe",
)


FFMPEG_FALLBACK_PATHS = (
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
)


KUAISHOU_SAVED_ASSETS_KEY = "kuaishou_saved_assets"


KUAISHOU_OUTPUT_FOLDER = "Kuaishou"

# 抖音精选独立落盘一级目录。与 organized 模式下的 manager.core.output_directory
# 保持一致，确保两种输出模式都不会把精选长视频混进主站「抖音」目录。
SELECTED_DOUYIN_OUTPUT_FOLDER = "Selected-Douyin"


DOUYIN_MEDIA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36"
    ),
    "Referer": "https://www.douyin.com/",
    "Accept": "*/*",
}


OUTPUT_TEMPLATE = (
    "%(upload_date>%Y-%m-%d,release_date>%Y-%m-%d|Unknown-Date)s-"
    "%(_filename_title,title|Untitled)s "
    "[%(_filename_media_id,id|unknown-id)s].%(ext)s"
)


AUTH_ERROR_MARKERS = (
    "captcha required",
    "captcha challenge",
    "complete the captcha",
    "solve the captcha",
    "login required",
    "authentication required",
    "confirm you're not a bot",
    "confirm you’re not a bot",
    "verify you are human",
    "fresh cookies",
    "cookies are needed",
    "security verification",
    "验证码",
    "安全验证",
    "登录后",
    "请先登录",
)


TEMPORARY_ACCESS_MARKERS = (
    "访问频繁",
    "请求频繁",
    "too many requests",
    "rate limit",
    "rate-limit",
    "temporarily limited",
    "http error 429",
    "status code 429",
)


COOKIE_LOAD_ERROR_MARKERS = (
    "cookie database",
    "could not copy chrome",
    "could not find chrome cookies",
    "failed to load cookies",
    "failed to decrypt",
    "could not decrypt",
    "cookies-from-browser",
    "keyring",
)


XIAOHONGSHU_COOKIE_BROWSER_ERROR = (
    "The Xiaohongshu task has an unsupported cookie-browser setting. Use Chrome "
    "Cookie or disable browser cookies, then create a new task."
)


XIAOHONGSHU_COOKIE_PROFILE_ERROR = (
    "Xiaohongshu Chrome Cookie access requires a bound Chrome profile. Create the "
    "task again so an authenticated profile can be selected; automatic Chrome "
    "profile scanning was blocked."
)


COOKIE_FALLBACK_WARNING = (
    "Chrome cookies could not be read, so anonymous access was used. "
    "The profile may be incomplete and restricted high-quality formats may be missing."
)


COOKIE_ACCESS_MESSAGE = (
    "Chrome cookies could not be read. Fully quit Chrome and retry, approve any "
    "system cookie-access prompt, or disable Chrome Cookie in settings to continue "
    "explicitly without login and create a new task. Opening a verification page is "
    "not required."
)


DOUYIN_LIVE_PHOTO_STATIC_FALLBACK_WARNING = (
    "Some Douyin image positions reported Live Photo data, but no complete trusted "
    "motion rendition was available. The highest-pixel static images will be saved "
    "for those positions. Create a new task from the original link later to retry "
    "the dynamic versions."
)


DOUYIN_ITEM_EXPANSION_MESSAGE = (
    "Douyin returned an uploader profile instead of the requested video. "
    "The unexpected profile entries were blocked. Retry the original video to "
    "rediscover it; Chrome verification was not requested because no explicit "
    "CAPTCHA or login response was shown."
)


DOUYIN_EMPTY_DETAIL_MARKERS = (
    "aweme detail is empty",
    "empty aweme detail",
    "no aweme detail",
    "unable to extract aweme detail",
    "uploader profile instead of the requested video",
    "returned data for a different video while requesting",
)


_INVALID_FILENAME = re.compile(r"[\x00-\x1f<>:\"/\\|?*]+")


_ERROR_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


_ERROR_SECRET_NAME_PATTERN = (
    r"authorization|proxy-authorization|set-cookie|cookies?|x-bogus|x-gorgon|"
    r"a[_-]?bogus|verifyfp|s_v_web_id|ttwid|odin_tt|sid_guard|sid_tt|"
    r"uid_tt(?:_ss)?|fp|expires|sign|auth|session[_-]?id|"
    r"[a-z0-9_-]*(?:token|signature|sessionid|csrf)[a-z0-9_-]*"
)


_ERROR_SECRET_FIELD_RE = re.compile(
    rf"(?<![\w-])['\"]?({_ERROR_SECRET_NAME_PATTERN})['\"]?\s*[:=]\s*"
    r"(?:\[redacted\]|'[^']*'|\"[^\"]*\"|[^\r\n,;}\]]+)",
    re.IGNORECASE,
)


_ERROR_SECRET_TUPLE_RE = re.compile(
    rf"(['\"]?)({_ERROR_SECRET_NAME_PATTERN})\1\s*,\s*(['\"])[^'\"]*\3",
    re.IGNORECASE,
)


_ERROR_COOKIE_CONTAINER_RE = re.compile(
    r"(?<![\w-])['\"]?(set-cookie|cookies?)['\"]?\s*[:=]\s*[^\r\n]*",
    re.IGNORECASE,
)


_ERROR_BEARER_RE = re.compile(
    r"\bbearer\s+(?:\[redacted\]|[^\s,;'\"}\]]+)",
    re.IGNORECASE,
)


_ERROR_HTTP_REASON_RE = re.compile(
    r"\b(HTTP Error\s+\d{3})(?::[^\r\n]*)",
    re.IGNORECASE,
)


_ERROR_IPV4_RE = re.compile(
    r"(?<![a-z0-9])(?:\d{1,3}\.){3}\d{1,3}(?![a-z0-9])",
    re.IGNORECASE,
)


_ERROR_BRACKETED_IP_RE = re.compile(r"\[[0-9a-f:.%]+\]", re.IGNORECASE)


_ERROR_HOST_FIELD_RE = re.compile(
    r"(?<![\w-])host\s*=\s*(?:\[[^\]\r\n]*\]|'[^']*'|\"[^\"]*\"|[^,\s)\]}]+)",
    re.IGNORECASE,
)


_DOUYIN_REDIRECT_ERROR_MARKERS = (
    "media endpoint redirected to an unrecognized Douyin CDN host",
    "Douyin media redirect could not be trusted",
)


_DOUYIN_REDIRECT_REASON_RE = re.compile(
    r"(?:Redirect reason:\s*|reason:\s*)([a-z0-9-]+)",
    re.IGNORECASE,
)


_DOUYIN_REDIRECT_HOST_PATTERNS = (
    re.compile(r"(\(host:\s*)([^;)\r\n]+)", re.IGNORECASE),
    re.compile(r"(Redirect host:\s*)([^;\r\n]+)", re.IGNORECASE),
)


_DOUYIN_REDIRECT_PORT_PATTERNS = (
    re.compile(r"(\bport:\s*)([^;)\r\n]+)", re.IGNORECASE),
    re.compile(r"(Redirect port:\s*)([^;\r\n]+)", re.IGNORECASE),
)


_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


FFPROBE_REQUIRED_MESSAGE = (
    "FFprobe was not found. Install FFmpeg with FFprobe, make sure its bin "
    "directory is on PATH, fully stop this app, and restart it. On macOS with "
    "Homebrew, run `brew install ffmpeg` and then `./start.command`."
)


FFPROBE_START_MESSAGE = (
    "FFprobe was found but could not be started. Reinstall FFmpeg with FFprobe, "
    "fully stop this app, and restart it."
)


class _DouyinProbeRejected(RuntimeError):
    pass


class _DouyinRedirectRejected(_DouyinProbeRejected):
    def __init__(
        self,
        message: str,
        *,
        redirect_host: str | None = None,
        redirect_host_fingerprint: str | None = None,
        redirect_port: int | None = None,
        redirect_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.redirect_host = redirect_host
        self.redirect_host_fingerprint = redirect_host_fingerprint
        self.redirect_port = redirect_port
        self.redirect_reason = redirect_reason


class _DouyinProbeIntegrityChanged(_DouyinProbeRejected):
    pass


class _DouyinProbeResponseChanged(_DouyinProbeRejected):
    """The trusted endpoint returned a non-media response that may be temporary."""


class _DouyinNoProgressTimeout(TemporaryAccessError):
    pass


def safe_component(
    value: str | None, fallback: str = "Unknown Author", limit: int = 120
) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = _INVALID_FILENAME.sub("_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    encoded = value.encode("utf-8")
    if len(encoded) > limit:
        value = encoded[:limit].decode("utf-8", errors="ignore").rstrip(" .")
    value = value or fallback
    if value.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        value = f"_{value}"
    return value


def platform_output_directory(
    platform: Platform, output_root: str | Path, author: str | None
) -> Path:
    """Compute the real per-platform, per-author output directory.

    Kuaishou and Selected Douyin each keep their own top-level folder so their
    output never mixes with another platform's files; every other platform keeps
    its original layout. The author segment is sanitized so a crafted author name
    cannot escape the folder.
    """
    root = Path(output_root).expanduser()
    author_folder = safe_component(author, fallback=f"{platform.value}-author")
    if platform == Platform.KUAISHOU:
        base = root / KUAISHOU_OUTPUT_FOLDER
    elif platform == Platform.SELECTED_DOUYIN:
        base = root / SELECTED_DOUYIN_OUTPUT_FOLDER
    else:
        base = root
    return base / author_folder


def _safe_douyin_redirect_family(hostname: str) -> str | None:
    normalized = hostname.strip().lower().rstrip(".")
    if not normalized or len(normalized) > 253:
        return None
    labels = normalized.split(".")
    if any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in labels
    ):
        return None
    return next(
        (
            domain
            for domain in sorted(
                DOUYIN_REGIONAL_MEDIA_DOMAINS,
                key=len,
                reverse=True,
            )
            if normalized == domain or normalized.endswith(f".{domain}")
        ),
        None,
    )


def _sanitize_douyin_redirect_diagnostic(message: str) -> str:
    if not any(marker in message for marker in _DOUYIN_REDIRECT_ERROR_MARKERS):
        return message
    reason_match = _DOUYIN_REDIRECT_REASON_RE.search(message)
    reason = reason_match.group(1).lower() if reason_match else ""

    def safe_host(match: re.Match[str]) -> str:
        family = (
            _safe_douyin_redirect_family(match.group(2))
            if reason in {"unverified-source-binding", "nonstandard-port"}
            else None
        )
        return f"{match.group(1)}{family or 'unavailable'}"

    def safe_port(match: re.Match[str]) -> str:
        raw_port = match.group(2).strip()
        try:
            port = int(raw_port) if len(raw_port) <= 5 and raw_port.isdigit() else 0
        except ValueError:
            port = 0
        return f"{match.group(1)}{port if 1 <= port <= 65_535 else 'unavailable'}"

    for pattern in _DOUYIN_REDIRECT_HOST_PATTERNS:
        message = pattern.sub(safe_host, message)
    for pattern in _DOUYIN_REDIRECT_PORT_PATTERNS:
        message = pattern.sub(safe_port, message)
    return message


def safe_external_error_message(value: BaseException | str) -> str:
    message = _sanitize_douyin_redirect_diagnostic(str(value))
    message = _ERROR_URL_RE.sub("[redacted URL]", message)
    message = _ERROR_HTTP_REASON_RE.sub(r"\1", message)
    message = _ERROR_HOST_FIELD_RE.sub("host=[redacted]", message)

    def redact_ip_literal(match: re.Match[str]) -> str:
        candidate = match.group(0).strip("[]").split("%", 1)[0]
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            return match.group(0)
        return "[redacted IP]"

    message = _ERROR_IPV4_RE.sub(redact_ip_literal, message)
    message = _ERROR_BRACKETED_IP_RE.sub(redact_ip_literal, message)
    message = _ERROR_SECRET_TUPLE_RE.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}{match.group(1)}, "
            f"{match.group(3)}[redacted]{match.group(3)}"
        ),
        message,
    )
    message = _ERROR_SECRET_FIELD_RE.sub(
        lambda match: f"{match.group(1)}=[redacted]",
        message,
    )
    message = _ERROR_COOKIE_CONTAINER_RE.sub(
        lambda match: f"{match.group(1)}=[redacted]",
        message,
    )
    message = _ERROR_BEARER_RE.sub("Bearer [redacted]", message)
    return message[:4_000]


def _safe_media_id(value: str | None, fallback: str = "unknown-id") -> str:
    original = str(value or fallback)
    normalized = unicodedata.normalize("NFKC", original)
    sanitized = safe_component(
        normalized,
        fallback=fallback,
        limit=max(MAX_MEDIA_ID_BYTES, len(normalized.encode("utf-8"))),
    )
    if sanitized == original and len(sanitized.encode("utf-8")) <= MAX_MEDIA_ID_BYTES:
        return sanitized

    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:12]
    hash_suffix = f"~{digest}"
    prefix_limit = MAX_MEDIA_ID_BYTES - len(hash_suffix.encode("ascii"))
    prefix = safe_component(sanitized, fallback="id", limit=prefix_limit)
    return f"{prefix}{hash_suffix}"


def _normalized_douyin_duration_ms(value: Any) -> int | None:
    if type(value) is int and 0 < value <= DOUYIN_MAX_DURATION_MS:
        return value
    return None


def _filename_title_limit(
    requested_limit: int, media_id: str, parent_path_length: int = 0
) -> int:
    date_prefix_bytes = len("Unknown-Date-".encode("ascii"))
    separators_bytes = len(" [].".encode("ascii"))
    fixed_bytes = (
        date_prefix_bytes
        + separators_bytes
        + RESERVED_EXTENSION_BYTES
        + len(media_id.encode("utf-8"))
    )
    available = MAX_FILENAME_COMPONENT_BYTES - fixed_bytes
    if parent_path_length > 0:
        # 完整路径 = 父目录 + 分隔符 + 文件名。深层目录（平台/作者/标题）
        # 下必须按剩余预算缩短标题，否则 Windows 上会因 MAX_PATH 直接失败。
        path_available = (
            WINDOWS_MAX_PATH - parent_path_length - 1 - PATH_BUDGET_MARGIN_BYTES - fixed_bytes
        )
        available = min(available, max(1, path_available))
    return max(1, min(int(requested_limit), MAX_TITLE_BYTES, available))


class _SafeFilenamePostProcessor(PostProcessor):
    def __init__(
        self,
        downloader: YoutubeDL,
        *,
        title_byte_limit: int,
        fallback_media_id: str,
        parent_path_length: int = 0,
    ) -> None:
        super().__init__(downloader)
        self.title_byte_limit = title_byte_limit
        self.fallback_media_id = fallback_media_id
        self.parent_path_length = parent_path_length

    def run(self, information: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        media_id = _safe_media_id(
            information.get("id"), fallback=self.fallback_media_id
        )
        title_limit = _filename_title_limit(
            self.title_byte_limit, media_id, self.parent_path_length
        )
        raw_title = information.get("title") or media_id
        information["_filename_title"] = safe_component(
            str(raw_title), fallback="Untitled", limit=title_limit
        )
        information["_filename_media_id"] = media_id
        return [], information


def _item_key(platform: Platform, media_id: str | None, url: str, index: int) -> str:
    identity = media_id or f"{url}\0{index}"
    source = f"{platform.value}\0{identity}".encode("utf-8")
    return hashlib.sha256(source).hexdigest()[:20]


def _is_auth_error(message: str) -> bool:
    lowered = message.lower()
    if any(marker in lowered for marker in AUTH_ERROR_MARKERS):
        return True
    return (
        re.search(
            r"(?<![a-z])(?:sign|log) in(?:\s+required|\s+to\b|[.!,:;]|$)|"
            r"\blog into an account\b|"
            r"\b(?:need|must) (?:to )?be logged in\b|"
            r"\b(?:you (?:are|re)|you're) not logged in\b|"
            r"\b(?:login|session) (?:has )?expired\b",
            lowered,
        )
        is not None
    )


def _is_temporary_access_error(message: str) -> bool:
    lowered = message.lower()
    return is_explicit_rate_limit_message(lowered) or any(
        marker in lowered for marker in TEMPORARY_ACCESS_MARKERS
    )


def _is_cookie_load_error(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in COOKIE_LOAD_ERROR_MARKERS)


@dataclass(slots=True)
class DownloaderConfig:
    cookie_browser: str | None = "chrome"
    cookie_profile: str | None = None
    # AI 搜索用独立的持久化浏览器 Profile 保存登录态；下载时复用它，避免
    # 「搜得到却下不下来」。仅在没有显式 Chrome Cookie 时作为补充来源。
    search_profile_dir: str | None = None
    allow_cookie_fallback: bool = False
    socket_timeout_seconds: int = 30
    retries: int = 10
    fragment_retries: int = 10
    concurrent_fragments: int = 4
    filename_limit: int = 180
    temporary_root: str | None = None
    anonymous_douyin: bool = False
    hls_total_timeout_seconds: float = 6 * 60 * 60
    hls_idle_timeout_seconds: float = 120


@dataclass(slots=True)
class EngineEvent:
    event: str
    progress: TransferProgress | None = None
    message: str | None = None
    title: str | None = None
    upload_date: str | None = None
    author: str | None = None
    media_type: MediaType | None = None
    selected_format: str | None = None
    resolution: str | None = None
    output_paths: list[str] = field(default_factory=list)
    cookie_fallback_used: bool = False
    # Verified per-image completion records for album downloads. They carry no
    # media URL, so they are safe to persist across retries and restarts.
    asset_records: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class DiscoveryResult:
    author: str
    items: list[DownloadItem]
    cookie_fallback_used: bool = False
    warning: str | None = None
    discovery_complete: bool = True


@dataclass(slots=True)
class DownloadOutcome:
    output_paths: list[str]
    title: str | None = None
    upload_date: str | None = None
    author: str | None = None
    media_type: MediaType | None = None
    selected_format: str | None = None
    resolution: str | None = None
    cookie_fallback_used: bool = False


@dataclass(slots=True)
class _DouyinDirectProbeFailure:
    rendition: dict[str, Any]
    label: str
    reason: str
    transient: bool
    all_sources_verified_lower: bool = False


@dataclass(slots=True)
class _DouyinProbeReuseContext:
    directory: Path
    files: dict[tuple[Any, ...], list[Path]] = field(default_factory=dict)


@dataclass(slots=True)
class _DouyinNoProgressBudget:
    idle_timeout_seconds: float
    phase: str
    deadline: float = field(init=False)

    def __post_init__(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self.deadline = time.monotonic() + self.idle_timeout_seconds

    def extend(self, seconds: float) -> None:
        """Push the deadline out by a locally-waited interval.

        Used for time spent queuing on a shared local resource (e.g. the Douyin
        media-probe lock), which is not "no media progress" and must not eat the
        idle budget. Unlike refresh(), this preserves the budget shared across
        candidates and retries.
        """

        if seconds > 0:
            self.deadline += seconds

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _DouyinNoProgressTimeout(
                f"Douyin {self.phase} made no media progress for "
                f"{int(self.idle_timeout_seconds)} seconds. The task was paused; "
                "completed files were preserved."
            )
        return remaining

    def request_timeout(self, maximum: float) -> float:
        return min(maximum, self.remaining())


@dataclass(slots=True)
class _DouyinQualityProbeRun:
    budget: _DouyinNoProgressBudget
    candidate_total: int = 0
    candidate_index: int = 0


EventCallback = Callable[[EngineEvent], None]


CancelCallback = Callable[[], bool]


class _YdlLogger:
    def __init__(self, callback: EventCallback | None = None) -> None:
        self.callback = callback
        self.errors: list[str] = []

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        if self.callback:
            self.callback(EngineEvent(event="warning", message=message))

    def error(self, message: str) -> None:
        self.errors.append(message)
