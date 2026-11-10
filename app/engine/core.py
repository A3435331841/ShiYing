"""Auto-split from the original downloader module."""
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

from .common import *  # noqa: F401,F403


from .discovery import DiscoveryMixin
from .douyin_media import DouyinMediaMixin
from .douyin_probe import DouyinProbeMixin
from .naming import NamingMixin
from .platform_items import PlatformItemsMixin
from .transfer import TransferMixin


class MediaDownloader(
    NamingMixin,
    DiscoveryMixin,
    PlatformItemsMixin,
    TransferMixin,
    DouyinProbeMixin,
    DouyinMediaMixin,
):

    def __init__(
        self,
        config: DownloaderConfig | None = None,
        *,
        discovery_callback: EventCallback | None = None,
    ) -> None:
        self.config = config or DownloaderConfig()
        self.discovery_callback = discovery_callback
        self._douyin_probe_context = threading.local()
    def _xiaohongshu_browser_cookies_enabled(self) -> bool:
        if self.config.cookie_browser is None:
            return False
        if self.config.cookie_browser != "chrome":
            raise TemporaryAccessError(XIAOHONGSHU_COOKIE_BROWSER_ERROR)
        if not str(self.config.cookie_profile or "").strip():
            raise TemporaryAccessError(XIAOHONGSHU_COOKIE_PROFILE_ERROR)
        return True
    def download_item(
        self,
        item: DownloadItem,
        platform: Platform,
        output_dir: str | Path,
        *,
        callback: EventCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> DownloadOutcome:
        should_cancel = should_cancel or (lambda: False)
        output_path = Path(output_dir).expanduser().resolve()
        output_path.mkdir(parents=True, exist_ok=True)
        if platform == Platform.KUAISHOU:
            return self._download_kuaishou_item(
                item, output_path, callback=callback, should_cancel=should_cancel,
            )
        if platform == Platform.XIAOHONGSHU:
            return self._download_xhs_item(
                item,
                output_path,
                callback=callback,
                should_cancel=should_cancel,
            )
        if platform == Platform.DOUYIN:
            with self._douyin_probe_reuse_scope(output_path):
                if (
                    (
                        isinstance(item.metadata.get("douyin_profile_media"), dict)
                        and item.metadata["douyin_profile_media"].get("media_kind")
                        == "image"
                    )
                    or (
                        isinstance(item.metadata.get("douyin_item_media"), dict)
                        and item.metadata["douyin_item_media"].get("media_kind")
                        == "image"
                    )
                    or (
                        item.media_type == MediaType.IMAGE
                        and "profile_url" in item.metadata
                    )
                ):
                    return self._download_douyin_profile_image(
                        item,
                        output_path,
                        callback=callback,
                        should_cancel=should_cancel,
                    )
                return self._download_with_ytdlp(
                    item,
                    output_path,
                    platform=platform,
                    callback=callback,
                    should_cancel=should_cancel,
                )
        if platform == Platform.WECHAT:
            return self._download_wechat_item(
                item,
                output_path,
                callback=callback,
                should_cancel=should_cancel,
            )
        if platform == Platform.BILIBILI and re.search(r"/(?:read/cv|opus/)", item.source_url):
            return self._download_bilibili_opus(
                item,
                output_path,
                callback=callback,
                should_cancel=should_cancel,
            )
        return self._download_with_ytdlp(
            item,
            output_path,
            platform=platform,
            callback=callback,
            should_cancel=should_cancel,
        )
    def _cookie_options(self, use_cookies: bool) -> dict[str, Any]:
        if not use_cookies or not self.config.cookie_browser:
            return {}
        value: tuple[str, ...]
        if self.config.cookie_profile:
            value = (self.config.cookie_browser, self.config.cookie_profile)
        else:
            value = (self.config.cookie_browser,)
        return {"cookiesfrombrowser": value}
    def _base_options(self, use_cookies: bool = True) -> dict[str, Any]:
        return {
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": self.config.socket_timeout_seconds,
            "retries": self.config.retries,
            "fragment_retries": self.config.fragment_retries,
            "extractor_retries": 3,
            "file_access_retries": 3,
            "js_runtimes": {"deno": {}},
            **self._cookie_options(use_cookies),
        }
    def _run_with_cookie_fallback(
        self,
        operation: Callable[[bool], Any],
        *,
        url: str,
    ) -> tuple[Any, bool]:
        use_cookies = bool(self.config.cookie_browser)
        try:
            return operation(use_cookies), False
        except DownloadCancelled:
            raise DownloadCancelledError("Task cancelled")
        except DownloadError as exc:
            message = str(exc)
            if use_cookies and _is_cookie_load_error(message):
                if self.config.allow_cookie_fallback:
                    try:
                        return operation(False), True
                    except DownloadCancelled as cancelled:
                        raise DownloadCancelledError("Task cancelled") from cancelled
                    except DownloadError as fallback_error:
                        self._raise_download_error(fallback_error, url)
                raise TemporaryAccessError(COOKIE_ACCESS_MESSAGE) from exc
            self._raise_download_error(exc, url)
        raise AssertionError("Unreachable")
    @staticmethod
    def _raise_download_error(exc: BaseException, url: str) -> None:
        message = str(exc)
        normalized_message = message.lower()
        if "uploader profile instead of the requested video" in normalized_message:
            raise MediaDownloadError(
                DOUYIN_ITEM_EXPANSION_MESSAGE,
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            ) from exc
        if "returned data for a different video while requesting" in normalized_message:
            raise MediaDownloadError(
                f"{safe_external_error_message(message)}. The mismatched response "
                "was blocked; Chrome verification was not requested.",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            ) from exc
        if _is_auth_error(message):
            issue_code = (
                SiteIssueCode.VERIFICATION_REQUIRED
                if any(
                    marker in normalized_message
                    for marker in (
                        "captcha required",
                        "captcha challenge",
                        "complete the captcha",
                        "solve the captcha",
                        "confirm you're not a bot",
                        "confirm you’re not a bot",
                        "verify you are human",
                        "security verification",
                        "验证码",
                        "安全验证",
                    )
                )
                else SiteIssueCode.LOGIN_REQUIRED
            )
            if issue_code == SiteIssueCode.VERIFICATION_REQUIRED:
                auth_message = (
                    "The site displayed an explicit CAPTCHA or verification challenge. "
                    "Complete the visible challenge in Chrome and retry."
                )
            else:
                issue_code = SiteIssueCode.LOGIN_REQUIRED
                auth_message = (
                    "The site requires a current Chrome login session or fresh browser "
                    "cookies. Log in to the original task URL in Chrome and retry. No "
                    "CAPTCHA was detected."
                )
            raise AuthenticationRequiredError(
                auth_message,
                verification_url=url,
                issue_code=issue_code,
            ) from exc
        if _is_temporary_access_error(message):
            raise TemporaryAccessError(
                "The site temporarily rate-limited the request. Retry after a short "
                "wait; Chrome verification is not required unless the page explicitly "
                "shows a CAPTCHA or login prompt.",
                issue_code=SiteIssueCode.RATE_LIMITED,
            ) from exc
        safe_message = safe_external_error_message(message)
        issue_code = classify_site_issue(message)
        raise MediaDownloadError(
            safe_message,
            issue_code=(issue_code if issue_code != SiteIssueCode.UNKNOWN else None),
        ) from exc
