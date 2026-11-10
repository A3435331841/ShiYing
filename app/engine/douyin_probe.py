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


class DouyinProbeMixin:
    @contextlib.contextmanager
    def _douyin_probe_reuse_scope(
        self,
        output_dir: Path,
    ) -> Iterator[None]:
        previous = getattr(self._douyin_probe_context, "reuse", None)
        with tempfile.TemporaryDirectory(
            prefix=".original-media-douyin-probe-",
            dir=output_dir,
        ) as temporary_directory:
            context = _DouyinProbeReuseContext(
                directory=Path(temporary_directory).resolve(strict=True)
            )
            self._douyin_probe_context.reuse = context
            try:
                yield
            finally:
                if previous is None:
                    with contextlib.suppress(AttributeError):
                        del self._douyin_probe_context.reuse
                else:
                    self._douyin_probe_context.reuse = previous
    @contextlib.contextmanager
    def _douyin_quality_probe_scope(self) -> Iterator[_DouyinQualityProbeRun]:
        previous = getattr(self._douyin_probe_context, "quality_run", None)
        if isinstance(previous, _DouyinQualityProbeRun):
            yield previous
            return
        budget = _DouyinNoProgressBudget(
            DOUYIN_QUALITY_PROBE_IDLE_TIMEOUT_SECONDS,
            "quality verification",
        )
        run = _DouyinQualityProbeRun(budget=budget)
        self._douyin_probe_context.quality_run = run
        try:
            yield run
        finally:
            if previous is None:
                with contextlib.suppress(AttributeError):
                    del self._douyin_probe_context.quality_run
            else:
                self._douyin_probe_context.quality_run = previous
    @staticmethod
    def _douyin_ratio_url(
        video_uri: str,
        ratio: str,
        *,
        hostname: str | None = None,
        line: str = "0",
    ) -> str:
        hostname = hostname or (
            DOUYIN_DEFAULT_PROBE_HOST if ratio == "default" else DOUYIN_RATIO_PROBE_HOST
        )
        query = urlencode(
            {
                "video_id": video_uri,
                "ratio": ratio,
                "line": line,
                "is_play_url": "1",
                "source": "PackSourceEnum_AWEME_DETAIL",
            }
        )
        return f"https://{hostname}/aweme/v1/play/?{query}"
    @classmethod
    def _douyin_default_probe_urls(cls, video_uri: str) -> list[str]:
        return [
            cls._douyin_ratio_url(
                video_uri,
                "default",
                hostname=hostname,
                line=line,
            )
            for hostname, line in (
                (DOUYIN_DEFAULT_PROBE_HOST, DOUYIN_DEFAULT_ROUTE_LINES[0]),
                (DOUYIN_RATIO_PROBE_HOST, DOUYIN_DEFAULT_ROUTE_LINES[1]),
                (DOUYIN_RATIO_PROBE_HOST, DOUYIN_DEFAULT_ROUTE_LINES[0]),
                (DOUYIN_DEFAULT_PROBE_HOST, DOUYIN_DEFAULT_ROUTE_LINES[1]),
            )
        ]
    def _probe_douyin_default_with_fallback(
        self,
        ydl: YoutubeDL,
        video_uri: str,
        *,
        expected_duration: float | None,
        callback: EventCallback | None,
        should_cancel: CancelCallback,
    ) -> dict[str, Any] | None:
        failures: list[Exception] = []
        candidate_urls = self._douyin_default_probe_urls(video_uri)
        for candidate_url in candidate_urls:
            try:
                probe = self._probe_douyin_ratio_with_retry(
                    ydl,
                    candidate_url,
                    ratio="default",
                    expected_duration=expected_duration,
                    callback=callback,
                    should_cancel=should_cancel,
                )
            except _DouyinNoProgressTimeout:
                raise
            except (DownloadCancelled, MediaDownloadError):
                raise
            except Exception as exc:
                failures.append(exc)
                continue
            if probe:
                probe["source_candidates"] = list(
                    dict.fromkeys([candidate_url, *candidate_urls])
                )
                return probe
        if failures:
            blocking = next(
                (
                    failure
                    for failure in failures
                    if isinstance(failure, _DouyinRedirectRejected)
                ),
                next(
                    (
                        failure
                        for failure in failures
                        if isinstance(failure, _DouyinProbeIntegrityChanged)
                    ),
                    next(
                        (
                            failure
                            for failure in failures
                            if self._should_pause_douyin_probe_error(failure)
                        ),
                        failures[-1],
                    ),
                ),
            )
            raise blocking
        return None
    def _add_douyin_probe_formats(
        self,
        ydl: YoutubeDL,
        info: dict[str, Any],
        *,
        expected_id: str,
        verification_url: str,
        expected_profile_id: str | None = None,
        callback: EventCallback | None = None,
        should_cancel: CancelCallback,
    ) -> bool:
        with self._douyin_quality_probe_scope():
            return self._add_douyin_probe_formats_scoped(
                ydl,
                info,
                expected_id=expected_id,
                verification_url=verification_url,
                expected_profile_id=expected_profile_id,
                callback=callback,
                should_cancel=should_cancel,
            )
    def _add_douyin_probe_formats_scoped(
        self,
        ydl: YoutubeDL,
        info: dict[str, Any],
        *,
        expected_id: str,
        verification_url: str,
        expected_profile_id: str | None = None,
        callback: EventCallback | None = None,
        should_cancel: CancelCallback,
    ) -> bool:
        formats = [
            value
            for value in (info.get("formats") or [])
            if not isinstance(value, dict)
            or not str(value.get("format_id") or "").startswith("douyin-api-")
        ]
        info["formats"] = formats
        video_uri = self._douyin_video_uri(
            info,
            expected_id,
            verification_url,
            expected_profile_id,
        )
        if not video_uri:
            info["_douyin_probe_failure"] = "no verified media identity was available"
            return False
        expected_duration = self._float_or_none(info.get("duration"))
        probes: list[dict[str, Any]] = []
        failures: list[tuple[str, str]] = []
        direct_candidates = info.get("_douyin_direct_candidates") or []
        if not isinstance(direct_candidates, list):
            direct_candidates = []
        if not direct_candidates:
            raise TemporaryAccessError(
                "Douyin author-feed quality renditions were unavailable, so the "
                "highest quality could not be verified. The task was paused before "
                "downloading this or later items. Refresh the task from the original "
                "link with Chrome Cookie enabled; no default-only fallback was "
                "downloaded."
            )
        quality_run = getattr(self._douyin_probe_context, "quality_run", None)
        if isinstance(quality_run, _DouyinQualityProbeRun):
            quality_run.candidate_index = 0
            quality_run.candidate_total = sum(
                len(candidate.get("urls") or [])
                for candidate in direct_candidates
                if isinstance(candidate, dict)
            ) + len(self._douyin_default_probe_urls(video_uri))
        direct_count = len(direct_candidates)
        direct_failures: list[_DouyinDirectProbeFailure] = []
        for index, candidate in enumerate(direct_candidates, start=1):
            if should_cancel():
                raise DownloadCancelled("Task cancelled")
            declared_width = int(candidate["width"])
            declared_height = int(candidate["height"])
            label = f"author-feed-{index}"
            if callback:
                callback(
                    EngineEvent(
                        event="probing",
                        message=(
                            "Checking Douyin author-feed quality "
                            f"{index}/{direct_count}: "
                            f"{declared_width}x{declared_height}"
                        ),
                    )
                )
            direct_probe, failure = self._probe_douyin_direct_rendition(
                ydl,
                candidate,
                label=label,
                expected_duration=expected_duration,
                callback=callback,
                should_cancel=should_cancel,
            )
            if direct_probe:
                probes.append(direct_probe)
            elif failure:
                direct_failures.append(failure)

        if should_cancel():
            raise DownloadCancelled("Task cancelled")
        if callback:
            callback(
                EngineEvent(
                    event="probing",
                    message="Checking Douyin quality 1/1: default",
                )
            )
        try:
            default_probe = self._probe_douyin_default_with_fallback(
                ydl,
                video_uri,
                expected_duration=expected_duration,
                callback=callback,
                should_cancel=should_cancel,
            )
        except _DouyinNoProgressTimeout:
            raise
        except DownloadCancelled:
            raise
        except MediaDownloadError:
            raise
        except Exception as exc:
            reason = self._safe_douyin_probe_failure(exc)
            if self._should_pause_douyin_probe_error(exc):
                error_type = (
                    DouyinMediaRefreshRequiredError
                    if isinstance(
                        exc,
                        (_DouyinRedirectRejected, _DouyinProbeResponseChanged),
                    )
                    else TemporaryAccessError
                )
                raise error_type(
                    "Douyin authoritative default quality source was temporarily "
                    "unavailable after automatic retries. The task was paused before "
                    "probing later items; wait briefly and continue the task. No "
                    "lower-quality fallback was downloaded. Probe details: "
                    f"default: {reason}"
                ) from exc
            failures.append(("default", reason))
        else:
            if default_probe:
                default_probe["requested_ratio"] = "default"
                probes.append(default_probe)
            else:
                failures.append(("default", "media metadata could not be parsed"))

        unresolved_direct = self._unresolved_douyin_direct_failures(
            direct_failures,
            probes,
        )
        if unresolved_direct:
            details = self._summarize_douyin_probe_failures(
                [(label, reason) for label, reason, _ in unresolved_direct]
            )
            if any(transient for _, _, transient in unresolved_direct):
                error_type = (
                    DouyinMediaRefreshRequiredError
                    if any(
                        self._douyin_probe_failure_requires_refresh(reason)
                        for _, reason, _ in unresolved_direct
                    )
                    else TemporaryAccessError
                )
                raise error_type(
                    "Douyin authoritative author-feed quality source was temporarily "
                    "unavailable after automatic retries. The task was paused before "
                    "probing later items; wait briefly and continue the task. No "
                    f"lower-quality fallback was downloaded. Probe details: {details}"
                )
            failures.extend((label, reason) for label, reason, _ in unresolved_direct)

        unique_probes: dict[tuple[Any, ...], dict[str, Any]] = {}
        unsupported_probes: list[dict[str, Any]] = []
        for probe in probes:
            video_codec = str(probe.get("vcodec") or "").lower()
            if video_codec in {"bytevc2", "h266", "vvc"}:
                unsupported_probes.append(probe)
                continue
            signature = self._douyin_probe_media_identity_key(probe)
            existing_probe = unique_probes.get(signature)
            if existing_probe is None:
                unique_probes[signature] = probe
            else:
                self._merge_douyin_probe_source_candidates(
                    existing_probe,
                    probe,
                )

        if unique_probes:
            best_supported_rank = max(
                self._douyin_probe_quality_key(value)
                for value in unique_probes.values()
            )
            blocking_unsupported = [
                value
                for value in unsupported_probes
                if self._douyin_probe_quality_key(value) > best_supported_rank
            ]
        else:
            blocking_unsupported = unsupported_probes
        codec_failures: list[tuple[str, str]] = []
        for probe in blocking_unsupported:
            video_codec = str(probe.get("vcodec") or "").lower()
            codec_failures.append(
                (
                    str(probe.get("requested_ratio") or "unknown"),
                    f"highest candidate uses unsupported video codec {video_codec}",
                )
            )

        if codec_failures:
            info["_douyin_probe_failure"] = self._summarize_douyin_probe_failures(
                codec_failures
            )
            return False
        if not unique_probes:
            info["_douyin_probe_failure"] = (
                self._summarize_douyin_probe_failures(failures)
                if failures
                else "no playable candidate was returned"
            )
            return False

        quality_floor = quality_floor_dimensions(
            [
                {
                    "width": info.get("_douyin_minimum_width"),
                    "height": info.get("_douyin_minimum_height"),
                }
            ],
            cap_full_hd=False,
        )
        best_probe = max(
            unique_probes.values(),
            key=self._douyin_probe_quality_key,
        )
        best_dimensions = (int(best_probe["width"]), int(best_probe["height"]))
        if quality_floor:
            best_short, best_long = sorted(best_dimensions)
            minimum_short, minimum_long = sorted(quality_floor)
            if best_short < minimum_short or best_long < minimum_long:
                info["_douyin_probe_failure"] = (
                    "best verified candidate was "
                    f"{best_dimensions[0]}x{best_dimensions[1]}, below the "
                    "discovered minimum "
                    f"{quality_floor[0]}x{quality_floor[1]}"
                )
                return False
        if failures:
            info["_douyin_probe_failure"] = self._summarize_douyin_probe_failures(
                failures
            )
            return False

        for index, probe in enumerate(unique_probes.values(), start=1):
            width = int(probe["width"])
            height = int(probe["height"])
            bit_rate = int(probe.get("bit_rate") or 0)
            formats.append(
                {
                    "format_id": f"douyin-api-{width}x{height}-{index}",
                    "format_note": "Verified original-quality endpoint",
                    "url": probe["url"],
                    "ext": "mp4",
                    "vcodec": probe.get("vcodec") or "h264",
                    "acodec": probe.get("acodec") or "aac",
                    "width": width,
                    "height": height,
                    "tbr": bit_rate / 1000 if bit_rate else None,
                    "_douyin_probe_bit_rate": bit_rate or None,
                    "filesize": probe.get("filesize"),
                    "duration": probe.get("duration"),
                    "_douyin_probe_prefix_size": probe.get("probe_prefix_size"),
                    "_douyin_probe_prefix_sha256": probe.get("probe_prefix_sha256"),
                    "_douyin_probe_source_url": probe.get("source_url"),
                    "_douyin_probe_source_urls": list(
                        probe.get("source_candidates") or []
                    ),
                    "_douyin_requested_ratio": probe.get("requested_ratio"),
                    "preference": -1,
                    "http_headers": dict(DOUYIN_MEDIA_HEADERS),
                }
            )
        if unique_probes and info.get("_douyin_verified_cache_only"):
            info["formats"] = [
                value
                for value in formats
                if value.get("format_id")
                not in {"profile-cached-play", "item-cached-play"}
            ]
        info.pop("_douyin_probe_failure", None)
        return True
    def _probe_douyin_ratio_with_retry(
        self,
        ydl: YoutubeDL,
        candidate_url: str,
        *,
        ratio: str,
        expected_duration: float | None,
        callback: EventCallback | None,
        should_cancel: CancelCallback,
    ) -> dict[str, Any] | None:
        quality_run = getattr(self._douyin_probe_context, "quality_run", None)
        if isinstance(quality_run, _DouyinQualityProbeRun):
            quality_run.candidate_index += 1
            if callback:
                callback(
                    EngineEvent(
                        event="probing",
                        message=(
                            "Checking Douyin media candidate "
                            f"{quality_run.candidate_index}/"
                            f"{max(quality_run.candidate_total, quality_run.candidate_index)} "
                            f"({ratio})"
                        ),
                    )
                )
        for attempt in range(1, DOUYIN_PROBE_ATTEMPTS + 1):
            try:
                lock_timeout = DOUYIN_PROCESS_POLL_SECONDS
                if isinstance(quality_run, _DouyinQualityProbeRun):
                    lock_timeout = min(
                        lock_timeout,
                        quality_run.budget.remaining(),
                    )
                lock_wait_started_at = time.monotonic()
                last_lock_status_at = (
                    lock_wait_started_at - DOUYIN_LOCK_STATUS_INTERVAL_SECONDS
                )
                while not DOUYIN_MEDIA_PROBE_LOCK.acquire(timeout=lock_timeout):
                    if should_cancel():
                        raise DownloadCancelled("Task cancelled")
                    if isinstance(quality_run, _DouyinQualityProbeRun):
                        lock_timeout = min(
                            DOUYIN_PROCESS_POLL_SECONDS,
                            quality_run.budget.remaining(),
                        )
                    now = time.monotonic()
                    if (
                        callback
                        and now - last_lock_status_at
                        >= DOUYIN_LOCK_STATUS_INTERVAL_SECONDS
                    ):
                        callback(
                            EngineEvent(
                                event="probing",
                                message=(
                                    "Waiting for another Douyin quality check "
                                    f"({int(now - lock_wait_started_at)}s)"
                                ),
                            )
                        )
                        last_lock_status_at = now
                try:
                    self._douyin_probe_context.ratio = ratio
                    self._douyin_probe_context.callback = callback
                    try:
                        probe = self._probe_douyin_candidate(
                            ydl,
                            candidate_url,
                            expected_duration=expected_duration,
                            should_cancel=should_cancel,
                        )
                        if probe and isinstance(quality_run, _DouyinQualityProbeRun):
                            quality_run.budget.refresh()
                        return probe
                    finally:
                        self._douyin_probe_context.ratio = None
                        self._douyin_probe_context.callback = None
                finally:
                    DOUYIN_MEDIA_PROBE_LOCK.release()
            except (
                DownloadCancelled,
                MediaDownloadError,
                _DouyinNoProgressTimeout,
            ):
                raise
            except Exception as exc:
                retryable = self._is_retryable_douyin_probe_error(exc)
                self._close_douyin_probe_error(exc)
                if isinstance(quality_run, _DouyinQualityProbeRun):
                    quality_run.budget.remaining()
                if attempt >= DOUYIN_PROBE_ATTEMPTS or not retryable:
                    raise
                if callback:
                    callback(
                        EngineEvent(
                            event="probing",
                            message=(
                                f"Retrying Douyin quality {ratio} after a "
                                f"temporary network error "
                                f"({attempt + 1}/{DOUYIN_PROBE_ATTEMPTS})"
                            ),
                        )
                    )
                retry_delay = DOUYIN_PROBE_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                if isinstance(quality_run, _DouyinQualityProbeRun):
                    retry_delay = min(
                        retry_delay,
                        quality_run.budget.remaining(),
                    )
                self._wait_for_douyin_probe_retry(retry_delay, should_cancel)
                if isinstance(quality_run, _DouyinQualityProbeRun):
                    quality_run.budget.remaining()
        return None
    @staticmethod
    def _is_retryable_douyin_probe_error(exc: Exception) -> bool:
        if isinstance(exc, _DouyinProbeResponseChanged):
            return True
        if isinstance(exc, _DouyinProbeRejected):
            return False
        if isinstance(exc, HTTPError):
            return exc.status in {
                401,
                403,
                404,
                408,
                410,
                425,
                429,
                500,
                502,
                503,
                504,
            }
        if isinstance(exc, (TransportError, TimeoutError, ConnectionError, OSError)):
            return True
        return False
    @classmethod
    def _should_pause_douyin_probe_error(cls, exc: Exception) -> bool:
        return isinstance(
            exc,
            (
                _DouyinRedirectRejected,
                _DouyinProbeIntegrityChanged,
                _DouyinProbeResponseChanged,
            ),
        ) or (cls._is_retryable_douyin_probe_error(exc))
    @staticmethod
    def _close_douyin_probe_error(exc: Exception) -> None:
        if isinstance(exc, HTTPError):
            with contextlib.suppress(Exception):
                exc.close()
    @staticmethod
    def _wait_for_douyin_probe_retry(
        delay_seconds: float,
        should_cancel: CancelCallback,
    ) -> None:
        deadline = time.monotonic() + max(delay_seconds, 0.0)
        while True:
            if should_cancel():
                raise DownloadCancelled("Task cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(DOUYIN_PROCESS_POLL_SECONDS, remaining))
    @staticmethod
    def _safe_douyin_probe_failure(exc: Exception) -> str:
        if isinstance(exc, _DouyinProbeRejected):
            return str(exc)
        if isinstance(exc, MediaDownloadError) and str(exc).startswith(
            (
                "Incomplete media response:",
                "Media response changed after quality verification:",
                "Highest-available video dimensions could not be verified",
                "Media server returned a video below its declared",
                "Downloaded video content did not match",
                "Downloaded video codec did not match",
                "Downloaded audio codec did not match",
                "Downloaded video size did not match",
                "Downloaded video bitrate was below",
                "Downloaded video duration did not match",
            )
        ):
            return safe_external_error_message(exc)
        if isinstance(exc, CertificateVerifyError):
            return "media TLS certificate validation failed"
        if isinstance(exc, SSLError):
            return "secure media connection failed"
        text = f"{type(exc).__name__} {exc}".lower()
        if "timeout" in text or "timed out" in text:
            return "media request or FFprobe timed out"
        if "ssl" in text or "certificate" in text:
            return "secure media connection failed"
        if any(
            marker in text
            for marker in ("connection", "network", "resolve", "remote end")
        ):
            return "media endpoint network request failed"
        if isinstance(exc, HTTPError):
            return f"media endpoint returned HTTP {exc.status}"
        if (
            type(exc).__name__.lower() == "httperror"
            or "http error" in text
            or "http status" in text
            or "status code" in text
        ):
            return "media endpoint returned an HTTP error"
        return f"media probe failed ({type(exc).__name__})"
    @staticmethod
    def _douyin_probe_failure_requires_refresh(reason: str) -> bool:
        return any(
            marker in reason
            for marker in (
                *_DOUYIN_REDIRECT_ERROR_MARKERS,
                "media endpoint did not return video data",
                "media endpoint did not return an MP4 file",
                "media endpoint returned an empty file",
            )
        )
    @classmethod
    def _preferred_douyin_probe_failure(
        cls,
        errors: list[tuple[str, bool]],
    ) -> str:
        if not errors:
            return "no media URL was available"
        return next(
            (
                reason
                for reason, _ in errors
                if cls._douyin_probe_failure_requires_refresh(reason)
            ),
            errors[-1][0],
        )
    @staticmethod
    def _summarize_douyin_probe_failures(
        failures: list[tuple[str, str]],
    ) -> str:
        if not failures:
            return "no playable candidate was returned"
        grouped: dict[str, list[str]] = {}
        for ratio, reason in failures:
            grouped.setdefault(reason, []).append(ratio)
        return "; ".join(
            f"{','.join(ratios)}: {reason}" for reason, ratios in grouped.items()
        )
    def _probe_douyin_candidate(
        self,
        ydl: YoutubeDL,
        candidate_url: str,
        *,
        ratio: str | None = None,
        expected_duration: float | None,
        callback: EventCallback | None = None,
        should_cancel: CancelCallback,
    ) -> dict[str, Any] | None:
        ratio = (
            ratio
            or getattr(
                self._douyin_probe_context,
                "ratio",
                None,
            )
            or "default"
        )
        callback = callback or getattr(
            self._douyin_probe_context,
            "callback",
            None,
        )
        quality_run = getattr(self._douyin_probe_context, "quality_run", None)
        if should_cancel():
            raise DownloadCancelled("Task cancelled")
        if not self._is_trusted_douyin_asset_url(
            candidate_url,
            MediaType.VIDEO,
        ):
            raise _DouyinProbeRejected(
                "untrusted initial Douyin media endpoint was blocked"
            )
        headers = {
            **DOUYIN_MEDIA_HEADERS,
            "Range": f"bytes=0-{DOUYIN_PROBE_BYTES - 1}",
        }
        request_timeout = DOUYIN_PROBE_HTTP_TIMEOUT_SECONDS
        if isinstance(quality_run, _DouyinQualityProbeRun):
            request_timeout = quality_run.budget.request_timeout(request_timeout)
        response = self._open_douyin_media_response(
            ydl,
            Request(
                candidate_url,
                headers=headers,
                extensions={"timeout": request_timeout},
            ),
            redirect_rejection_reason=lambda value: (
                self._douyin_media_redirect_rejection_reason(
                    value,
                    MediaType.VIDEO,
                    allow_verified_regional=True,
                )
            ),
            should_cancel=should_cancel,
        )
        try:
            final_url = str(response.url)
            redirect_reason = self._douyin_media_redirect_rejection_reason(
                final_url,
                MediaType.VIDEO,
                allow_verified_regional=True,
            )
            if redirect_reason is not None:
                raise self._douyin_redirect_rejection(
                    final_url,
                    redirect_reason,
                )
            content_type = (
                str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
            )
            if content_type and not (
                content_type.startswith("video/")
                or content_type
                in {
                    "application/mp4",
                    "application/octet-stream",
                    "binary/octet-stream",
                }
            ):
                raise _DouyinProbeResponseChanged(
                    "media endpoint did not return video data"
                )
            payload = bytearray()
            last_range_report_at: float | None = None
            while len(payload) < DOUYIN_PROBE_BYTES:
                if should_cancel():
                    raise DownloadCancelled("Task cancelled")
                chunk = self._read_douyin_stream_chunk(
                    response,
                    DOUYIN_PROBE_BYTES - len(payload),
                )
                if not chunk:
                    break
                payload.extend(chunk)
                if isinstance(quality_run, _DouyinQualityProbeRun):
                    quality_run.budget.refresh()
                if callback:
                    now = time.monotonic()
                    if (
                        last_range_report_at is None
                        or now - last_range_report_at
                        >= DOUYIN_PROGRESS_INTERVAL_SECONDS
                    ):
                        candidate_index = (
                            quality_run.candidate_index
                            if isinstance(quality_run, _DouyinQualityProbeRun)
                            else 1
                        )
                        candidate_total = (
                            max(quality_run.candidate_total, candidate_index)
                            if isinstance(quality_run, _DouyinQualityProbeRun)
                            else 1
                        )
                        callback(
                            EngineEvent(
                                event="probing",
                                message=(
                                    "Reading Douyin quality candidate "
                                    f"{candidate_index}/{candidate_total} ({ratio})"
                                ),
                                progress=TransferProgress(
                                    downloaded_bytes=len(payload),
                                    total_bytes=None,
                                ),
                            )
                        )
                        last_range_report_at = now
            if len(payload) < 12 or payload[4:8] != b"ftyp":
                raise _DouyinProbeResponseChanged(
                    "media endpoint did not return an MP4 file"
                )
            content_range = str(response.headers.get("Content-Range") or "")
            size_match = re.search(r"/(\d+)$", content_range)
            filesize = int(size_match.group(1)) if size_match else None
            content_length = str(response.headers.get("Content-Length") or "")
            try:
                response_status = int(getattr(response, "status", 200) or 200)
            except (TypeError, ValueError):
                response_status = 200
            if (
                filesize is None
                and response_status != 206
                and content_length.isdigit()
                and int(content_length) >= len(payload)
            ):
                filesize = int(content_length)
        finally:
            with contextlib.suppress(Exception):
                response.close()

        if should_cancel():
            raise DownloadCancelled("Task cancelled")
        media = self._ffprobe_douyin_media(
            bytes(payload),
            should_cancel=should_cancel,
        )
        local_path: Path | None = None
        temporary_directory = None
        reuse_registered = False
        try:
            prefix_duration = (
                self._float_or_none(media.get("duration")) if media else None
            )
            # A fragmented MP4 prefix can report a positive fragment duration.
            # Confirm the complete, byte-bound file before rejecting its identity.
            confirm_full_duration = (
                expected_duration is not None
                and prefix_duration is not None
                and abs(prefix_duration - expected_duration)
                > self._douyin_duration_tolerance(expected_duration)
                and (filesize is None or filesize > len(payload))
            )
            if confirm_full_duration or not self._douyin_probe_metadata_complete(
                media, filesize=filesize
            ):
                reuse = getattr(self._douyin_probe_context, "reuse", None)
                if isinstance(reuse, _DouyinProbeReuseContext):
                    temporary_fd, temporary_name = tempfile.mkstemp(
                        prefix="candidate-",
                        suffix=".mp4",
                        dir=reuse.directory,
                    )
                    os.close(temporary_fd)
                    local_path = Path(temporary_name)
                else:
                    temporary_directory = tempfile.TemporaryDirectory(
                        prefix="original-media-douyin-probe-"
                    )
                    local_path = Path(temporary_directory.name) / "candidate.mp4"
                self._download_douyin_probe_file(
                    ydl,
                    candidate_url,
                    local_path,
                    ratio=ratio,
                    expected_prefix=bytes(payload),
                    expected_filesize=filesize,
                    callback=callback,
                    should_cancel=should_cancel,
                )
                media = self._ffprobe_douyin_media(
                    bytes(payload),
                    local_path=local_path,
                    should_cancel=should_cancel,
                )
            if not media:
                raise _DouyinProbeRejected("FFprobe could not parse the media stream")
            width = int(media.get("width") or 0)
            height = int(media.get("height") or 0)
            if width <= 0 or height <= 0:
                raise _DouyinProbeRejected("FFprobe returned no video dimensions")
            duration = self._float_or_none(media.get("duration"))
            if duration is None or duration <= 0:
                raise _DouyinProbeRejected("FFprobe returned no media duration")
            if expected_duration is not None:
                tolerance = self._douyin_duration_tolerance(expected_duration)
                if abs(duration - expected_duration) > tolerance:
                    raise _DouyinProbeRejected(
                        "media duration did not match the requested Douyin item "
                        f"(expected {expected_duration:.3f}s, "
                        f"measured {duration:.3f}s, tolerance {tolerance:.3f}s)"
                    )
            bit_rate = int(media.get("bit_rate") or 0)
            if bit_rate <= 0 and filesize and duration and duration > 0:
                bit_rate = int(filesize * 8 / duration)
            if not filesize and bit_rate <= 0:
                raise _DouyinProbeRejected(
                    "FFprobe returned no bitrate or complete media size"
                )
            result = {
                **media,
                "url": final_url,
                "filesize": filesize,
                "bit_rate": bit_rate,
                "probe_prefix_size": len(payload),
                "probe_prefix_sha256": hashlib.sha256(payload).hexdigest(),
                "source_url": candidate_url,
            }
            if local_path is not None:
                reuse_registered = self._register_douyin_probe_file(
                    local_path,
                    result,
                )
            return result
        finally:
            if temporary_directory is not None:
                temporary_directory.cleanup()
            elif local_path is not None and not reuse_registered:
                local_path.unlink(missing_ok=True)
    @staticmethod
    def _douyin_duration_tolerance(duration: float) -> float:
        return max(0.5, min(2.0, duration * 0.01))
    def _download_douyin_probe_file(
        self,
        ydl: YoutubeDL,
        candidate_url: str,
        path: Path,
        *,
        ratio: str = "default",
        expected_prefix: bytes,
        expected_filesize: int | None,
        callback: EventCallback | None = None,
        should_cancel: CancelCallback,
    ) -> None:
        if should_cancel():
            raise DownloadCancelled("Task cancelled")
        if not self._is_trusted_douyin_asset_url(
            candidate_url,
            MediaType.VIDEO,
        ):
            raise _DouyinProbeRejected(
                "untrusted initial Douyin media endpoint was blocked"
            )
        if (
            expected_filesize is not None
            and expected_filesize > DOUYIN_MAX_PROBE_FILE_BYTES
        ):
            raise _DouyinProbeRejected("media file exceeded the safe probe size limit")
        quality_run = getattr(self._douyin_probe_context, "quality_run", None)
        request_timeout = DOUYIN_PROBE_HTTP_TIMEOUT_SECONDS
        if isinstance(quality_run, _DouyinQualityProbeRun):
            request_timeout = quality_run.budget.request_timeout(request_timeout)
        response = self._open_douyin_media_response(
            ydl,
            Request(
                candidate_url,
                headers=dict(DOUYIN_MEDIA_HEADERS),
                extensions={"timeout": request_timeout},
            ),
            redirect_rejection_reason=lambda value: (
                self._douyin_media_redirect_rejection_reason(
                    value,
                    MediaType.VIDEO,
                    allow_verified_regional=True,
                )
            ),
            should_cancel=should_cancel,
        )
        downloaded = 0
        prefix = bytearray()
        started_at = time.monotonic()
        last_report_at = 0.0

        def report_progress(*, force: bool = False) -> None:
            nonlocal last_report_at
            if not callback:
                return
            now = time.monotonic()
            if not force and now - last_report_at < DOUYIN_PROGRESS_INTERVAL_SECONDS:
                return
            elapsed = max(now - started_at, 0.001)
            speed = downloaded / elapsed if downloaded else None
            eta = (
                max(0.0, (expected_filesize - downloaded) / speed)
                if expected_filesize is not None and speed is not None and speed > 0
                else None
            )
            percent = (
                min(100.0, downloaded * 100.0 / expected_filesize)
                if expected_filesize
                else None
            )
            callback(
                EngineEvent(
                    event="probing",
                    message=(
                        "Reading the Douyin original file to verify quality "
                        f"({ratio})"
                    ),
                    progress=TransferProgress(
                        downloaded_bytes=downloaded,
                        total_bytes=expected_filesize,
                        percent=percent,
                        speed_bytes_per_second=speed,
                        eta_seconds=eta,
                    ),
                )
            )
            last_report_at = now

        report_progress(force=True)
        try:
            final_url = str(response.url)
            redirect_reason = self._douyin_media_redirect_rejection_reason(
                final_url,
                MediaType.VIDEO,
                allow_verified_regional=True,
            )
            if redirect_reason is not None:
                raise self._douyin_redirect_rejection(
                    final_url,
                    redirect_reason,
                )
            content_type = (
                str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
            )
            if content_type and not (
                content_type.startswith("video/")
                or content_type
                in {
                    "application/mp4",
                    "application/octet-stream",
                    "binary/octet-stream",
                }
            ):
                raise _DouyinProbeResponseChanged(
                    "media endpoint did not return video data"
                )
            declared_length = str(response.headers.get("Content-Length") or "")
            if declared_length.isdigit():
                declared_size = int(declared_length)
                if declared_size > DOUYIN_MAX_PROBE_FILE_BYTES:
                    raise _DouyinProbeRejected(
                        "media file exceeded the safe probe size limit"
                    )
                if expected_filesize is not None and declared_size != expected_filesize:
                    raise _DouyinProbeIntegrityChanged(
                        "media size changed between the range probe and local probe"
                    )
            with path.open("wb") as output:
                while True:
                    if should_cancel():
                        raise DownloadCancelled("Task cancelled")
                    chunk = self._read_douyin_stream_chunk(
                        response,
                        DOUYIN_STREAM_CHUNK_BYTES,
                    )
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if isinstance(quality_run, _DouyinQualityProbeRun):
                        quality_run.budget.refresh()
                    if downloaded > DOUYIN_MAX_PROBE_FILE_BYTES:
                        raise _DouyinProbeRejected(
                            "media file exceeded the safe probe size limit"
                        )
                    if expected_filesize is not None and downloaded > expected_filesize:
                        raise _DouyinProbeIntegrityChanged(
                            "media size changed between the range probe and local probe"
                        )
                    if len(prefix) < len(expected_prefix):
                        remaining = len(expected_prefix) - len(prefix)
                        prefix.extend(chunk[:remaining])
                    output.write(chunk)
                    report_progress()
                output.flush()
                os.fsync(output.fileno())
        finally:
            with contextlib.suppress(Exception):
                response.close()
        if downloaded <= 0:
            raise _DouyinProbeResponseChanged("media endpoint returned an empty file")
        if expected_filesize is not None and downloaded != expected_filesize:
            raise _DouyinProbeIntegrityChanged(
                "media size changed between the range probe and local probe"
            )
        if bytes(prefix) != expected_prefix:
            raise _DouyinProbeIntegrityChanged(
                "media content changed between the range probe and local probe"
            )
        report_progress(force=True)
    @staticmethod
    def _read_douyin_stream_chunk(response: Any, size: int) -> bytes:
        if isinstance(response, RequestsResponseAdapter):
            read_available = getattr(response.fp, "read1", None)
            if callable(read_available):
                try:
                    data = read_available(size, decode_content=True)
                    if response.fp.closed:
                        response.close()
                    return data
                except urllib3.exceptions.SSLError as exc:
                    raise SSLError(cause=exc) from exc
                except urllib3.exceptions.ProtocolError as exc:
                    incomplete_read = next(
                        (
                            error
                            for error in (
                                exc.__context__,
                                exc.__cause__,
                                *variadic(exc.args),
                            )
                            if isinstance(error, http.client.IncompleteRead)
                        ),
                        None,
                    )
                    if incomplete_read is not None:
                        partial = incomplete_read.partial
                        if not isinstance(partial, int):
                            partial = len(partial)
                        raise IncompleteRead(
                            partial=partial,
                            expected=incomplete_read.expected,
                        ) from exc
                    raise TransportError(cause=exc) from exc
                except urllib3.exceptions.HTTPError as exc:
                    raise TransportError(cause=exc) from exc
        return response.read(size)
    def _ffprobe_douyin_media(
        self,
        initial_bytes: bytes,
        *,
        local_path: Path | None = None,
        should_cancel: CancelCallback,
    ) -> dict[str, Any] | None:
        executable = self._find_ffprobe_executable()
        if not executable:
            raise MediaDownloadError(FFPROBE_REQUIRED_MESSAGE)
        entries = (
            "stream=codec_type,codec_name,width,height,bit_rate,duration:"
            "format=duration,bit_rate,size"
        )
        base_command = [
            executable,
            "-v",
            "error",
            "-show_entries",
            entries,
            "-of",
            "json",
        ]
        if local_path is not None:
            payload = self._run_ffprobe(
                [*base_command, "-i", str(local_path)],
                timeout_seconds=DOUYIN_FFPROBE_FILE_TIMEOUT_SECONDS,
                should_cancel=should_cancel,
            )
            return self._parse_ffprobe_payload(payload)
        try:
            payload = self._run_ffprobe(
                [*base_command, "-i", "pipe:0"],
                input_data=initial_bytes,
                timeout_seconds=DOUYIN_FFPROBE_PIPE_TIMEOUT_SECONDS,
                should_cancel=should_cancel,
            )
        except TimeoutError:
            payload = None
        return self._parse_ffprobe_payload(payload)
    @classmethod
    def _douyin_probe_metadata_complete(
        cls,
        media: dict[str, Any] | None,
        *,
        filesize: int | None = None,
    ) -> bool:
        if not media:
            return False
        return (
            int(media.get("width") or 0) > 0
            and int(media.get("height") or 0) > 0
            and (cls._float_or_none(media.get("duration")) or 0) > 0
            and (int(media.get("bit_rate") or 0) > 0 or (filesize or 0) > 0)
        )
    @staticmethod
    def _find_ffprobe_executable() -> str | None:
        executable = shutil.which("ffprobe")
        if executable:
            return executable
        for value in FFPROBE_FALLBACK_PATHS:
            candidate = Path(value)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        return None
    @staticmethod
    def _find_ffmpeg_executable() -> str | None:
        executable = shutil.which("ffmpeg")
        if executable:
            return executable
        for value in FFMPEG_FALLBACK_PATHS:
            candidate = Path(value)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        return None
    def _run_ffprobe(
        self,
        command: list[str],
        *,
        input_data: bytes | None = None,
        timeout_seconds: float,
        should_cancel: CancelCallback,
    ) -> bytes | None:
        with contextlib.ExitStack() as cleanup:
            try:
                stdin = subprocess.DEVNULL
                if input_data is not None:
                    # A timed-out communicate(input=...) may leave bytes unwritten;
                    # retrying with input=None does not resume stdin on Python 3.13.
                    # An anonymous input file keeps output polling cancellable without
                    # a writer thread, private subprocess state or a longer deadline.
                    stdin = cleanup.enter_context(tempfile.TemporaryFile())
                    stdin.write(input_data)
                    stdin.seek(0)
                process = subprocess.Popen(
                    command,
                    stdin=stdin,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                raise MediaDownloadError(FFPROBE_START_MESSAGE) from exc

            deadline = time.monotonic() + max(0.1, timeout_seconds)
            try:
                while True:
                    if should_cancel():
                        raise DownloadCancelled("Task cancelled")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("FFprobe timed out while reading media")
                    try:
                        stdout, _ = process.communicate(
                            timeout=min(DOUYIN_PROCESS_POLL_SECONDS, remaining),
                        )
                        return stdout or None
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    self._terminate_process(process)
                if process.stdout is not None:
                    process.stdout.close()
    @staticmethod
    def _terminate_process(process: subprocess.Popen[bytes]) -> None:
        with contextlib.suppress(OSError):
            process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                process.kill()
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                process.wait(timeout=0.5)
    @staticmethod
    def _parse_ffprobe_payload(payload: bytes | None) -> dict[str, Any] | None:
        if not payload:
            return None
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        streams = data.get("streams") or []
        video = next(
            (
                stream
                for stream in streams
                if isinstance(stream, dict) and stream.get("codec_type") == "video"
            ),
            None,
        )
        if not video:
            return None
        audio = next(
            (
                stream
                for stream in streams
                if isinstance(stream, dict) and stream.get("codec_type") == "audio"
            ),
            None,
        )
        format_data = data.get("format") or {}

        def safe_int(value: Any) -> int:
            try:
                parsed = int(value or 0)
            except (TypeError, ValueError):
                return 0
            return parsed if parsed > 0 else 0

        def safe_number(*values: Any) -> Any | None:
            for value in values:
                try:
                    parsed = float(value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(parsed) and parsed > 0:
                    return value
            return None

        return {
            "width": safe_int(video.get("width")),
            "height": safe_int(video.get("height")),
            "vcodec": video.get("codec_name"),
            "acodec": audio.get("codec_name") if audio else "none",
            "bit_rate": safe_int(
                safe_number(video.get("bit_rate"), format_data.get("bit_rate"))
            ),
            "duration": safe_number(video.get("duration"), format_data.get("duration")),
        }
    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            parsed = float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
        return parsed if parsed is not None and math.isfinite(parsed) else None
    @staticmethod
    def _highest_verified_douyin_format(
        info: dict[str, Any],
    ) -> dict[str, Any] | None:
        verified = [
            value
            for value in info.get("formats") or []
            if isinstance(value, dict)
            and str(value.get("format_id") or "").startswith("douyin-api-")
            and isinstance(value.get("url"), str)
        ]
        return max(
            verified,
            key=lambda value: (
                int(value.get("width") or 0) * int(value.get("height") or 0),
                int(
                    value.get("_douyin_probe_bit_rate")
                    or round(float(value.get("tbr") or 0) * 1_000)
                ),
                int(value.get("filesize") or 0),
                str(value.get("_douyin_requested_ratio") or "") == "default",
            ),
            default=None,
        )
    def _select_highest_douyin_live_photo_asset(
        self,
        ydl: YoutubeDL,
        asset: RemoteAsset,
        *,
        callback: EventCallback | None,
        should_cancel: CancelCallback,
    ) -> RemoteAsset:
        with self._douyin_quality_probe_scope():
            return self._select_highest_douyin_live_photo_asset_scoped(
                ydl,
                asset,
                callback=callback,
                should_cancel=should_cancel,
            )
    def _select_highest_douyin_live_photo_asset_scoped(
        self,
        ydl: YoutubeDL,
        asset: RemoteAsset,
        *,
        callback: EventCallback | None,
        should_cancel: CancelCallback,
    ) -> RemoteAsset:
        video_uri = str(asset.video_uri or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", video_uri):
            raise MediaDownloadError("Douyin Live Photo has no verified media identity")

        if not asset.quality_candidates:
            raise TemporaryAccessError(
                "Douyin Live Photo has no structured author-feed quality "
                "renditions. The task was paused instead of downloading a "
                "default-only fallback. Create a new task from the original profile."
            )
        renditions = asset.quality_candidates
        quality_run = getattr(self._douyin_probe_context, "quality_run", None)
        if isinstance(quality_run, _DouyinQualityProbeRun):
            quality_run.candidate_index = 0
            quality_run.candidate_total = sum(
                len(rendition.get("urls") or [])
                for rendition in renditions
                if isinstance(rendition, dict)
            ) + len(self._douyin_default_probe_urls(video_uri))
        direct_probes: list[dict[str, Any]] = []
        direct_failures: list[_DouyinDirectProbeFailure] = []
        probe_count = len(renditions) + 1
        for rendition_index, rendition in enumerate(renditions, start=1):
            if should_cancel():
                raise DownloadCancelledError("Task cancelled")
            label = f"author-feed-{rendition_index}"
            if callback:
                callback(
                    EngineEvent(
                        event="probing",
                        message=(
                            "Checking Douyin Live Photo quality "
                            f"{rendition_index}/{probe_count}: {label}"
                        ),
                    )
                )
            try:
                direct_probe, failure = self._probe_douyin_direct_rendition(
                    ydl,
                    rendition,
                    label=label,
                    expected_duration=asset.duration,
                    callback=callback,
                    should_cancel=should_cancel,
                )
            except DownloadCancelled as exc:
                raise DownloadCancelledError("Task cancelled") from exc
            if direct_probe:
                direct_probes.append(direct_probe)
            elif failure:
                direct_failures.append(failure)

        if callback:
            callback(
                EngineEvent(
                    event="probing",
                    message=(
                        "Checking Douyin Live Photo quality "
                        f"{probe_count}/{probe_count}: default"
                    ),
                )
            )
        try:
            default_probe = self._probe_douyin_default_with_fallback(
                ydl,
                video_uri,
                expected_duration=asset.duration,
                callback=callback,
                should_cancel=should_cancel,
            )
        except _DouyinNoProgressTimeout:
            raise
        except DownloadCancelled as exc:
            raise DownloadCancelledError("Task cancelled") from exc
        except MediaDownloadError:
            raise
        except Exception as exc:
            reason = self._safe_douyin_probe_failure(exc)
            if self._should_pause_douyin_probe_error(exc):
                error_type = (
                    DouyinMediaRefreshRequiredError
                    if isinstance(
                        exc,
                        (_DouyinRedirectRejected, _DouyinProbeResponseChanged),
                    )
                    else TemporaryAccessError
                )
                raise error_type(
                    "Douyin Live Photo authoritative quality source was temporarily "
                    "unavailable after automatic retries. The task was paused before "
                    "probing later items; wait briefly and continue the task. No "
                    "lower-quality fallback was downloaded. Probe details: "
                    f"default: {reason}"
                ) from exc
            raise MediaDownloadError(
                "Douyin Live Photo default original-quality source could not be "
                f"verified. Probe details: default: {reason}"
            ) from exc
        if not default_probe:
            raise MediaDownloadError(
                "Douyin Live Photo default original-quality source returned no "
                "playable media metadata"
            )
        default_probe["requested_ratio"] = "default"
        probes = [*direct_probes, default_probe]

        unresolved = self._unresolved_douyin_direct_failures(
            direct_failures,
            probes,
        )
        if unresolved:
            details = self._summarize_douyin_probe_failures(
                [(label, reason) for label, reason, _ in unresolved]
            )
            if any(transient for _, _, transient in unresolved):
                error_type = (
                    DouyinMediaRefreshRequiredError
                    if any(
                        self._douyin_probe_failure_requires_refresh(reason)
                        for _, reason, _ in unresolved
                    )
                    else TemporaryAccessError
                )
                raise error_type(
                    "Douyin Live Photo authoritative quality source was temporarily "
                    "unavailable after automatic retries. The task was paused before "
                    "probing later items; wait briefly and continue the task. No "
                    f"lower-quality fallback was downloaded. Probe details: {details}"
                )
            raise MediaDownloadError(
                "Douyin Live Photo author-feed quality source could not be verified. "
                f"Probe details: {details}"
            )

        supported: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []
        for probe in probes:
            if str(probe.get("vcodec") or "").lower() in {
                "bytevc2",
                "h266",
                "vvc",
            }:
                unsupported.append(probe)
            else:
                supported.append(probe)
        if not supported:
            raise MediaDownloadError(
                "Douyin Live Photo returned no supported playable candidate",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )
        best = max(
            supported,
            key=lambda value: (
                *self._douyin_probe_quality_key(value),
                str(value.get("requested_ratio") or "") == "default",
            ),
        )
        best_identity = self._douyin_probe_media_identity_key(best)
        for probe in supported:
            if (
                probe is not best
                and self._douyin_probe_media_identity_key(probe) == best_identity
            ):
                self._merge_douyin_probe_source_candidates(best, probe)
        best_rank = self._douyin_probe_quality_key(best)
        if any(
            self._douyin_probe_quality_key(value) > best_rank for value in unsupported
        ):
            raise MediaDownloadError(
                "Douyin Live Photo highest candidate uses an unsupported video codec",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )

        actual_width = int(best.get("width") or 0)
        actual_height = int(best.get("height") or 0)
        declared_too_high = bool(
            asset.width
            and asset.height
            and (
                min(actual_width, actual_height) < min(asset.width, asset.height)
                or max(actual_width, actual_height) < max(asset.width, asset.height)
            )
        )
        if actual_width <= 0 or actual_height <= 0 or declared_too_high:
            raise MediaDownloadError(
                "Douyin Live Photo best verified candidate was below its declared "
                f"{asset.width}x{asset.height} resolution"
            )
        requested_ratio = safe_component(
            str(best.get("requested_ratio") or "verified"),
            fallback="verified",
        )
        redirect_source_url = str(best.get("source_url") or "").strip()
        best_urls = [
            value
            for value in (
                redirect_source_url,
                str(best["url"]),
                *(best.get("source_candidates") or []),
            )
            if isinstance(value, str) and value
        ]
        if requested_ratio in DOUYIN_PROBE_RATIOS:
            if requested_ratio == "default":
                best_urls.extend(self._douyin_default_probe_urls(video_uri))
            else:
                best_urls.append(self._douyin_ratio_url(video_uri, requested_ratio))
        best_urls = self._bounded_douyin_transfer_candidates(best_urls)
        return RemoteAsset(
            candidates=best_urls,
            index=asset.index,
            width=actual_width,
            height=actual_height,
            size=(int(best["filesize"]) if best.get("filesize") else None),
            format_id=(
                "douyin-highest-live-photo-"
                f"{requested_ratio}-{actual_width}x{actual_height}"
            ),
            video_uri=video_uri,
            duration=self._float_or_none(best.get("duration")) or asset.duration,
            bit_rate=(int(best["bit_rate"]) if best.get("bit_rate") else None),
            video_codec=str(best.get("vcodec") or "").lower() or None,
            audio_codec=str(best.get("acodec") or "").lower() or None,
            probe_prefix_size=int(best.get("probe_prefix_size") or 0) or None,
            probe_prefix_sha256=str(best.get("probe_prefix_sha256") or "") or None,
            redirect_source_url=redirect_source_url or None,
        )
    @staticmethod
    def _douyin_probe_quality_key(value: dict[str, Any]) -> tuple[int, int, int]:
        return (
            int(value.get("width") or 0) * int(value.get("height") or 0),
            int(value.get("bit_rate") or 0),
            int(value.get("filesize") or 0),
        )
    @classmethod
    def _douyin_probe_media_identity_key(
        cls,
        value: dict[str, Any],
    ) -> tuple[Any, ...]:
        duration = cls._float_or_none(value.get("duration"))
        return (
            int(value.get("width") or 0),
            int(value.get("height") or 0),
            str(value.get("vcodec") or "").lower(),
            str(value.get("acodec") or "").lower(),
            int(value.get("bit_rate") or 0),
            int(value.get("filesize") or 0),
            round(duration, 6) if duration is not None else None,
            int(value.get("probe_prefix_size") or 0),
            str(value.get("probe_prefix_sha256") or "").lower(),
        )
    @classmethod
    def _douyin_asset_media_identity_key(
        cls,
        asset: RemoteAsset,
    ) -> tuple[Any, ...]:
        return cls._douyin_probe_media_identity_key(
            {
                "width": asset.width,
                "height": asset.height,
                "vcodec": asset.video_codec,
                "acodec": asset.audio_codec,
                "bit_rate": asset.bit_rate,
                "filesize": asset.size,
                "duration": asset.duration,
                "probe_prefix_size": asset.probe_prefix_size,
                "probe_prefix_sha256": asset.probe_prefix_sha256,
            }
        )
    def _register_douyin_probe_file(
        self,
        path: Path,
        probe: dict[str, Any],
    ) -> bool:
        reuse = getattr(self._douyin_probe_context, "reuse", None)
        if not isinstance(reuse, _DouyinProbeReuseContext):
            return False
        try:
            resolved = path.resolve(strict=True)
            if (
                path.is_symlink()
                or not resolved.is_file()
                or resolved.parent != reuse.directory
            ):
                return False
        except OSError:
            return False
        key = self._douyin_probe_media_identity_key(probe)
        reuse.files.setdefault(key, []).append(resolved)
        return True
    def _take_douyin_probe_file(self, asset: RemoteAsset) -> Path | None:
        reuse = getattr(self._douyin_probe_context, "reuse", None)
        if not isinstance(reuse, _DouyinProbeReuseContext):
            return None
        key = self._douyin_asset_media_identity_key(asset)
        candidates = reuse.files.get(key)
        while candidates:
            candidate = candidates.pop(0)
            try:
                resolved = candidate.resolve(strict=True)
                if (
                    candidate.is_symlink()
                    or not resolved.is_file()
                    or resolved.parent != reuse.directory
                ):
                    continue
            except OSError:
                continue
            if not candidates:
                reuse.files.pop(key, None)
            return resolved
        reuse.files.pop(key, None)
        return None
    @classmethod
    def _merge_douyin_probe_source_candidates(
        cls,
        target: dict[str, Any],
        source: dict[str, Any],
    ) -> None:
        candidates = [
            value
            for value in (
                str(target.get("source_url") or ""),
                str(target.get("url") or ""),
                *(target.get("source_candidates") or []),
                str(source.get("source_url") or ""),
                str(source.get("url") or ""),
                *(source.get("source_candidates") or []),
            )
            if isinstance(value, str)
            and value
            and (
                cls._is_trusted_douyin_asset_url(value, MediaType.VIDEO)
                or cls._is_douyin_regional_media_host(cls._strict_https_hostname(value))
            )
        ]
        target["source_candidates"] = list(dict.fromkeys(candidates))
    @staticmethod
    def _bounded_douyin_transfer_candidates(candidates: Iterable[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, str) or not candidate:
                continue
            try:
                parsed = urlsplit(candidate)
                identity = urlunsplit(
                    (
                        parsed.scheme.lower(),
                        parsed.netloc.lower(),
                        parsed.path,
                        parsed.query,
                        "",
                    )
                )
            except (TypeError, ValueError):
                identity = candidate
            if identity in seen:
                continue
            seen.add(identity)
            result.append(candidate)
            if len(result) >= DOUYIN_MAX_TRANSFER_CANDIDATES:
                break
        return result
    def _probe_douyin_direct_rendition(
        self,
        ydl: YoutubeDL,
        rendition: dict[str, Any],
        *,
        label: str,
        expected_duration: float | None,
        callback: EventCallback | None,
        should_cancel: CancelCallback,
    ) -> tuple[dict[str, Any] | None, _DouyinDirectProbeFailure | None]:
        """Try same-rendition mirrors until one meets the declared dimensions."""
        urls = list(dict.fromkeys(rendition.get("urls") or []))
        declared_width = int(rendition.get("width") or 0)
        declared_height = int(rendition.get("height") or 0)
        errors: list[tuple[str, bool]] = []
        lower_errors: list[tuple[str, bool]] = []
        lower_urls: set[str] = set()
        for candidate_url in urls:
            if should_cancel():
                raise DownloadCancelled("Task cancelled")
            try:
                probe = self._probe_douyin_ratio_with_retry(
                    ydl,
                    candidate_url,
                    ratio=label,
                    expected_duration=expected_duration,
                    callback=callback,
                    should_cancel=should_cancel,
                )
            except (_DouyinNoProgressTimeout, DownloadCancelled, MediaDownloadError):
                raise
            except Exception as exc:
                errors.append(
                    (
                        self._safe_douyin_probe_failure(exc),
                        self._should_pause_douyin_probe_error(exc),
                    )
                )
                continue
            if should_cancel():
                raise DownloadCancelled("Task cancelled")
            actual_width = int(probe.get("width") or 0) if probe else 0
            actual_height = int(probe.get("height") or 0) if probe else 0
            if actual_width <= 0 or actual_height <= 0:
                errors.append(("media metadata could not be parsed", False))
                continue
            if min(actual_width, actual_height) < min(
                declared_width, declared_height
            ) or max(actual_width, actual_height) < max(
                declared_width, declared_height
            ):
                lower_urls.add(candidate_url)
                lower_errors.append(
                    (
                        "verified media was below the author-feed "
                        f"{declared_width}x{declared_height} rendition "
                        f"(measured {actual_width}x{actual_height})",
                        False,
                    )
                )
                continue
            probe["requested_ratio"] = label
            # Keep untried/temporarily unavailable mirrors for the fully checked
            # transfer, but never re-add a mirror already measured as too small.
            probe["source_candidates"] = [
                value
                for value in dict.fromkeys(
                    [str(probe.get("source_url") or ""), candidate_url, *urls]
                )
                if value
                and value not in lower_urls
                and self._is_trusted_douyin_asset_url(value, MediaType.VIDEO)
            ]
            return probe, None

        # A failed/empty mirror is unknown, not evidence of a lower rendition.
        # This distinction must survive aggregation across author-feed entries.
        return None, _DouyinDirectProbeFailure(
            rendition=rendition,
            label=label,
            reason=self._preferred_douyin_probe_failure(errors or lower_errors),
            transient=any(transient for _, transient in errors),
            all_sources_verified_lower=bool(urls) and len(lower_urls) == len(urls),
        )
    @classmethod
    def _unresolved_douyin_direct_failures(
        cls,
        failures: list[_DouyinDirectProbeFailure],
        verified_probes: list[dict[str, Any]],
    ) -> list[tuple[str, str, bool]]:
        best = max(
            verified_probes,
            key=cls._douyin_probe_quality_key,
            default=None,
        )
        best_pixels = (
            int(best.get("width") or 0) * int(best.get("height") or 0) if best else 0
        )
        best_bit_rate = int(best.get("bit_rate") or 0) if best else 0
        unresolved: list[tuple[str, str, bool]] = []
        for failure in failures:
            rendition = failure.rendition
            declared_pixels = int(rendition.get("width") or 0) * int(
                rendition.get("height") or 0
            )
            declared_bit_rate = int(rendition.get("bit_rate") or 0)
            dominated = best_pixels > declared_pixels or (
                best_pixels == declared_pixels
                and (
                    (declared_bit_rate > 0 and best_bit_rate >= declared_bit_rate)
                    or (declared_bit_rate == 0 and failure.all_sources_verified_lower)
                )
            )
            # Pixel count alone cannot cover a missing short or long edge.
            if best:
                actual_edges = sorted(
                    (int(best.get("width") or 0), int(best.get("height") or 0))
                )
                declared_edges = sorted(
                    (
                        int(rendition.get("width") or 0),
                        int(rendition.get("height") or 0),
                    )
                )
                dominated = dominated and all(
                    actual >= declared
                    for actual, declared in zip(actual_edges, declared_edges)
                )
            if not dominated:
                unresolved.append((failure.label, failure.reason, failure.transient))
        return unresolved
    def _reuse_verified_douyin_probe_file(
        self,
        probe_path: Path,
        asset: RemoteAsset,
        output_dir: Path,
        upload_date: str | None,
        title: str,
        media_id: str,
        *,
        callback: EventCallback | None,
        should_cancel: CancelCallback,
        asset_index: int | None,
        progress_index: int | None,
        progress_count: int | None,
        paired_stem: str | None = None,
        compact_names: bool = False,
    ) -> tuple[Path, RemoteAsset]:
        if should_cancel():
            raise DownloadCancelledError("Task cancelled")
        try:
            file_size = probe_path.stat().st_size
        except OSError as exc:
            raise MediaDownloadError(
                "The verified Douyin probe file could not be read"
            ) from exc
        path = self._xhs_output_path(
            output_dir,
            upload_date,
            title,
            media_id,
            "mp4",
            asset_index,
            compact_names=compact_names,
        )

        def progress_percent(completed: bool) -> float:
            fraction = 1.0 if completed else 0.0
            if (
                progress_index is not None
                and progress_count is not None
                and 0 < progress_index <= progress_count
            ):
                return min(
                    100.0,
                    ((progress_index - 1) + fraction) * 100.0 / progress_count,
                )
            return fraction * 100.0

        if callback:
            callback(
                EngineEvent(
                    event="downloading",
                    progress=TransferProgress(
                        downloaded_bytes=0,
                        total_bytes=file_size,
                        percent=progress_percent(False),
                        fragment_index=progress_index,
                        fragment_count=progress_count,
                        filename=str(path),
                    ),
                )
            )
        started_at = time.monotonic()
        try:
            chosen = self._verify_local_video_asset(
                probe_path,
                asset,
                should_cancel=should_cancel,
                require_quality_fingerprint=True,
            )
            if should_cancel():
                raise DownloadCancelledError("Task cancelled")
            if paired_stem is None:
                path = publish_unique(probe_path, path)
            else:
                path = path.with_name(paired_stem + path.suffix)
                publish_no_replace(probe_path, path)
        except BaseException:
            probe_path.unlink(missing_ok=True)
            raise
        if callback:
            elapsed = max(time.monotonic() - started_at, 0.001)
            callback(
                EngineEvent(
                    event="downloading",
                    progress=TransferProgress(
                        downloaded_bytes=file_size,
                        total_bytes=file_size,
                        percent=progress_percent(True),
                        speed_bytes_per_second=file_size / elapsed,
                        eta_seconds=0.0,
                        fragment_index=progress_index,
                        fragment_count=progress_count,
                        filename=str(path),
                    ),
                )
            )
        return path.resolve(), chosen
    @staticmethod
    def _is_retryable_douyin_transfer_error(exc: Exception) -> bool:
        if isinstance(exc, HTTPError):
            return exc.status in {
                401,
                403,
                404,
                408,
                410,
                425,
                429,
                500,
                502,
                503,
                504,
            }
        if isinstance(exc, MediaDownloadError):
            return str(exc).startswith(
                (
                    "Incomplete media response:",
                    "Media response changed after quality verification:",
                    "Highest-available video dimensions could not be verified",
                    "Media server returned a video below its declared",
                    "Downloaded video content did not match",
                    "Downloaded video codec did not match",
                    "Downloaded audio codec did not match",
                    "Downloaded video size did not match",
                    "Downloaded video bitrate was below",
                    "Downloaded video duration did not match",
                )
            )
        return isinstance(exc, (TransportError, TimeoutError, ConnectionError))
