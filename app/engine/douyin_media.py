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


class DouyinMediaMixin:
    def _discover_douyin_item(
        self,
        url: str,
        should_cancel: CancelCallback,
    ) -> DiscoveryResult:
        expected_id = self._douyin_video_id(url)
        if not expected_id:
            raise DiscoveryError("The Douyin URL has no video identifier")

        if self.config.anonymous_douyin and not self.config.cookie_browser:
            detail = fetch_signed_aweme_detail(
                expected_id, verification_url=url, use_browser_cookies=False,
                should_cancel=should_cancel, status_callback=self._report_discovery,
            )
            media = verified_aweme_metadata(detail, expected_id)
            if not media:
                raise TemporaryAccessError("公开会话未取得经过校验的完整作品数据，请稍后重试。", issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED)
            author = str(media.get("author") or "抖音作者")
            item = DownloadItem(
                id=_item_key(Platform.DOUYIN, expected_id, url, 1),
                media_id=expected_id, source_url=url,
                title=str(media.get("title") or "抖音作品"),
                upload_date=self._douyin_upload_date(media["create_time"]),
                author=author, playlist_index=1, extractor_key="Douyin",
                media_type=MediaType.IMAGE if media.get("media_kind") == "image" else MediaType.VIDEO,
                metadata={"verification_url": url, "item_identity_verified": True,
                          "douyin_item_media": media, "access_mode": "public"},
            )
            return DiscoveryResult(author=author, items=[item])

        def operation(use_cookies: bool) -> tuple[dict[str, Any], str | None]:
            options = {
                **self._douyin_ytdlp_options(use_cookies),
                "skip_download": True,
                "noplaylist": True,
                "ignoreerrors": False,
                "logger": _YdlLogger(),
            }
            with YoutubeDL(options) as ydl:
                info = self._extract_douyin_raw_info(
                    ydl,
                    url,
                    expected_id=expected_id,
                    expected_profile_id=None,
                    verification_url=url,
                    profile_metadata=None,
                    fallback_title="Untitled Douyin video",
                    should_cancel=should_cancel,
                    status_callback=self._report_discovery,
                )
                self._validate_douyin_info(info, expected_id, url)
                video_uri = self._douyin_video_uri(info, expected_id, url)
                if (
                    video_uri is None
                    and not isinstance(info.get("_douyin_profile_media"), dict)
                    and use_cookies
                ):
                    detail = fetch_signed_aweme_detail(
                        expected_id,
                        verification_url=url,
                        expected_sec_uid=None,
                        cookie_profile=self.config.cookie_profile,
                        should_cancel=should_cancel,
                        status_callback=self._report_discovery,
                    )
                    verified_metadata = verified_aweme_metadata(
                        detail,
                        expected_id,
                    )
                    if verified_metadata:
                        info["_douyin_profile_media"] = verified_metadata
                        info["channel_id"] = verified_metadata["owner_id"]
                        info["channel"] = str(
                            verified_metadata.get("author") or "Douyin Author"
                        )
                        info["title"] = str(
                            verified_metadata.get("title")
                            or info.get("title")
                            or expected_id
                        )
                        create_time = verified_metadata.get("create_time")
                        if type(create_time) is int and create_time > 0:
                            info["timestamp"] = create_time
                        if verified_metadata.get("media_kind") == "video":
                            video_uri = (
                                str(verified_metadata.get("video_uri") or "").strip()
                                or None
                            )
            return info, video_uri

        (info, video_uri), fallback = self._run_with_cookie_fallback(
            operation,
            url=url,
        )
        author = (
            self._author_from_info(info, fallback="Douyin Author") or "Douyin Author"
        )
        title = str(info.get("title") or "").strip()
        if not title or title == expected_id or title.isdigit():
            title = "Untitled Douyin video"
        cached_media: dict[str, Any] = {
            "media_id": expected_id,
            "title": title,
            "author": author,
        }
        if video_uri:
            cached_media["video_uri"] = video_uri
        native_formats = info.get("formats")
        quality_candidates: list[Any] = [info]
        if isinstance(native_formats, list):
            quality_candidates.extend(
                value
                for value in native_formats
                if isinstance(value, dict)
                and str(value.get("vcodec") or "").lower() != "none"
            )
        quality_floor = quality_floor_dimensions(quality_candidates)
        if quality_floor:
            cached_media["minimum_width"], cached_media["minimum_height"] = (
                quality_floor
            )
        owner_id = str(info.get("channel_id") or "").strip()
        if not owner_id:
            raise TemporaryAccessError(
                "Douyin did not return a verified author identity, so the author-feed "
                "highest-quality renditions could not be checked. Retry the original "
                "video; no default-only fallback was downloaded."
            )
        cached_media["owner_id"] = owner_id
        if not self.config.cookie_browser:
            raise TemporaryAccessError(
                "Douyin highest-quality verification requires the verified author "
                "feed. Enable automatic Chrome Cookie reading and retry the original "
                "video; no default-only fallback was downloaded."
            )
        if owner_id:
            try:
                embedded_media = info.get("_douyin_profile_media")
                enriched_media = (
                    embedded_media
                    if isinstance(embedded_media, dict)
                    else discover_item_metadata_from_profile(
                        owner_id,
                        expected_id,
                        cookie_profile=self.config.cookie_profile,
                        prefer_exact_detail=True,
                        should_cancel=should_cancel,
                        status_callback=self._report_discovery,
                    )
                )
            except DownloadCancelledError:
                raise
            except TemporaryAccessError as exc:
                raise TemporaryAccessError(
                    "Douyin verified author-feed request failed after automatic "
                    "retries. Site response: "
                    f"{safe_external_error_message(exc)}",
                    issue_code=classify_site_issue(exc),
                ) from exc
            except AuthenticationRequiredError as exc:
                issue_code = classify_site_issue(
                    exc,
                    authentication_required=True,
                )
                if issue_code == SiteIssueCode.LOGIN_REQUIRED:
                    message = (
                        "Douyin requires a current Chrome login session to verify "
                        "the author's highest-quality renditions. Open the original "
                        "video in Chrome, sign in, then retry."
                    )
                else:
                    message = (
                        "Douyin displayed an explicit CAPTCHA or verification page "
                        "while checking the author's highest-quality renditions. "
                        "Open the original video in Chrome, finish the visible "
                        "verification, then retry."
                    )
                raise AuthenticationRequiredError(
                    message,
                    verification_url=url,
                    issue_code=issue_code,
                ) from exc
            except DiscoveryError as exc:
                issue_code = classify_site_issue(exc)
                if issue_code == SiteIssueCode.LOCAL_CONFIGURATION:
                    raise MediaDownloadError(
                        "Douyin author-feed quality verification could not start "
                        "because a required local component is unavailable. "
                        f"Details: {safe_external_error_message(exc)}",
                        issue_code=issue_code,
                    ) from exc
                diagnostic_code = public_signing_diagnostic_code(exc)
                raise MediaDownloadError(
                    "Douyin author-feed data failed identity or integrity validation. "
                    "Retry the original video; Chrome verification is not required "
                    "unless Douyin explicitly shows a CAPTCHA or login page. "
                    f"Diagnostic code: {diagnostic_code}.",
                    issue_code=issue_code,
                    diagnostic_code=diagnostic_code,
                ) from exc
            if not enriched_media:
                raise TemporaryAccessError(
                    "Douyin could not find the requested video in its verified "
                    "author feed, so the highest quality could not be confirmed. "
                    "Retry the original video; Chrome verification was not requested."
                )
            if enriched_media.get("media_kind") == "image":
                if not is_complete_profile_media_metadata(
                    enriched_media,
                    expected_id,
                    owner_id,
                ):
                    raise MediaDownloadError(
                        "Douyin image metadata failed item or author identity "
                        "validation. The cross-wired response was blocked; Chrome "
                        "verification was not requested.",
                        issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
                    )
                image_title = str(enriched_media.get("title") or title).strip()
                if (
                    not image_title
                    or image_title == expected_id
                    or image_title.isdigit()
                ):
                    image_title = "Untitled Douyin image"
                image_author = str(
                    enriched_media.get("author") or author or "Douyin Author"
                ).strip()
                create_time = enriched_media.get("create_time")
                if type(create_time) is not int or create_time <= 0:
                    raise TemporaryAccessError(
                        "Douyin returned the Live Photo without a verified publish "
                        "time. Retry the original item so it can be named correctly."
                    )
                upload_date = self._douyin_upload_date(create_time)
                item = DownloadItem(
                    id=_item_key(Platform.DOUYIN, expected_id, url, 1),
                    media_id=expected_id,
                    source_url=url,
                    title=image_title,
                    upload_date=upload_date,
                    author=image_author,
                    playlist_index=1,
                    extractor_key="Douyin",
                    media_type=MediaType.IMAGE,
                    metadata={
                        "verification_url": url,
                        "item_identity_verified": True,
                        "douyin_item_media": enriched_media,
                    },
                )
                return DiscoveryResult(
                    author=image_author,
                    items=[item],
                    cookie_fallback_used=fallback,
                    warning=(
                        " ".join(
                            value
                            for value in (
                                COOKIE_FALLBACK_WARNING if fallback else None,
                                (
                                    DOUYIN_LIVE_PHOTO_STATIC_FALLBACK_WARNING
                                    if enriched_media.get(
                                        "live_photo_static_fallback_indexes"
                                    )
                                    else None
                                ),
                            )
                            if value
                        )
                        or None
                    ),
                )
            enriched_video_uri = str(enriched_media.get("video_uri") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", enriched_video_uri):
                raise TemporaryAccessError(
                    "Douyin returned the requested item without a verified video "
                    "identity. Retry the original link; no lower-quality fallback "
                    "was downloaded."
                )
            if video_uri and enriched_video_uri != video_uri:
                raise MediaDownloadError(
                    "Douyin author-feed enrichment returned a different media "
                    "identity for the requested video. The cross-wired response was "
                    "blocked; Chrome verification was not requested."
                )
            cached_media["video_uri"] = enriched_video_uri
            if "direct_candidates" in enriched_media:
                cached_media["direct_candidates"] = enriched_media["direct_candidates"]
            enriched_duration_ms = _normalized_douyin_duration_ms(
                enriched_media.get("duration_ms")
            )
            if enriched_duration_ms is not None:
                cached_media["duration_ms"] = enriched_duration_ms
            if not self._douyin_direct_candidates_from_cache(cached_media):
                raise TemporaryAccessError(
                    "Douyin author-feed data did not include a verified direct "
                    "highest-quality rendition. Retry after a short wait; no "
                    "default-only fallback was downloaded."
                )
            combined_floor = quality_floor_dimensions(
                [cached_media, enriched_media],
                cap_full_hd=False,
            )
            if combined_floor:
                cached_media["minimum_width"], cached_media["minimum_height"] = (
                    combined_floor
                )
        if "duration_ms" not in cached_media:
            duration = self._float_or_none(info.get("duration"))
            if duration and 0 < duration <= DOUYIN_MAX_DURATION_MS / 1_000:
                cached_media["duration_ms"] = int(round(duration * 1_000))
        timestamp = self._float_or_none(info.get("timestamp"))
        if timestamp and timestamp > 0:
            cached_media["create_time"] = int(timestamp)

        item = DownloadItem(
            id=_item_key(Platform.DOUYIN, expected_id, url, 1),
            media_id=expected_id,
            source_url=url,
            title=title,
            upload_date=self._normalize_date(info.get("upload_date")),
            author=author,
            playlist_index=1,
            extractor_key="Douyin",
            media_type=MediaType.VIDEO,
            metadata={
                "verification_url": url,
                "item_identity_verified": True,
                "douyin_item_media": cached_media,
            },
        )
        return DiscoveryResult(
            author=author or "Douyin Author",
            items=[item],
            cookie_fallback_used=fallback,
            warning=COOKIE_FALLBACK_WARNING if fallback else None,
        )
    @staticmethod
    def _douyin_upload_date(create_time: int) -> str:
        china_timezone = timezone(timedelta(hours=8))
        try:
            return (
                datetime.fromtimestamp(create_time, china_timezone).date().isoformat()
            )
        except (OverflowError, OSError, ValueError) as exc:
            raise DiscoveryError("The Douyin publish time is invalid") from exc
    def _douyin_ytdlp_options(self, use_cookies: bool = True) -> dict[str, Any]:
        options = self._base_options(use_cookies)
        options["socket_timeout"] = min(
            max(1, int(self.config.socket_timeout_seconds)),
            DOUYIN_YTDLP_SOCKET_TIMEOUT_SECONDS,
        )
        options["retries"] = min(
            max(0, int(self.config.retries)),
            DOUYIN_YTDLP_RETRIES,
        )
        options["extractor_retries"] = min(
            int(options.get("extractor_retries") or 0),
            DOUYIN_YTDLP_RETRIES,
        )
        return options
    @staticmethod
    def _douyin_video_id(url: str) -> str | None:
        match = re.search(r"/video/(\d+)(?:[/?#]|$)", url)
        return match.group(1) if match else None
    @staticmethod
    def _douyin_profile_id(url: str) -> str | None:
        match = re.search(r"/user/([^/?#]+)(?:[/?#]|$)", url)
        return unquote(match.group(1)).strip() if match else None
    @staticmethod
    def _is_douyin_media_host(hostname: str | None) -> bool:
        normalized = (hostname or "").lower().rstrip(".")
        return normalized == "amemv.com" or normalized.endswith(".amemv.com")
    @staticmethod
    def _is_douyin_direct_media_host(hostname: str | None) -> bool:
        normalized = (hostname or "").lower().rstrip(".")
        return any(
            normalized == domain or normalized.endswith(f".{domain}")
            for domain in DOUYIN_DIRECT_MEDIA_DOMAINS
        )
    @staticmethod
    def _is_douyin_regional_media_host(hostname: str | None) -> bool:
        normalized = (hostname or "").lower().rstrip(".")
        return any(
            normalized == domain or normalized.endswith(f".{domain}")
            for domain in DOUYIN_REGIONAL_MEDIA_DOMAINS
        )
    @staticmethod
    def _douyin_regional_media_family(hostname: str | None) -> str | None:
        normalized = (hostname or "").lower().rstrip(".")
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
    @staticmethod
    def _parse_legacy_ipv4_part(value: str) -> int | None:
        if not value:
            return None
        try:
            if value.lower().startswith("0x"):
                return int(value[2:], 16)
            if len(value) > 1 and value.startswith("0"):
                return int(value[1:], 8)
            return int(value, 10)
        except ValueError:
            return None
    @classmethod
    def _looks_like_ip_literal(cls, hostname: str) -> bool:
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            return True
        parts = hostname.split(".")
        if not 1 <= len(parts) <= 4:
            return False
        numbers = [cls._parse_legacy_ipv4_part(part) for part in parts]
        if any(number is None or number < 0 for number in numbers):
            return False
        limits = {
            1: (0xFFFFFFFF,),
            2: (0xFF, 0xFFFFFF),
            3: (0xFF, 0xFF, 0xFFFF),
            4: (0xFF, 0xFF, 0xFF, 0xFF),
        }[len(numbers)]
        return all(number <= limit for number, limit in zip(numbers, limits))
    @staticmethod
    def _is_special_use_hostname(hostname: str) -> bool:
        special_names = {
            "example.com",
            "example.net",
            "example.org",
            "home.arpa",
            "localhost",
        }
        special_suffixes = (
            ".home",
            ".home.arpa",
            ".internal",
            ".invalid",
            ".lan",
            ".local",
            ".localhost",
            ".alt",
            ".example",
            ".onion",
            ".test",
            ".example.com",
            ".example.net",
            ".example.org",
        )
        return hostname in special_names or hostname.endswith(special_suffixes)
    @classmethod
    def _parse_strict_https_url(
        cls,
        value: str,
    ) -> tuple[str | None, str | None]:
        raw_value = str(value or "")
        if (
            not raw_value
            or len(raw_value) > 8_192
            or "\\" in raw_value
            or any(character.isspace() for character in raw_value)
            or any(
                ord(character) < 32 or ord(character) == 127 for character in raw_value
            )
        ):
            return None, "malformed-url"
        try:
            parsed = urlsplit(raw_value)
            hostname = (parsed.hostname or "").lower().rstrip(".")
            username = parsed.username
            password = parsed.password
            port = parsed.port
        except (TypeError, ValueError):
            return None, "malformed-url"
        if "[" in parsed.netloc or "]" in parsed.netloc:
            return None, (
                "ip-literal"
                if cls._looks_like_ip_literal(hostname)
                else "malformed-url"
            )
        if username is not None or password is not None:
            return None, "embedded-credentials"
        if not hostname:
            return None, "missing-host"
        if len(hostname) > 253:
            return None, "hostname-too-long"
        try:
            hostname.encode("ascii")
        except UnicodeEncodeError:
            return None, "non-ascii-host"
        labels = hostname.split(".")
        is_ip_literal = cls._looks_like_ip_literal(hostname)
        is_dns_hostname = (
            not is_ip_literal
            and len(labels) >= 2
            and not any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in labels
            )
        )
        if parsed.scheme.lower() != "https":
            return None, "non-https-scheme"
        if is_ip_literal:
            return None, "ip-literal"
        if len(labels) < 2:
            return None, "single-label-host"
        if not is_dns_hostname:
            return None, "invalid-hostname"
        if cls._is_special_use_hostname(hostname):
            return None, "local-or-special-use-host"
        if port not in {None, 443}:
            return None, "nonstandard-port"
        return hostname, None
    @classmethod
    def _strict_https_hostname(cls, value: str) -> str | None:
        hostname, reason = cls._parse_strict_https_url(value)
        return hostname if reason is None else None
    @classmethod
    def _is_verified_douyin_media_redirect(
        cls,
        source_url: str,
        final_url: str,
    ) -> bool:
        if not cls._is_trusted_douyin_asset_url(source_url, MediaType.VIDEO):
            return False
        hostname = cls._strict_https_hostname(final_url)
        return cls._is_douyin_regional_media_host(hostname)
    @classmethod
    def _douyin_redirect_diagnostic(
        cls,
        value: str,
    ) -> tuple[str | None, str]:
        hostname, reason = cls._parse_strict_https_url(value)
        return hostname, reason or "unrecognized-host"
    @classmethod
    def _douyin_media_redirect_rejection_reason(
        cls,
        value: str,
        media_type: MediaType,
        *,
        allow_verified_regional: bool,
    ) -> str | None:
        hostname, validation_reason = cls._parse_strict_https_url(value)
        if validation_reason is not None:
            return validation_reason
        if cls._is_trusted_douyin_asset_url(value, media_type):
            return None
        if media_type == MediaType.VIDEO and cls._is_douyin_regional_media_host(
            hostname
        ):
            return None if allow_verified_regional else "unverified-source-binding"
        return "unrecognized-host"
    @classmethod
    def _douyin_redirect_rejection(
        cls,
        value: str,
        reason: str,
    ) -> _DouyinRedirectRejected:
        hostname, _ = cls._douyin_redirect_diagnostic(value)
        if reason == "nonstandard-port":
            try:
                hostname = (urlsplit(value).hostname or "").lower().rstrip(".") or None
            except (TypeError, ValueError):
                hostname = None
        visible_hostname = (
            cls._douyin_regional_media_family(hostname)
            if reason in {"unverified-source-binding", "nonstandard-port"}
            else None
        )
        host_fingerprint = None
        if hostname and (
            reason == "unrecognized-host"
            or (reason == "nonstandard-port" and not visible_hostname)
        ):
            host_fingerprint = hashlib.sha256(hostname.encode("ascii")).hexdigest()[:12]
        redirect_port = None
        if reason == "nonstandard-port":
            try:
                parsed_port = urlsplit(value).port
            except (TypeError, ValueError):
                parsed_port = None
            if type(parsed_port) is int and 1 <= parsed_port <= 65_535:
                redirect_port = parsed_port
        return _DouyinRedirectRejected(
            "media endpoint redirected to an unrecognized Douyin CDN host "
            f"(host: {visible_hostname or 'unavailable'}; "
            f"host-fingerprint: {host_fingerprint or 'unavailable'}; "
            f"port: {redirect_port or 'unavailable'}; "
            f"reason: {reason})",
            redirect_host=visible_hostname,
            redirect_host_fingerprint=host_fingerprint,
            redirect_port=redirect_port,
            redirect_reason=reason,
        )
    @classmethod
    def _open_douyin_media_response(
        cls,
        ydl: YoutubeDL,
        request: Request,
        *,
        redirect_rejection_reason: Callable[[str], str | None],
        should_cancel: CancelCallback | None = None,
    ):
        should_cancel = should_cancel or (lambda: False)
        if should_cancel():
            raise DownloadCancelled("Task cancelled")
        initial_reason = redirect_rejection_reason(request.url)
        if initial_reason is not None:
            raise cls._douyin_redirect_rejection(request.url, initial_reason)
        director = getattr(ydl, "_request_director", None)
        if director is None:
            raise _DouyinProbeRejected(
                "secure Douyin redirect validation requires a yt-dlp request director"
            )
        handler = getattr(director, "handlers", {}).get("Requests")
        if not isinstance(handler, RequestsRH) or requests is None:
            raise _DouyinProbeRejected(
                "secure Douyin redirect validation requires the yt-dlp requests handler"
            )
        headers = handler._get_headers(request)
        isolated_cookie_jar = getattr(
            handler,
            "_douyin_isolated_media_cookie_jar",
            None,
        )
        if not isinstance(isolated_cookie_jar, CookieJar):
            isolated_cookie_jar = CookieJar()
            setattr(
                handler,
                "_douyin_isolated_media_cookie_jar",
                isolated_cookie_jar,
            )
        else:
            isolated_cookie_jar.clear()
        session = handler._get_instance(
            cookiejar=isolated_cookie_jar,
            legacy_ssl_support=request.extensions.get("legacy_ssl"),
        )
        current_url = request.url
        current_headers = {
            key: value
            for key, value in headers.items()
            if key.lower()
            not in {
                "authorization",
                "cookie",
                "host",
                "proxy-authorization",
            }
        }
        request_timeout = handler._calculate_timeout(request)
        redirect_deadline = time.monotonic() + request_timeout
        for redirect_count in range(DOUYIN_MAX_MEDIA_REDIRECTS + 1):
            if should_cancel():
                raise DownloadCancelled("Task cancelled")
            remaining_timeout = redirect_deadline - time.monotonic()
            if remaining_timeout <= 0:
                raise TransportError(
                    cause=TimeoutError("Douyin redirect chain timed out")
                )
            redirect_location_present = False
            redirect_location: str | None = None

            def suppress_requests_redirect_preparation(response, *args, **kwargs):
                nonlocal redirect_location_present, redirect_location
                redirect_location_present = "Location" in response.headers
                redirect_location = response.headers.pop("Location", None)
                return response

            try:
                raw_response = session.request(
                    method=request.method,
                    url=current_url,
                    data=request.data,
                    headers=current_headers,
                    timeout=max(0.001, min(request_timeout, remaining_timeout)),
                    proxies=handler._get_proxies(request),
                    allow_redirects=False,
                    stream=True,
                    hooks={"response": suppress_requests_redirect_preparation},
                )
            except requests.exceptions.SSLError as exc:
                if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                    raise CertificateVerifyError(cause=exc) from exc
                raise SSLError(cause=exc) from exc
            except requests.exceptions.ProxyError as exc:
                raise ProxyError(cause=exc) from exc
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ) as exc:
                raise TransportError(cause=exc) from exc
            except urllib3.exceptions.HTTPError as exc:
                raise TransportError(cause=exc) from exc
            except requests.exceptions.RequestException as exc:
                raise RequestError(cause=exc) from exc

            if redirect_location_present:
                raw_response.headers["Location"] = redirect_location or ""

            if should_cancel():
                raw_response.close()
                raise DownloadCancelled("Task cancelled")

            is_redirect_response = bool(raw_response.is_redirect) or getattr(
                raw_response,
                "status_code",
                None,
            ) in {301, 302, 303, 307, 308}
            if is_redirect_response:
                location = str(raw_response.headers.get("Location") or "")
                if (
                    not location
                    or len(location) > 8_192
                    or "\\" in location
                    or any(character.isspace() for character in location)
                    or any(
                        ord(character) < 32 or ord(character) == 127
                        for character in location
                    )
                ):
                    raw_response.close()
                    raise cls._douyin_redirect_rejection(
                        "",
                        "malformed-url",
                    )
                try:
                    target_url = urljoin(str(raw_response.url), location)
                except (TypeError, ValueError):
                    raw_response.close()
                    raise cls._douyin_redirect_rejection(
                        "",
                        "malformed-url",
                    )
                try:
                    reason = redirect_rejection_reason(target_url)
                except BaseException:
                    raw_response.close()
                    raise
                if reason is not None:
                    raw_response.close()
                    raise cls._douyin_redirect_rejection(target_url, reason)
                if redirect_count >= DOUYIN_MAX_MEDIA_REDIRECTS:
                    raw_response.close()
                    raise cls._douyin_redirect_rejection(
                        target_url,
                        "too-many-redirects",
                    )
                raw_response.close()
                if urlsplit(current_url).netloc.lower() != (
                    urlsplit(target_url).netloc.lower()
                ):
                    current_headers = {
                        key: value
                        for key, value in current_headers.items()
                        if key.lower()
                        not in {
                            "authorization",
                            "cookie",
                            "host",
                            "proxy-authorization",
                        }
                    }
                current_url = target_url
                continue

            response = RequestsResponseAdapter(raw_response)
            if not 200 <= response.status < 300:
                raise HTTPError(response)
            return response
        raise AssertionError("unreachable Douyin redirect loop state")
    @classmethod
    def _is_trusted_douyin_asset_url(
        cls,
        value: str,
        media_type: MediaType,
    ) -> bool:
        hostname = cls._strict_https_hostname(value)
        if not hostname:
            return False
        if media_type == MediaType.IMAGE:
            return hostname == "douyinpic.com" or hostname.endswith(".douyinpic.com")
        return cls._is_douyin_direct_media_host(hostname)
    @classmethod
    def _douyin_direct_candidates_from_cache(
        cls,
        cached: dict[str, Any],
    ) -> list[dict[str, Any]]:
        raw_candidates = cached.get("direct_candidates")
        if not isinstance(raw_candidates, list):
            return []
        expected_video_uri = str(cached.get("video_uri") or "").strip()
        result: list[dict[str, Any]] = []
        for value in raw_candidates[:4]:
            if not isinstance(value, dict):
                continue
            if (
                expected_video_uri
                and str(value.get("video_uri") or "").strip() != expected_video_uri
            ):
                continue
            try:
                width = int(value.get("width") or 0)
                height = int(value.get("height") or 0)
                bit_rate = int(value.get("bit_rate") or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if not (0 < width <= 16_384 and 0 < height <= 16_384):
                continue
            urls: list[str] = []
            raw_urls = value.get("urls")
            if not isinstance(raw_urls, list):
                continue
            for candidate_url in raw_urls[:20]:
                if not isinstance(candidate_url, str):
                    continue
                if not cls._is_trusted_douyin_asset_url(
                    candidate_url,
                    MediaType.VIDEO,
                ):
                    continue
                try:
                    parsed_candidate = urlsplit(candidate_url)
                    candidate_video_ids = parse_qs(parsed_candidate.query).get(
                        "video_id"
                    )
                except (TypeError, ValueError):
                    continue
                if (
                    parsed_candidate.path.rstrip("/") == "/aweme/v1/play"
                    and candidate_video_ids
                    and candidate_video_ids != [expected_video_uri]
                ):
                    continue
                if candidate_url not in urls:
                    urls.append(candidate_url)
            if not urls:
                continue
            urls.sort(
                key=lambda url: cls._douyin_direct_source_priority(
                    url,
                    expected_video_uri,
                )
            )
            urls = urls[:5]
            candidate: dict[str, Any] = {
                "width": width,
                "height": height,
                "urls": urls,
            }
            if bit_rate > 0:
                candidate["bit_rate"] = bit_rate
            codec_hint = str(value.get("codec_hint") or "").strip().lower()
            if codec_hint in {"h264", "hevc", "h265", "vvc", "h266", "bytevc2"}:
                candidate["codec_hint"] = codec_hint
            result.append(candidate)
        return result
    @classmethod
    def _douyin_direct_source_priority(
        cls,
        value: str,
        expected_video_uri: str,
    ) -> tuple[int, int]:
        try:
            parsed = urlsplit(value)
            query = parse_qs(parsed.query)
        except (TypeError, ValueError):
            return (3, 0)
        is_play_endpoint = bool(
            cls._is_trusted_douyin_asset_url(value, MediaType.VIDEO)
            and parsed.path.rstrip("/") == "/aweme/v1/play"
            and re.fullmatch(
                r"[A-Za-z0-9_-]{10,200}",
                str((query.get("video_id") or [""])[0]),
            )
            and query.get("video_id") == [expected_video_uri]
        )
        if is_play_endpoint:
            return (0, 0)
        return (2 if "dy_q" in query else 1, 0)
    @staticmethod
    def _raise_douyin_mismatch(
        expected_id: str, actual_id: str, verification_url: str
    ) -> None:
        raise MediaDownloadError(
            "Douyin returned data for a different video while requesting "
            f"{expected_id} (received {actual_id}). The mismatched response was "
            "blocked; Chrome verification was not requested."
        )
    @staticmethod
    def _is_douyin_playlist_result(info: dict[str, Any]) -> bool:
        result_type = str(info.get("_type") or "").lower()
        return info.get("entries") is not None or result_type in {
            "playlist",
            "multi_video",
        }
    def _validate_douyin_info(
        self,
        info: dict[str, Any],
        expected_id: str,
        verification_url: str,
        expected_profile_id: str | None = None,
    ) -> None:
        actual_id = str(info.get("id") or "").strip()
        if actual_id != expected_id:
            self._raise_douyin_mismatch(
                expected_id, actual_id or "missing", verification_url
            )
        if self._is_douyin_playlist_result(info):
            raise MediaDownloadError(DOUYIN_ITEM_EXPANSION_MESSAGE)
        if expected_profile_id:
            actual_profile_id = str(info.get("channel_id") or "").strip()
            if actual_profile_id != expected_profile_id:
                raise MediaDownloadError(
                    "Douyin returned data from a different author while requesting "
                    f"profile {expected_profile_id} (received "
                    f"{actual_profile_id or 'missing'}). The mismatched response was "
                    "blocked; Chrome verification was not requested."
                )
    def _douyin_video_uri(
        self,
        info: dict[str, Any],
        expected_id: str,
        verification_url: str,
        expected_profile_id: str | None = None,
    ) -> str | None:
        self._validate_douyin_info(
            info,
            expected_id,
            verification_url,
            expected_profile_id,
        )
        uris: set[str] = set()
        for value in info.get("formats") or []:
            if not isinstance(value, dict):
                continue
            media_url = value.get("url")
            if not isinstance(media_url, str):
                continue
            parsed = urlsplit(media_url)
            if (
                not self._is_douyin_media_host(parsed.hostname)
                or parsed.path.rstrip("/") != "/aweme/v1/play"
            ):
                continue
            for uri in parse_qs(parsed.query).get("video_id", []):
                if re.fullmatch(r"[A-Za-z0-9_-]{10,200}", uri):
                    uris.add(uri)
        if len(uris) > 1:
            raise MediaDownloadError(
                "Douyin returned multiple media identities for one video. The "
                "cross-wired response was blocked; Chrome verification was not "
                "requested."
            )
        return next(iter(uris), None)
    @staticmethod
    def _should_use_douyin_signed_detail(error: DownloadError) -> bool:
        message = str(error).lower()
        if any(
            marker in message
            for marker in (
                "connection reset",
                "connection refused",
                "network is unreachable",
                "temporary failure in name resolution",
                "name or service not known",
                "proxy error",
                "certificate verify",
                "ssl error",
                "timed out",
                "timeout",
            )
        ):
            return False
        return (
            _is_auth_error(message)
            or any(marker in message for marker in DOUYIN_EMPTY_DETAIL_MARKERS)
            or any(
                marker in message
                for marker in (
                    "an extractor error has occurred",
                    "unable to extract video data",
                    "no video formats found",
                    "requested format is not available",
                    "keyerror",
                    "'video'",
                    '"video"',
                )
            )
        )
    def _extract_douyin_raw_info(
        self,
        ydl: YoutubeDL,
        source_url: str,
        *,
        expected_id: str,
        expected_profile_id: str | None,
        verification_url: str,
        profile_metadata: dict[str, Any] | None,
        fallback_title: str,
        should_cancel: CancelCallback,
        status_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        cached_result = self._douyin_raw_info_from_item_metadata(
            profile_metadata,
            expected_id=expected_id,
            expected_profile_id=expected_profile_id,
            verification_url=verification_url,
            fallback_title=fallback_title,
        ) or self._douyin_raw_info_from_profile_metadata(
            profile_metadata,
            expected_id=expected_id,
            expected_profile_id=expected_profile_id,
            verification_url=verification_url,
            fallback_title=fallback_title,
        )
        if cached_result:
            return cached_result
        if status_callback:
            status_callback("Reading Douyin item metadata with bounded retries")
        try:
            raw_result = ydl.extract_info(
                source_url,
                download=False,
                process=False,
            )
            if isinstance(raw_result, dict):
                if self._is_douyin_playlist_result(raw_result):
                    extraction_error = DownloadError(
                        "Douyin returned an uploader profile instead of the requested "
                        "video"
                    )
                else:
                    actual_id = str(raw_result.get("id") or "").strip()
                    if actual_id == expected_id:
                        return raw_result
                    extraction_error = DownloadError(
                        "Douyin returned data for a different video while requesting "
                        f"{expected_id} (received {actual_id or 'missing'})"
                    )
            else:
                extraction_error = DownloadError(
                    "Douyin returned an empty aweme detail"
                )
        except DownloadError as exc:
            extraction_error = exc

        if not self.config.cookie_browser or not self._should_use_douyin_signed_detail(
            extraction_error
        ):
            raise extraction_error
        if should_cancel():
            raise DownloadCancelled("Task cancelled")

        detail = fetch_signed_aweme_detail(
            expected_id,
            verification_url=verification_url,
            expected_sec_uid=expected_profile_id,
            cookie_profile=self.config.cookie_profile,
            should_cancel=should_cancel,
            status_callback=status_callback,
        )
        verified_metadata = verified_aweme_metadata(
            detail,
            expected_id,
            expected_profile_id=expected_profile_id,
        )
        if verified_metadata and verified_metadata.get("media_kind") == "image":
            return {
                "id": expected_id,
                "channel_id": verified_metadata["owner_id"],
                "channel": str(verified_metadata.get("author") or "Douyin Author"),
                "title": str(
                    verified_metadata.get("title") or fallback_title or expected_id
                ),
                "timestamp": verified_metadata.get("create_time"),
                "formats": [],
                "_douyin_profile_media": verified_metadata,
                "webpage_url": source_url,
                "original_url": source_url,
                "extractor": "Douyin",
                "extractor_key": "Douyin",
            }
        images = detail.get("images")
        is_image_post = str(detail.get("aweme_type") or "") == "68" or (
            isinstance(images, list) and bool(images)
        )
        if is_image_post:
            raise TemporaryAccessError(
                "Douyin returned the requested image or Live Photo item without "
                "complete trusted media metadata. Retry the original link so the "
                "item can be parsed again; Chrome verification is not required.",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            )
        parsed = DouyinIE(ydl)._parse_aweme_video_app(detail)
        if not isinstance(parsed, dict):
            raise DownloadError("Douyin signed detail returned no downloadable media")
        if verified_metadata and verified_metadata.get("media_kind") == "video":
            self._validate_douyin_info(
                parsed,
                expected_id,
                verification_url,
                str(verified_metadata["owner_id"]),
            )
            parsed["_douyin_profile_media"] = verified_metadata
        parsed.setdefault("webpage_url", source_url)
        parsed.setdefault("original_url", source_url)
        parsed.setdefault("extractor", "Douyin")
        parsed.setdefault("extractor_key", "Douyin")
        return parsed
    def _douyin_raw_info_from_item_metadata(
        self,
        metadata: dict[str, Any] | None,
        *,
        expected_id: str,
        expected_profile_id: str | None,
        verification_url: str,
        fallback_title: str,
    ) -> dict[str, Any] | None:
        if not metadata or "douyin_item_media" not in metadata:
            return None
        cached = metadata.get("douyin_item_media")
        if (
            not isinstance(cached, dict)
            or metadata.get("item_identity_verified") is not True
            or self._douyin_video_id(verification_url) != expected_id
        ):
            raise MediaDownloadError(
                "Douyin item metadata could not be verified. Create a new task "
                "from the original video and retry; Chrome verification was not "
                "requested."
            )
        cached_id = str(cached.get("media_id") or "").strip()
        if cached_id != expected_id:
            self._raise_douyin_mismatch(
                expected_id,
                cached_id or "missing",
                verification_url,
            )
        cached_owner = str(cached.get("owner_id") or "").strip()
        if expected_profile_id and cached_owner != expected_profile_id:
            raise MediaDownloadError(
                "Douyin item metadata belongs to a different author. Create a new "
                "task from the original link and retry; Chrome verification was not "
                "requested."
            )
        video_uri = str(cached.get("video_uri") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", video_uri):
            raise MediaDownloadError(
                "Douyin item metadata has no verified media identity. Create a new "
                "task from the original video and retry; Chrome verification was not "
                "requested."
            )

        result: dict[str, Any] = {
            "id": expected_id,
            "channel": str(cached.get("author") or "Douyin Author"),
            "title": str(cached.get("title") or fallback_title or expected_id),
            "formats": [
                {
                    "format_id": "item-cached-play",
                    "url": self._douyin_ratio_url(video_uri, "720p"),
                    "ext": "mp4",
                    "preference": -1,
                    "source_preference": -2,
                }
            ],
            "_format_sort_fields": ("quality", "codec", "size", "br"),
            "_douyin_verified_cache_only": True,
        }
        if cached_owner:
            result["channel_id"] = cached_owner
        direct_candidates = self._douyin_direct_candidates_from_cache(cached)
        if direct_candidates:
            result["_douyin_direct_candidates"] = direct_candidates
        quality_floor = quality_floor_dimensions([cached], cap_full_hd=False)
        if quality_floor:
            result["_douyin_minimum_width"], result["_douyin_minimum_height"] = (
                quality_floor
            )
        duration_ms = _normalized_douyin_duration_ms(cached.get("duration_ms"))
        if duration_ms is not None:
            result["duration"] = duration_ms / 1_000
        create_time = cached.get("create_time")
        if isinstance(create_time, int) and create_time > 0:
            result["timestamp"] = create_time
            result["upload_date"] = self._douyin_upload_date(create_time).replace(
                "-", ""
            )
        self._validate_douyin_info(
            result,
            expected_id,
            verification_url,
            expected_profile_id,
        )
        return result
    def _douyin_raw_info_from_profile_metadata(
        self,
        metadata: dict[str, Any] | None,
        *,
        expected_id: str,
        expected_profile_id: str | None,
        verification_url: str,
        fallback_title: str,
    ) -> dict[str, Any] | None:
        if not metadata or "douyin_profile_media" not in metadata:
            return None
        cached = metadata.get("douyin_profile_media")
        if (
            not isinstance(cached, dict)
            or metadata.get("profile_owner_verified") is not True
            or not expected_profile_id
        ):
            raise MediaDownloadError(
                "Douyin profile media metadata is incomplete. Retry the original "
                "profile to refresh it; Chrome verification is not required for "
                "this local metadata error.",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            )
        cached_id = str(cached.get("media_id") or "").strip()
        if cached_id != expected_id:
            self._raise_douyin_mismatch(
                expected_id,
                cached_id or "missing",
                verification_url,
            )
        cached_owner = str(cached.get("owner_id") or "").strip()
        if cached_owner != expected_profile_id:
            raise MediaDownloadError(
                "Douyin profile metadata belongs to a different author. Create a "
                "new task from the original profile and retry; Chrome verification "
                "was not requested."
            )
        raw_direct_candidates = cached.get("direct_candidates")
        if cached.get("media_kind") == "video" and raw_direct_candidates is None:
            raise TemporaryAccessError(
                "This saved Douyin task predates author-feed highest-quality "
                "verification. The task was paused instead of downloading a "
                "default-only fallback. Create a new task from the original profile."
            )
        if not is_complete_profile_media_metadata(
            cached,
            expected_id,
            expected_profile_id,
        ):
            raise MediaDownloadError(
                "Douyin profile media metadata is incomplete. Retry the original "
                "profile to refresh it; Chrome verification is not required for "
                "this local metadata error.",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            )
        video_uri = str(cached.get("video_uri") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", video_uri):
            raise MediaDownloadError(
                "Douyin profile metadata has no verified media identity. Create a "
                "new task from the original profile and retry; Chrome verification "
                "was not requested."
            )

        result: dict[str, Any] = {
            "id": expected_id,
            "channel_id": expected_profile_id,
            "channel": str(cached.get("author") or "Douyin Author"),
            "title": str(cached.get("title") or fallback_title or expected_id),
            "formats": [
                {
                    "format_id": "profile-cached-play",
                    "url": self._douyin_ratio_url(video_uri, "720p"),
                    "ext": "mp4",
                    "preference": -1,
                    "source_preference": -2,
                }
            ],
            "_format_sort_fields": ("quality", "codec", "size", "br"),
            "_douyin_profile_cache_only": True,
            "_douyin_verified_cache_only": True,
        }
        direct_candidates = self._douyin_direct_candidates_from_cache(cached)
        if direct_candidates:
            result["_douyin_direct_candidates"] = direct_candidates
        quality_floor = quality_floor_dimensions([cached], cap_full_hd=False)
        if quality_floor:
            result["_douyin_minimum_width"], result["_douyin_minimum_height"] = (
                quality_floor
            )
        duration_ms = _normalized_douyin_duration_ms(cached.get("duration_ms"))
        if duration_ms is not None:
            result["duration"] = duration_ms / 1_000
        create_time = cached.get("create_time")
        if isinstance(create_time, int) and create_time > 0:
            result["timestamp"] = create_time
            result["upload_date"] = self._douyin_upload_date(create_time).replace(
                "-", ""
            )
        self._validate_douyin_info(
            result,
            expected_id,
            verification_url,
            expected_profile_id,
        )
        return result
    def _download_douyin_profile_image(
        self,
        item: DownloadItem,
        output_dir: Path,
        *,
        callback: EventCallback | None,
        should_cancel: CancelCallback,
    ) -> DownloadOutcome:
        if should_cancel():
            raise DownloadCancelledError("Task cancelled")
        media_id = str(item.media_id or "").strip()
        canonical_item_url = (
            f"https://www.douyin.com/video/{media_id}" if media_id else ""
        )
        item_cached = item.metadata.get("douyin_item_media")
        profile_cached = item.metadata.get("douyin_profile_media")
        cached = item_cached if isinstance(item_cached, dict) else profile_cached
        is_bound_item = (
            isinstance(item_cached, dict)
            and item.metadata.get("item_identity_verified") is True
            and item.metadata.get("verification_url") == canonical_item_url
        )
        profile_url = str(item.metadata.get("profile_url") or "").strip()
        if is_bound_item:
            profile_url = (
                "https://www.douyin.com/user/"
                f"{str(item_cached.get('owner_id') or '').strip()}"
            )
        profile_id = self._douyin_profile_id(profile_url)
        try:
            profile_source = identify_url(profile_url)
            item_source = identify_url(item.source_url)
        except (TypeError, ValueError):
            profile_source = None
            item_source = None
        if (
            not (is_bound_item or item.metadata.get("profile_owner_verified") is True)
            or not profile_id
            or not media_id
            or profile_source is None
            or profile_source.platform != Platform.DOUYIN
            or profile_source.kind != SourceKind.PROFILE
            or profile_source.url != profile_url
            or item_source is None
            or item_source.platform != Platform.DOUYIN
            or item_source.kind != SourceKind.ITEM
            or item_source.url != canonical_item_url
            or item.source_url != canonical_item_url
            or not isinstance(cached, dict)
            or cached.get("media_kind") != "image"
        ):
            raise MediaDownloadError(
                "Douyin profile image metadata is incomplete or belongs to a "
                "different item. Create a new task from the original profile."
            )
        if not is_complete_profile_media_metadata(cached, media_id, profile_id):
            live_assets = cached.get("live_photo_assets")
            if isinstance(live_assets, list) and any(
                isinstance(value, dict) and value.get("direct_candidates") is None
                for value in live_assets
            ):
                raise TemporaryAccessError(
                    "This saved Douyin Live Photo task predates author-feed "
                    "highest-quality verification. The task was paused instead of "
                    "downloading a default-only fallback. Create a new task from the "
                    "original profile."
                )
            raise MediaDownloadError(
                "Douyin profile image metadata is incomplete. Create a new task "
                "from the original profile."
            )

        title = str(cached.get("title") or item.title or "").strip()
        if not title or title == media_id or title.isdigit():
            title = "Untitled Douyin image"
        author = str(cached.get("author") or item.author or "Douyin Author").strip()
        create_time = cached.get("create_time")
        if type(create_time) is not int or create_time <= 0:
            raise MediaDownloadError("Douyin profile image publish time is invalid")
        upload_date = self._douyin_upload_date(create_time)
        image_assets = self._douyin_cached_assets(
            cached.get("image_assets"),
            format_prefix="douyin-highest-image",
        )
        live_photo_assets = self._douyin_cached_assets(
            cached.get("live_photo_assets") or [],
            format_prefix="douyin-highest-live-photo",
        )
        if not image_assets:
            raise MediaDownloadError(
                "Douyin profile image has no highest-available assets",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            )

        if callback:
            callback(
                EngineEvent(
                    event="metadata",
                    title=title,
                    upload_date=upload_date,
                    author=author,
                    media_type=MediaType.IMAGE,
                )
            )

        live_photo_by_index = {asset.index: asset for asset in live_photo_assets}
        static_paths: dict[int, Path] = {}
        output_paths: list[str] = []
        completed_assets: list[RemoteAsset] = []
        transfers = [
            (asset, motion)
            for asset in image_assets
            for motion in ((False, True) if asset.index in live_photo_by_index else (False,))
        ]
        total_assets = len(transfers)
        progress_index = 0

        def transfer_preferred_asset(
            ydl: YoutubeDL,
            image_asset: RemoteAsset,
            position: int,
            motion: bool,
        ) -> tuple[Path, RemoteAsset, bool]:
            live_asset = live_photo_by_index.get(image_asset.index) if motion else None
            try:
                if live_asset:
                    reused = self._existing_douyin_live_photo_asset(
                        item,
                        output_dir,
                        media_id,
                        live_asset,
                        should_cancel=should_cancel,
                        expected_stem=static_paths[image_asset.index].stem,
                    )
                    if reused:
                        return reused[0], reused[1], True
                    selected = self._select_highest_douyin_live_photo_asset(
                        ydl,
                        live_asset,
                        callback=callback,
                        should_cancel=should_cancel,
                    )
                    path, chosen = self._download_first_available_asset(
                        ydl,
                        [selected],
                        output_dir,
                        upload_date,
                        title,
                        media_id,
                        profile_url,
                        platform=Platform.DOUYIN,
                        media_type=MediaType.VIDEO,
                        callback=callback,
                        should_cancel=should_cancel,
                        asset_index=live_asset.index,
                        progress_index=position,
                        progress_count=total_assets,
                        verify_declared_dimensions=True,
                        require_quality_fingerprint=True,
                        paired_stem=static_paths[image_asset.index].stem,
                        compact_names=item.metadata.get("_compact_asset_names") is True,
                    )
                    return path, chosen, False

                reused = self._existing_douyin_image_asset(
                    item,
                    output_dir,
                    media_id,
                    image_asset,
                )
                if reused:
                    return reused[0], reused[1], True
                path, chosen = self._download_first_available_asset(
                    ydl,
                    [image_asset],
                    output_dir,
                    upload_date,
                    title,
                    media_id,
                    profile_url,
                    platform=Platform.DOUYIN,
                    media_type=MediaType.IMAGE,
                    callback=callback,
                    should_cancel=should_cancel,
                    asset_index=image_asset.index,
                    progress_index=position,
                    progress_count=total_assets,
                    verify_declared_dimensions=True,
                    pair_image=image_asset.index in live_photo_by_index,
                    compact_names=item.metadata.get("_compact_asset_names") is True,
                )
                return path, chosen, False
            except DownloadCancelledError:
                raise
            except TemporaryAccessError:
                raise
            except Exception as exc:
                label = "Live Photo" if live_asset else "Image"
                raise MediaDownloadError(
                    f"{label} {image_asset.index} failed: "
                    f"{safe_external_error_message(exc)}",
                    issue_code=getattr(exc, "issue_code", None),
                ) from exc

        with YoutubeDL(self._base_options(False)) as ydl:
            for image_asset, motion in transfers:
                progress_index += 1
                path, chosen, reused = transfer_preferred_asset(
                    ydl,
                    image_asset,
                    progress_index,
                    motion,
                )
                if reused:
                    if callback:
                        callback(
                            EngineEvent(
                                event="downloading",
                                progress=TransferProgress(
                                    downloaded_bytes=int(chosen.size or 0),
                                    total_bytes=chosen.size,
                                    percent=progress_index * 100.0 / total_assets,
                                    fragment_index=progress_index,
                                    fragment_count=total_assets,
                                    filename=str(path),
                                ),
                            )
                        )
                output_paths.append(str(path))
                if not motion:
                    static_paths[image_asset.index] = path
                completed_assets.append(chosen)
                if callback:
                    callback(
                        EngineEvent(
                            event="asset_completed",
                            output_paths=list(output_paths),
                            selected_format=chosen.format_id,
                            resolution=self._asset_resolution(chosen),
                        )
                    )

        largest = max(
            completed_assets,
            key=lambda value: int(value.width or 0) * int(value.height or 0),
        )
        selected_format = (
            "douyin-highest-live-photos-or-images"
            if live_photo_assets
            else "douyin-highest-images"
        )
        outcome = DownloadOutcome(
            output_paths=output_paths,
            title=title,
            upload_date=upload_date,
            author=author,
            media_type=MediaType.IMAGE,
            selected_format=selected_format,
            resolution=self._asset_resolution(largest),
        )
        if callback:
            callback(
                EngineEvent(
                    event="completed",
                    title=outcome.title,
                    upload_date=outcome.upload_date,
                    author=outcome.author,
                    media_type=outcome.media_type,
                    selected_format=outcome.selected_format,
                    resolution=outcome.resolution,
                    output_paths=outcome.output_paths,
                )
            )
        return outcome
    def _existing_douyin_image_asset(
        self,
        item: DownloadItem,
        output_dir: Path,
        media_id: str,
        asset: RemoteAsset,
    ) -> tuple[Path, RemoteAsset] | None:
        expected_suffix = re.compile(
            rf"\[{re.escape(media_id)}\]-{asset.index:03d}(?: \((?:[2-9]|[1-9][0-9]+)\))?\.[^.]+$"
        )
        compact_suffix = re.compile(
            rf"^{asset.index:03d}(?: \((?:[2-9]|[1-9][0-9]+)\))?\.[^.]+$"
        )
        paths_to_check = (
            list(item.output_paths)
            if item.output_paths
            else [str(p) for p in output_dir.iterdir() if p.is_file() and not p.name.startswith(".")]
        )
        for value in paths_to_check:
            candidate = Path(value)
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                resolved = candidate.resolve(strict=True)
                valid_name = expected_suffix.search(resolved.name) or (
                    item.metadata.get("_compact_asset_names") is True
                    and compact_suffix.fullmatch(resolved.name)
                )
                if resolved.parent != output_dir or not valid_name:
                    continue
                with resolved.open("rb") as handle:
                    dimensions = self._image_dimensions(handle.read(1024 * 1024))
                size = resolved.stat().st_size
            except OSError:
                continue
            if not dimensions:
                continue
            width, height = dimensions
            if (
                asset.width
                and asset.height
                and (
                    min(width, height) < min(asset.width, asset.height)
                    or max(width, height) < max(asset.width, asset.height)
                )
            ):
                continue
            return (
                resolved,
                RemoteAsset(
                    candidates=list(asset.candidates),
                    index=asset.index,
                    width=width,
                    height=height,
                    size=size,
                    format_id=asset.format_id,
                ),
            )
        return None
    def _existing_douyin_live_photo_asset(
        self,
        item: DownloadItem,
        output_dir: Path,
        media_id: str,
        asset: RemoteAsset,
        *,
        should_cancel: CancelCallback,
        expected_stem: str | None = None,
    ) -> tuple[Path, RemoteAsset] | None:
        expected_suffix = re.compile(
            rf"\[{re.escape(media_id)}\]-{asset.index:03d}(?: \((?:[2-9]|[1-9][0-9]+)\))?\.mp4$"
        )
        compact_suffix = re.compile(
            rf"^{asset.index:03d}(?: \((?:[2-9]|[1-9][0-9]+)\))?\.mp4$"
        )
        quality_values = [
            value
            for value in (asset.quality_candidates or [])
            if isinstance(value, dict)
        ]
        floor_values = [
            {
                "width": asset.width,
                "height": asset.height,
                "bit_rate": 0,
            },
            *quality_values,
        ]
        highest_floor = max(
            floor_values,
            key=self._douyin_probe_quality_key,
        )
        verification_asset = RemoteAsset(
            candidates=list(asset.candidates),
            index=asset.index,
            width=int(highest_floor.get("width") or asset.width or 0) or None,
            height=int(highest_floor.get("height") or asset.height or 0) or None,
            format_id=(
                "douyin-highest-live-photo-saved-"
                f"{int(highest_floor.get('width') or asset.width or 0)}x"
                f"{int(highest_floor.get('height') or asset.height or 0)}"
            ),
            video_uri=asset.video_uri,
            duration=asset.duration,
            quality_candidates=asset.quality_candidates,
        )
        paths_to_check = (
            list(item.output_paths)
            if item.output_paths
            else [str(p) for p in output_dir.iterdir() if p.is_file() and not p.name.startswith(".")]
        )
        for value in paths_to_check:
            if should_cancel():
                raise DownloadCancelledError("Task cancelled")
            candidate = Path(value)
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                resolved = candidate.resolve(strict=True)
                if expected_stem is not None and resolved.stem != expected_stem:
                    continue
                valid_name = expected_suffix.search(resolved.name) or (
                    item.metadata.get("_compact_asset_names") is True
                    and compact_suffix.fullmatch(resolved.name)
                )
                if resolved.parent != output_dir or not valid_name:
                    continue
                chosen = self._verify_local_video_asset(
                    resolved,
                    verification_asset,
                    should_cancel=should_cancel,
                    require_quality_fingerprint=False,
                )
                size = resolved.stat().st_size
            except DownloadCancelledError:
                raise
            except (OSError, MediaDownloadError):
                continue
            return (
                resolved,
                RemoteAsset(
                    candidates=list(chosen.candidates),
                    index=chosen.index,
                    width=chosen.width,
                    height=chosen.height,
                    size=size,
                    format_id=chosen.format_id,
                    video_uri=chosen.video_uri,
                    duration=chosen.duration,
                    bit_rate=chosen.bit_rate,
                    quality_candidates=chosen.quality_candidates,
                    video_codec=chosen.video_codec,
                    audio_codec=chosen.audio_codec,
                ),
            )
        return None
    @classmethod
    def _douyin_cached_assets(
        cls,
        values: Any,
        *,
        format_prefix: str,
    ) -> list[RemoteAsset]:
        if not isinstance(values, list):
            return []
        result: list[RemoteAsset] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            try:
                index = int(value.get("index") or 0)
                width = int(value.get("width") or 0)
                height = int(value.get("height") or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            candidates = [
                candidate
                for candidate in value.get("candidates") or []
                if isinstance(candidate, str) and candidate
            ]
            if index <= 0 or width <= 0 or height <= 0 or not candidates:
                continue
            quality_candidates: list[dict[str, Any]] | None = None
            if format_prefix == "douyin-highest-live-photo":
                video_uri = str(value.get("video_uri") or "").strip()
                parsed_renditions: list[dict[str, Any]] = []
                for rendition in value.get("direct_candidates") or []:
                    if not isinstance(rendition, dict):
                        continue
                    try:
                        rendition_width = int(rendition.get("width") or 0)
                        rendition_height = int(rendition.get("height") or 0)
                        rendition_bit_rate = int(rendition.get("bit_rate") or 0)
                    except (TypeError, ValueError, OverflowError):
                        continue
                    rendition_urls = [
                        url
                        for url in rendition.get("urls") or []
                        if isinstance(url, str)
                        and cls._is_trusted_douyin_asset_url(url, MediaType.VIDEO)
                    ][:5]
                    if (
                        rendition_width <= 0
                        or rendition_height <= 0
                        or not rendition_urls
                        or str(rendition.get("video_uri") or "").strip() != video_uri
                    ):
                        continue
                    parsed_rendition: dict[str, Any] = {
                        "width": rendition_width,
                        "height": rendition_height,
                        "urls": list(dict.fromkeys(rendition_urls)),
                    }
                    if rendition_bit_rate > 0:
                        parsed_rendition["bit_rate"] = rendition_bit_rate
                    codec_hint = str(rendition.get("codec_hint") or "").strip().lower()
                    if codec_hint in {
                        "h264",
                        "hevc",
                        "h265",
                        "vvc",
                        "h266",
                        "bytevc2",
                    }:
                        parsed_rendition["codec_hint"] = codec_hint
                    parsed_renditions.append(parsed_rendition)
                quality_candidates = parsed_renditions or [
                    {
                        "width": width,
                        "height": height,
                        "urls": list(candidates),
                    }
                ]
            duration_ms = _normalized_douyin_duration_ms(value.get("duration_ms"))
            result.append(
                RemoteAsset(
                    candidates=candidates,
                    index=index,
                    width=width,
                    height=height,
                    format_id=f"{format_prefix}-{width}x{height}",
                    video_uri=(str(value.get("video_uri") or "").strip() or None),
                    duration=(duration_ms / 1_000 if duration_ms is not None else None),
                    quality_candidates=quality_candidates,
                )
            )
        return result
