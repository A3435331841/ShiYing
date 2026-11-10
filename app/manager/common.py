"""Shared constants and helpers for job orchestration."""
from __future__ import annotations
import re
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from ..browser import (
    COOKIE_DIAGNOSTIC_CODES,
    chrome_profile_has_cookies,
    select_chrome_profile_with_cookies,
)
from ..downloader import (
    DOUYIN_ITEM_EXPANSION_MESSAGE,
    DOUYIN_LIVE_PHOTO_STATIC_FALLBACK_WARNING,
    KUAISHOU_SAVED_ASSETS_KEY,
    DiscoveryResult,
    DownloaderConfig,
    EngineEvent,
    MediaDownloader,
    XIAOHONGSHU_COOKIE_BROWSER_ERROR,
    platform_output_directory,
    safe_component,
    safe_external_error_message,
)
from ..douyin import (
    discover_item_metadata_from_profile,
    is_complete_profile_media_metadata,
    quality_floor_dimensions,
)
from ..douyin_signing import new_signed_discovery_budget, public_signing_diagnostic_code
from ..errors import (
    AuthenticationRequiredError,
    DiscoveryError,
    DouyinMediaRefreshRequiredError,
    DownloadCancelledError,
    MediaDownloadError,
    SiteIssueCode,
    TemporaryAccessError,
    classify_site_issue,
)
from ..kuaishou import source_identity as kuaishou_source_identity
from ..models import (
    ACTIVE_JOB_STATUSES,
    RETRYABLE_ITEM_STATUSES,
    DownloadItem,
    DownloadJob,
    ItemStatus,
    JobStatus,
    ManagerEvent,
    MediaType,
    Platform,
    SourceKind,
    TransferProgress,
    utc_now,
)
from ..platforms import identify_url
from ..storage import JobNotFoundError, JsonJobStore
from ..xiaohongshu import (
    xiaohongshu_note_id,
    xiaohongshu_profile_id,
)

__all__ = [
    "JobBusyError",
    "ItemNotFoundError",
    "ItemNotRetryableError",
    "Listener",
    "NON_RETRYABLE_SITE_ISSUES",
    "QUEUE_PAUSE_SITE_ISSUES",
    "_KUAISHOU_SAVED_ASSET_FIELDS",
    "_KUAISHOU_SAVED_ASSET_KINDS",
    "_KUAISHOU_SECURITY_TRANSFER_ERRORS",
    "_public_kuaishou_saved_asset",
    "_kuaishou_cookie_diagnostic",
    "_recover_cookie_diagnostic_code",
    "_is_kuaishou_security_transfer_error",
    "LEGACY_DOUYIN_RESULT_ERROR",
    "DOUYIN_ITEM_SOURCE_ERROR",
    "DOUYIN_ITEM_MIGRATION_MESSAGE",
    "DOUYIN_UNVERIFIABLE_QUEUE_ERROR",
    "DOUYIN_PROFILE_REDISCOVERY_MESSAGE",
    "DOUYIN_PROFILE_REDISCOVERY_ITEM_MARKER",
    "DOUYIN_PROFILE_REFRESH_REQUIRED_MARKER",
    "DOUYIN_PROFILE_REMOVED_ITEM_MARKER",
    "DOUYIN_PROFILE_RETIRED_ITEM_MESSAGE",
    "DOUYIN_PROFILE_REMOVED_PARTIAL_ITEM_MESSAGE",
    "DOUYIN_PROFILE_METADATA_ERROR",
    "XIAOHONGSHU_ITEM_BINDING_ERROR",
    "XIAOHONGSHU_CHROME_PROFILE_ERROR",
    "XIAOHONGSHU_AUTH_COOKIE_NAMES",
    "XIAOHONGSHU_BINDING_REDISCOVERY_MARKER",
    "XIAOHONGSHU_MEDIA_VERIFICATION_VERSION",
    "XIAOHONGSHU_MEDIA_VERIFICATION_MARKER",
    "XIAOHONGSHU_PROFILE_MEDIA_REVALIDATION_MARKER",
    "XIAOHONGSHU_PROFILE_MEDIA_REVALIDATION_MESSAGE",
    "LEGACY_DOUYIN_GENERIC_SIGNING_MARKER",
    "LEGACY_DOUYIN_GENERIC_SIGNING_MESSAGE",
    "LEGACY_DOUYIN_MEDIA_REDIRECT_MARKER",
    "LEGACY_DOUYIN_MEDIA_REDIRECT_MARKERS",
    "LEGACY_DOUYIN_MEDIA_REDIRECT_MESSAGE",
    "LEGACY_DOUYIN_SHORT_REDIRECT_MESSAGE",
    "_LEGACY_DOUYIN_MARKDOWN_ITEM_RE",
    "annotations",
    "re",
    "threading",
    "time",
    "uuid",
    "Callable",
    "Future",
    "ThreadPoolExecutor",
    "replace",
    "datetime",
    "Path",
    "Any",
    "parse_qs",
    "urlsplit",
    "COOKIE_DIAGNOSTIC_CODES",
    "chrome_profile_has_cookies",
    "select_chrome_profile_with_cookies",
    "DOUYIN_ITEM_EXPANSION_MESSAGE",
    "DOUYIN_LIVE_PHOTO_STATIC_FALLBACK_WARNING",
    "KUAISHOU_SAVED_ASSETS_KEY",
    "DiscoveryResult",
    "DownloaderConfig",
    "EngineEvent",
    "MediaDownloader",
    "XIAOHONGSHU_COOKIE_BROWSER_ERROR",
    "platform_output_directory",
    "safe_component",
    "safe_external_error_message",
    "discover_item_metadata_from_profile",
    "is_complete_profile_media_metadata",
    "quality_floor_dimensions",
    "new_signed_discovery_budget",
    "public_signing_diagnostic_code",
    "AuthenticationRequiredError",
    "DiscoveryError",
    "DouyinMediaRefreshRequiredError",
    "DownloadCancelledError",
    "MediaDownloadError",
    "SiteIssueCode",
    "TemporaryAccessError",
    "classify_site_issue",
    "kuaishou_source_identity",
    "ACTIVE_JOB_STATUSES",
    "RETRYABLE_ITEM_STATUSES",
    "DownloadItem",
    "DownloadJob",
    "ItemStatus",
    "JobStatus",
    "ManagerEvent",
    "MediaType",
    "Platform",
    "SourceKind",
    "TransferProgress",
    "utc_now",
    "identify_url",
    "JobNotFoundError",
    "JsonJobStore",
    "xiaohongshu_note_id",
    "xiaohongshu_profile_id",
]


class JobBusyError(RuntimeError):
    pass


class ItemNotFoundError(KeyError):
    pass


class ItemNotRetryableError(RuntimeError):
    pass


Listener = Callable[[ManagerEvent, DownloadJob], None]


NON_RETRYABLE_SITE_ISSUES = frozenset(
    {
        SiteIssueCode.CONTENT_UNAVAILABLE,
        SiteIssueCode.REGION_RESTRICTED,
    }
)


QUEUE_PAUSE_SITE_ISSUES = frozenset(
    {
        SiteIssueCode.RATE_LIMITED,
        SiteIssueCode.REQUEST_REJECTED,
        SiteIssueCode.MEDIA_LINK_EXPIRED,
        SiteIssueCode.SITE_UNAVAILABLE,
        SiteIssueCode.NETWORK_ERROR,
        SiteIssueCode.COOKIE_UNAVAILABLE,
        SiteIssueCode.SECURITY_BLOCKED,
        SiteIssueCode.LOCAL_CONFIGURATION,
    }
)


_KUAISHOU_SAVED_ASSET_FIELDS = {
    "media_id": str,
    "index": int,
    "media_kind": str,
    "path": str,
    "width": int,
    "height": int,
    "size": int,
    "format_id": str,
    "local_sha256": str,
    "source_sha256": str,
}


_KUAISHOU_SAVED_ASSET_KINDS = frozenset({"video", "image"})


_KUAISHOU_SECURITY_TRANSFER_ERRORS = frozenset(
    {
        "Untrusted Kuaishou media URL was blocked",
        "Kuaishou media redirect was blocked before requesting an untrusted target",
        "Kuaishou media request redirected to an untrusted URL",
    }
)


def _public_kuaishou_saved_asset(record: object) -> dict[str, object] | None:
    """Persist only local file facts for a verified Kuaishou asset."""
    if not isinstance(record, dict):
        return None
    cleaned: dict[str, object] = {}
    for name, kind in _KUAISHOU_SAVED_ASSET_FIELDS.items():
        value = record.get(name)
        if value is None or (kind is int and isinstance(value, bool)):
            continue
        if not isinstance(value, kind):
            continue
        if name in {"local_sha256", "source_sha256"} and not re.fullmatch(
            r"[0-9a-f]{64}", value
        ):
            continue
        cleaned[name] = value
    if not isinstance(cleaned.get("media_id"), str) or not cleaned["media_id"]:
        return None
    if not isinstance(cleaned.get("index"), int) or cleaned["index"] <= 0:
        return None
    if cleaned.get("media_kind") not in _KUAISHOU_SAVED_ASSET_KINDS:
        return None
    path = cleaned.get("path")
    if not isinstance(path, str) or not Path(path).is_absolute() or "://" in path:
        return None
    return cleaned


def _kuaishou_cookie_diagnostic(cause: BaseException | None) -> str | None:
    """Read only fixed diagnostic categories from the exception chain."""
    current = cause
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        code = getattr(current, "diagnostic_code", None)
        if isinstance(code, str) and code in COOKIE_DIAGNOSTIC_CODES:
            return code
        current = current.__cause__ or current.__context__
    return None


def _recover_cookie_diagnostic_code(message: str | None) -> str | None:
    match = re.search(r"\bDiagnostic:\s*([a-z0-9_]+)\b", message or "", re.I)
    if not match:
        return None
    code = match.group(1).lower()
    return code if code in COOKIE_DIAGNOSTIC_CODES else None


def _is_kuaishou_security_transfer_error(
    cause: BaseException | None, message: str
) -> bool:
    if not isinstance(cause, MediaDownloadError):
        return False
    if message in _KUAISHOU_SECURITY_TRANSFER_ERRORS:
        return True
    match = re.fullmatch(
        r"All highest-available media URLs failed: Candidate [1-9][0-9]*: (.+)",
        message,
    )
    return bool(match and match.group(1) in _KUAISHOU_SECURITY_TRANSFER_ERRORS)


LEGACY_DOUYIN_RESULT_ERROR = (
    "This legacy Douyin profile result must be manually reviewed; "
    "create a new task before downloading again."
)


DOUYIN_ITEM_SOURCE_ERROR = (
    "This legacy Douyin item task no longer has a verifiable original video URL. "
    "Its queued entries will not be retried; create a new task from the original "
    "video link."
)


DOUYIN_ITEM_MIGRATION_MESSAGE = (
    "This legacy Douyin item task must be rediscovered from its original video "
    "link before downloading. Existing files were preserved."
)


DOUYIN_UNVERIFIABLE_QUEUE_ERROR = (
    "This legacy Douyin task contains an unverified numeric queue and no longer "
    "has enough source metadata to continue safely. Create a new task from the "
    "original profile or video link. Existing files were preserved."
)


DOUYIN_PROFILE_REDISCOVERY_MESSAGE = (
    "This Douyin profile task contains legacy entries without complete verified "
    "author and media metadata. Unsaved placeholders were removed; "
    "retry the original profile to discover it again. Existing files were preserved."
)


DOUYIN_PROFILE_REDISCOVERY_ITEM_MARKER = "_douyin_profile_rediscovery_pending"


DOUYIN_PROFILE_REFRESH_REQUIRED_MARKER = "_douyin_profile_refresh_required"


DOUYIN_PROFILE_REMOVED_ITEM_MARKER = "_douyin_profile_removed_after_refresh"


DOUYIN_PROFILE_RETIRED_ITEM_MESSAGE = (
    "This preserved legacy entry was not returned by the refreshed Douyin profile. "
    "It was skipped and will not be downloaded again; existing files were preserved."
)


DOUYIN_PROFILE_REMOVED_PARTIAL_ITEM_MESSAGE = (
    "This partially downloaded Douyin profile entry was not returned by a complete "
    "verified profile refresh. It is no longer available for automatic retry; "
    "existing files were preserved."
)


DOUYIN_PROFILE_METADATA_ERROR = (
    "Douyin profile discovery temporarily returned incomplete verified media "
    "metadata. Retry after a short wait. No numeric placeholders were queued and "
    "Chrome verification is not required for this temporary response."
)


XIAOHONGSHU_ITEM_BINDING_ERROR = (
    "Xiaohongshu item identity or profile membership could not be verified. "
    "The untrusted or cross-wired entry was blocked before download; retry the "
    "original link to rediscover it."
)


XIAOHONGSHU_CHROME_PROFILE_ERROR = (
    "No current authenticated Xiaohongshu session was found in the selected "
    "Chrome profile. Sign in to Xiaohongshu in a regular Chrome profile and "
    "retry. This is a Chrome login/profile setting issue; CAPTCHA verification "
    "is not required."
)


XIAOHONGSHU_AUTH_COOKIE_NAMES = ("web_session", "id_token")


XIAOHONGSHU_BINDING_REDISCOVERY_MARKER = "_xiaohongshu_binding_rediscovery_pending"


XIAOHONGSHU_MEDIA_VERIFICATION_VERSION = 1


XIAOHONGSHU_MEDIA_VERIFICATION_MARKER = "_xiaohongshu_media_verification_version"


XIAOHONGSHU_PROFILE_MEDIA_REVALIDATION_MARKER = (
    "_xiaohongshu_profile_media_revalidation_pending"
)


XIAOHONGSHU_PROFILE_MEDIA_REVALIDATION_MESSAGE = (
    "This Xiaohongshu profile item was completed by an older version without "
    "verified media-type metadata. Retry the original profile to rediscover and "
    "verify whether the note contains images or video. Existing files were preserved."
)


LEGACY_DOUYIN_GENERIC_SIGNING_MARKER = (
    "Douyin could not create a verified signed request"
)


LEGACY_DOUYIN_GENERIC_SIGNING_MESSAGE = (
    "This task was paused by an older version after a generic Douyin signing "
    "failure. Retry the original link; Chrome verification is not required unless "
    "Douyin explicitly shows a CAPTCHA or login page."
)


LEGACY_DOUYIN_MEDIA_REDIRECT_MARKER = (
    "Douyin media request redirected to an untrusted URL"
)


LEGACY_DOUYIN_MEDIA_REDIRECT_MARKERS = (
    LEGACY_DOUYIN_MEDIA_REDIRECT_MARKER,
    "media endpoint redirected to an unrecognized Douyin CDN host",
    "Douyin media redirect could not be trusted",
)


LEGACY_DOUYIN_MEDIA_REDIRECT_MESSAGE = (
    "This task contains a Douyin media redirect failure recorded by an older "
    "version, which did not preserve the redirect hostname. Retry the original "
    "link to rediscover and verify the media with the current version. Existing "
    "files were preserved."
)


LEGACY_DOUYIN_SHORT_REDIRECT_MESSAGE = (
    "This saved Douyin short-link task contains a media redirect failure from an "
    "older version, but its original resolved target was not preserved. Create a "
    "new task from the original short link so the current version can bind and "
    "verify its target. Existing files were preserved."
)


_LEGACY_DOUYIN_MARKDOWN_ITEM_RE = re.compile(
    r"(https://www\.douyin\.com/video/(\d+))\]\("
    r"(https://www\.douyin\.com/video/(\d+))"
)
