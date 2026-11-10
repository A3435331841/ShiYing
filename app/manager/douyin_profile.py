"""Auto-split from the original task manager module."""
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

from .common import *  # noqa: F401,F403
from .xiaohongshu import XiaohongshuMixin


class DouyinProfileMixin:
    def _refresh_douyin_media_during_run(
        self,
        job_id: str,
        item_id: str,
        engine: MediaDownloader,
        cancel_event: threading.Event,
    ) -> DownloadItem:
        if cancel_event.is_set():
            raise DownloadCancelledError("Task cancelled")
        self._on_engine_event(
            job_id,
            item_id,
            EngineEvent(
                event="probing",
                message=(
                    "Refreshing this Douyin item from the original task link "
                    "after a blocked media route"
                ),
            ),
        )
        job_snapshot = self.get_job(job_id)
        if (
            job_snapshot.platform != Platform.DOUYIN
            or job_snapshot.source_kind not in {SourceKind.PROFILE, SourceKind.ITEM}
        ):
            raise DouyinMediaRefreshRequiredError(
                "Douyin media refresh requires an original profile or video link"
            )
        current_item = self._find_item(job_snapshot, item_id)
        current_media_id = str(current_item.media_id or "").strip()
        if not current_media_id:
            raise DouyinMediaRefreshRequiredError(
                "Douyin media refresh could not verify the current item identity"
            )
        refresh_author = str(current_item.author or job_snapshot.author or "").strip()
        refresh_cookie_fallback = False
        if job_snapshot.source_kind == SourceKind.PROFILE:
            if not engine.config.cookie_browser:
                raise TemporaryAccessError(
                    "Douyin automatic item refresh was skipped because Chrome "
                    "Cookie is disabled for this task. Continue with Chrome Cookie "
                    "enabled to request a fresh, identity-bound media address.",
                    issue_code=SiteIssueCode.COOKIE_UNAVAILABLE,
                )
            profile_id = MediaDownloader._douyin_profile_id(job_snapshot.source_url)
            if not profile_id:
                raise TemporaryAccessError(
                    "Douyin automatic media refresh could not verify the profile "
                    "identity"
                )
            previous_metadata = current_item.metadata.get("douyin_profile_media")
            if not isinstance(previous_metadata, dict):
                raise TemporaryAccessError(
                    "Douyin automatic item refresh could not verify the previous "
                    "quality floor"
                )

            def report_refresh_status(message: str) -> None:
                self._on_engine_event(
                    job_id,
                    item_id,
                    EngineEvent(event="probing", message=message),
                )

            refresh_progress_budget = new_signed_discovery_budget()
            try:
                refreshed_metadata = discover_item_metadata_from_profile(
                    profile_id,
                    current_media_id,
                    cookie_profile=engine.config.cookie_profile,
                    prefer_exact_detail=True,
                    should_cancel=cancel_event.is_set,
                    status_callback=report_refresh_status,
                    progress_budget=refresh_progress_budget,
                )
                if (
                    refreshed_metadata
                    and is_complete_profile_media_metadata(
                        refreshed_metadata,
                        current_media_id,
                        profile_id,
                    )
                    and not self._douyin_refresh_preserves_quality(
                        previous_metadata,
                        refreshed_metadata,
                    )
                ):
                    refreshed_metadata = discover_item_metadata_from_profile(
                        profile_id,
                        current_media_id,
                        cookie_profile=engine.config.cookie_profile,
                        should_cancel=cancel_event.is_set,
                        status_callback=report_refresh_status,
                        progress_budget=refresh_progress_budget,
                    )
            except (
                AuthenticationRequiredError,
                DownloadCancelledError,
                TemporaryAccessError,
            ):
                raise
            except Exception as exc:
                issue_code = classify_site_issue(exc)
                if issue_code == SiteIssueCode.LOCAL_CONFIGURATION:
                    raise TemporaryAccessError(
                        "Douyin automatic item refresh could not start because a "
                        "required local component is unavailable. Details: "
                        f"{safe_external_error_message(exc)}",
                        issue_code=issue_code,
                    ) from exc
                diagnostic_code = public_signing_diagnostic_code(exc)
                raise TemporaryAccessError(
                    "Douyin automatic item refresh did not pass identity or "
                    "integrity validation. The task was paused without downloading "
                    f"a fallback. Diagnostic code: {diagnostic_code}.",
                    issue_code=issue_code,
                    diagnostic_code=diagnostic_code,
                ) from exc
            if not refreshed_metadata or not is_complete_profile_media_metadata(
                refreshed_metadata,
                current_media_id,
                profile_id,
            ):
                raise TemporaryAccessError(
                    "Douyin automatic item refresh did not return complete verified "
                    "metadata. The old media address was not reused."
                )
            if not self._douyin_refresh_preserves_quality(
                previous_metadata,
                refreshed_metadata,
            ):
                raise TemporaryAccessError(
                    "Douyin automatic item refresh returned media below the "
                    "previously verified quality floor. The old address was not "
                    "reused and the lower-quality response was not downloaded."
                )
            fresh_current = current_item.model_copy(deep=True)
            fresh_current.metadata["douyin_profile_media"] = dict(refreshed_metadata)
            fresh_current.metadata["profile_url"] = job_snapshot.source_url
            fresh_current.metadata["profile_owner_verified"] = True
            refreshed_title = str(refreshed_metadata.get("title") or "").strip()
            if refreshed_title not in {
                "Untitled Douyin video",
                "Untitled Douyin image",
            }:
                fresh_current.title = refreshed_title or fresh_current.title
            refresh_author = str(
                refreshed_metadata.get("author") or refresh_author
            ).strip()
            fresh_current.author = refresh_author or fresh_current.author
            create_time = refreshed_metadata.get("create_time")
            if type(create_time) is int and create_time > 0:
                fresh_current.upload_date = MediaDownloader._douyin_upload_date(
                    create_time
                )
            fresh_current.media_type = (
                MediaType.IMAGE
                if refreshed_metadata.get("media_kind") == "image"
                else MediaType.VIDEO
            )
            if not self._is_complete_douyin_profile_item(
                job_snapshot,
                fresh_current,
            ):
                raise TemporaryAccessError(
                    "Douyin automatic item refresh returned media from a different "
                    "profile or item"
                )
        else:
            try:
                result = engine.discover(
                    job_snapshot.source_url,
                    job_snapshot.platform,
                    job_snapshot.source_kind,
                    should_cancel=cancel_event.is_set,
                )
                self._validate_discovery_result(job_snapshot, result)
            except (
                AuthenticationRequiredError,
                DownloadCancelledError,
                TemporaryAccessError,
            ):
                raise
            except Exception as exc:
                issue_code = classify_site_issue(exc)
                if issue_code == SiteIssueCode.LOCAL_CONFIGURATION:
                    raise TemporaryAccessError(
                        "Douyin automatic media refresh could not start because a "
                        "required local component is unavailable. Details: "
                        f"{safe_external_error_message(exc)}",
                        issue_code=issue_code,
                    ) from exc
                diagnostic_code = public_signing_diagnostic_code(exc)
                raise TemporaryAccessError(
                    "Douyin automatic media refresh did not pass identity or "
                    "integrity validation. The task was paused without downloading "
                    f"a fallback. Diagnostic code: {diagnostic_code}.",
                    issue_code=issue_code,
                    diagnostic_code=diagnostic_code,
                ) from exc
            fresh_current = next(
                (
                    value
                    for value in result.items
                    if str(value.media_id or "").strip() == current_media_id
                ),
                None,
            )
            if fresh_current is None:
                raise TemporaryAccessError(
                    "Douyin automatic media refresh did not return the current item. "
                    "The task was paused without reusing its old media address."
                )
            refresh_author = str(
                fresh_current.author or result.author or refresh_author
            ).strip()
            refresh_cookie_fallback = result.cookie_fallback_used
        if cancel_event.is_set():
            raise DownloadCancelledError("Task cancelled")

        with self._lock:
            job = self._require_job(job_id)
            old_current = self._find_item(job, item_id)
            if str(old_current.media_id or "").strip() != current_media_id:
                raise TemporaryAccessError(
                    "Douyin item identity changed during automatic media refresh"
                )
            old_current.source_url = fresh_current.source_url
            old_current.title = fresh_current.title
            old_current.upload_date = fresh_current.upload_date
            old_current.author = fresh_current.author
            old_current.extractor_key = fresh_current.extractor_key
            old_current.playlist_index = fresh_current.playlist_index
            old_current.media_type = fresh_current.media_type
            if job.source_kind == SourceKind.ITEM:
                old_current.metadata = dict(fresh_current.metadata)
            else:
                old_current.metadata = {
                    **old_current.metadata,
                    **fresh_current.metadata,
                }
            refreshed_item = old_current
            if job.source_kind == SourceKind.ITEM:
                bound, _ = self._bind_douyin_item_metadata(job, refreshed_item)
                if not bound:
                    raise TemporaryAccessError(
                        "Douyin automatic media refresh returned a different item"
                    )
            else:
                refreshed_item.metadata["profile_url"] = job.source_url
                refreshed_item.metadata["profile_owner_verified"] = True
            refreshed_item.metadata.pop(
                DOUYIN_PROFILE_REFRESH_REQUIRED_MARKER,
                None,
            )
            refreshed_item.status = ItemStatus.DOWNLOADING
            refreshed_item.error = None
            refreshed_item.auth_message = None
            refreshed_item.updated_at = utc_now()
            if refresh_author:
                job.author = refresh_author
            job.cookie_fallback_used |= refresh_cookie_fallback
            job.status = JobStatus.DOWNLOADING
            job.active_item_id = refreshed_item.id
            self._commit_locked(job)
            refreshed_snapshot = refreshed_item.model_copy(deep=True)
            refreshed_snapshot.metadata["_job_id"] = job_id
            if job.source_kind == SourceKind.PROFILE:
                refreshed_snapshot.metadata["profile_url"] = job.source_url
        self._notify(
            self.get_job(job_id),
            "media_refreshed",
            refreshed_snapshot.id,
        )
        return refreshed_snapshot
    @staticmethod
    def _douyin_refresh_preserves_quality(
        previous: dict[str, Any],
        refreshed: dict[str, Any],
    ) -> bool:
        media_kind = previous.get("media_kind")
        if media_kind != refreshed.get("media_kind"):
            return False
        if media_kind == "video":
            if (
                str(previous.get("video_uri") or "").strip()
                != str(refreshed.get("video_uri") or "").strip()
            ):
                return False
            previous_floor = quality_floor_dimensions(
                [previous, *(previous.get("direct_candidates") or [])],
                cap_full_hd=False,
            )
            refreshed_floor = quality_floor_dimensions(
                [refreshed, *(refreshed.get("direct_candidates") or [])],
                cap_full_hd=False,
            )
            if previous_floor is None or refreshed_floor is None:
                return False
            previous_short, previous_long = sorted(previous_floor)
            refreshed_short, refreshed_long = sorted(refreshed_floor)
            if refreshed_short < previous_short or refreshed_long < previous_long:
                return False
            if refreshed_short > previous_short or refreshed_long > previous_long:
                return True

            def codec_family(value: Any) -> str:
                normalized = str(value or "").strip().lower()
                if normalized in {"h265", "hevc", "bytevc1"}:
                    return "hevc"
                if normalized in {"h266", "vvc", "bytevc2"}:
                    return "vvc"
                return normalized

            previous_candidates = previous.get("direct_candidates")
            refreshed_candidates = refreshed.get("direct_candidates")
            if not isinstance(previous_candidates, list) or not isinstance(
                refreshed_candidates,
                list,
            ):
                return False
            for previous_candidate in previous_candidates:
                if not isinstance(previous_candidate, dict):
                    return False
                try:
                    previous_size = tuple(
                        sorted(
                            (
                                int(previous_candidate.get("width") or 0),
                                int(previous_candidate.get("height") or 0),
                            )
                        )
                    )
                    previous_bit_rate = int(previous_candidate.get("bit_rate") or 0)
                except (TypeError, ValueError, OverflowError):
                    return False
                previous_codec = codec_family(previous_candidate.get("codec_hint"))
                matched = False
                for refreshed_candidate in refreshed_candidates:
                    if not isinstance(refreshed_candidate, dict):
                        continue
                    try:
                        refreshed_size = tuple(
                            sorted(
                                (
                                    int(refreshed_candidate.get("width") or 0),
                                    int(refreshed_candidate.get("height") or 0),
                                )
                            )
                        )
                        refreshed_bit_rate = int(
                            refreshed_candidate.get("bit_rate") or 0
                        )
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if refreshed_size[0] < previous_size[0] or (
                        refreshed_size[1] < previous_size[1]
                    ):
                        continue
                    if refreshed_size != previous_size:
                        matched = True
                        break
                    if (
                        previous_codec
                        and codec_family(refreshed_candidate.get("codec_hint"))
                        != previous_codec
                    ):
                        continue
                    if previous_bit_rate > 0 and (
                        refreshed_bit_rate < previous_bit_rate
                    ):
                        continue
                    matched = True
                    break
                if not matched:
                    return False
            return True
        if media_kind != "image":
            return False

        def indexed_assets(values: Any) -> dict[int, dict[str, Any]] | None:
            if values is None:
                return {}
            if not isinstance(values, list):
                return None
            result: dict[int, dict[str, Any]] = {}
            for value in values:
                if not isinstance(value, dict):
                    return None
                try:
                    index = int(value.get("index") or 0)
                    width = int(value.get("width") or 0)
                    height = int(value.get("height") or 0)
                except (TypeError, ValueError, OverflowError):
                    return None
                if index <= 0 or width <= 0 or height <= 0 or index in result:
                    return None
                result[index] = value
            return result

        previous_images = indexed_assets(previous.get("image_assets"))
        refreshed_images = indexed_assets(refreshed.get("image_assets"))
        if previous_images is None or refreshed_images is None:
            return False
        for index, previous_asset in previous_images.items():
            refreshed_asset = refreshed_images.get(index)
            if refreshed_asset is None:
                return False
            previous_size = tuple(
                sorted((int(previous_asset["width"]), int(previous_asset["height"])))
            )
            refreshed_size = tuple(
                sorted((int(refreshed_asset["width"]), int(refreshed_asset["height"])))
            )
            if (
                refreshed_size[0] < previous_size[0]
                or refreshed_size[1] < previous_size[1]
            ):
                return False

        previous_live = indexed_assets(previous.get("live_photo_assets"))
        refreshed_live = indexed_assets(refreshed.get("live_photo_assets"))
        if previous_live is None or refreshed_live is None:
            return False
        for index, previous_asset in previous_live.items():
            refreshed_asset = refreshed_live.get(index)
            if refreshed_asset is None:
                return False
            if not DouyinProfileMixin._douyin_refresh_preserves_quality(
                {
                    "media_kind": "video",
                    "video_uri": previous_asset.get("video_uri"),
                    "minimum_width": previous_asset.get("width"),
                    "minimum_height": previous_asset.get("height"),
                    "direct_candidates": previous_asset.get("direct_candidates"),
                },
                {
                    "media_kind": "video",
                    "video_uri": refreshed_asset.get("video_uri"),
                    "minimum_width": refreshed_asset.get("width"),
                    "minimum_height": refreshed_asset.get("height"),
                    "direct_candidates": refreshed_asset.get("direct_candidates"),
                },
            ):
                return False
        return True
    @staticmethod
    def _legacy_douyin_markdown_item_source(value: str) -> str | None:
        match = _LEGACY_DOUYIN_MARKDOWN_ITEM_RE.fullmatch(value)
        if not match or match.group(2) != match.group(4):
            return None
        try:
            label_source = identify_url(match.group(1))
            target_source = identify_url(match.group(3))
        except (TypeError, ValueError):
            return None
        if (
            label_source.platform != Platform.DOUYIN
            or target_source.platform != Platform.DOUYIN
            or label_source.kind != SourceKind.ITEM
            or target_source.kind != SourceKind.ITEM
            or label_source.url != target_source.url
        ):
            return None
        return target_source.url
    @staticmethod
    def _douyin_item_target(job: DownloadJob) -> tuple[str, str] | None:
        if job.platform != Platform.DOUYIN:
            return None
        try:
            source = identify_url(job.source_url)
        except (TypeError, ValueError):
            return None
        if source.platform != Platform.DOUYIN or source.kind != SourceKind.ITEM:
            return None
        media_id = MediaDownloader._douyin_video_id(source.url)
        return (source.url, media_id) if media_id else None
    def _prepare_douyin_item_rediscovery_locked(self, job: DownloadJob) -> bool:
        target = self._douyin_item_target(job)
        if not target:
            if job.platform == Platform.DOUYIN and job.source_kind == SourceKind.ITEM:
                raise ItemNotRetryableError(DOUYIN_ITEM_SOURCE_ERROR)
            return False
        canonical_url, expected_id = target
        source_changed = (
            job.source_url != canonical_url or job.source_kind != SourceKind.ITEM
        )
        job.source_url = canonical_url
        job.source_kind = SourceKind.ITEM
        valid_items = [
            item
            for item in job.items
            if item.media_id == expected_id
            and self._is_bound_douyin_item_url(
                item.source_url,
                canonical_url,
                expected_id,
            )
        ]
        requires_identity_refresh = False
        for item in valid_items:
            had_profile_metadata = any(
                key in item.metadata
                for key in (
                    "profile_url",
                    "profile_owner_verified",
                    "douyin_profile_media",
                )
            )
            invalid_cache = self._discard_invalid_douyin_item_cache(
                item,
                canonical_url,
                expected_id,
            )
            self._bind_douyin_item_metadata(job, item)
            requires_identity_refresh |= had_profile_metadata or invalid_cache
        if requires_identity_refresh:
            job.discovery_complete = False
        invalid_expansion = self._douyin_item_has_invalid_expansion(job)
        if (
            not source_changed
            and not invalid_expansion
            and not requires_identity_refresh
            and job.discovery_complete
        ):
            return False
        job.items = valid_items[:1]
        for item in job.items:
            self._bind_douyin_item_metadata(job, item)
            if source_changed or invalid_expansion or requires_identity_refresh:
                item.status = ItemStatus.FAILED
                item.error = (
                    DOUYIN_ITEM_EXPANSION_MESSAGE
                    if invalid_expansion
                    else DOUYIN_ITEM_MIGRATION_MESSAGE
                )
                item.auth_message = None
                item.retryable = True
                item.updated_at = utc_now()
        job.active_item_id = None
        job.refresh_counts()
        return True
    @classmethod
    def _douyin_item_has_invalid_expansion(cls, job: DownloadJob) -> bool:
        target = cls._douyin_item_target(job)
        if not target or not job.items:
            return False
        canonical_url, expected_id = target
        valid_items = [
            item
            for item in job.items
            if item.media_id == expected_id
            and cls._is_bound_douyin_item_url(
                item.source_url,
                canonical_url,
                expected_id,
            )
        ]
        return len(job.items) != 1 or len(valid_items) != 1
    @staticmethod
    def _migrate_douyin_profile_for_rediscovery(
        job: DownloadJob,
        now: datetime,
    ) -> None:
        preserved_items = [item for item in job.items if item.output_paths]
        for item in preserved_items:
            item.auth_message = None
            item.status = ItemStatus.FAILED
            item.error = DOUYIN_PROFILE_REDISCOVERY_MESSAGE
            item.retryable = False
            item.metadata[DOUYIN_PROFILE_REDISCOVERY_ITEM_MARKER] = True
            media_id = str(item.media_id or "").strip()
            if not item.title or item.title == media_id or item.title.isdigit():
                item.title = f"Recovered Douyin files {media_id or item.id}"
            item.updated_at = now
        job.items = preserved_items
        job.status = JobStatus.INTERRUPTED
        job.error = DOUYIN_PROFILE_REDISCOVERY_MESSAGE
        job.warning = DOUYIN_PROFILE_REDISCOVERY_MESSAGE
        job.auth_message = None
        job.verification_url = job.source_url
        job.active_item_id = None
        job.cancel_requested = False
        job.retryable = True
        job.discovery_complete = False
        job.finished_at = now
    @staticmethod
    def _migrate_legacy_douyin_redirect_profile(
        job: DownloadJob,
        now: datetime,
    ) -> None:
        preserved_items: list[DownloadItem] = []
        for item in job.items:
            if item.status == ItemStatus.COMPLETED and item.output_paths:
                item.error = None
                item.auth_message = None
                item.metadata.pop(DOUYIN_PROFILE_REDISCOVERY_ITEM_MARKER, None)
                preserved_items.append(item)
                continue
            if not item.output_paths:
                continue
            item.status = ItemStatus.FAILED
            item.error = LEGACY_DOUYIN_MEDIA_REDIRECT_MESSAGE
            item.auth_message = None
            item.retryable = False
            item.metadata[DOUYIN_PROFILE_REDISCOVERY_ITEM_MARKER] = True
            item.updated_at = now
            preserved_items.append(item)
        job.items = preserved_items
        job.status = JobStatus.INTERRUPTED
        job.error = LEGACY_DOUYIN_MEDIA_REDIRECT_MESSAGE
        job.warning = LEGACY_DOUYIN_MEDIA_REDIRECT_MESSAGE
        job.auth_message = None
        job.verification_url = None
        job.active_item_id = None
        job.cancel_requested = False
        job.retryable = True
        job.discovery_complete = False
        job.finished_at = now
    @staticmethod
    def _retire_legacy_douyin_redirect_short_link(
        job: DownloadJob,
        now: datetime,
    ) -> None:
        for item in job.items:
            item.auth_message = None
            if item.status == ItemStatus.COMPLETED and item.output_paths:
                item.error = None
                continue
            item.status = ItemStatus.FAILED
            item.error = LEGACY_DOUYIN_SHORT_REDIRECT_MESSAGE
            item.retryable = False
            item.updated_at = now
        job.refresh_counts()
        job.status = JobStatus.PARTIAL if job.completed_items else JobStatus.FAILED
        job.error = LEGACY_DOUYIN_SHORT_REDIRECT_MESSAGE
        job.warning = LEGACY_DOUYIN_SHORT_REDIRECT_MESSAGE
        job.auth_message = None
        job.verification_url = None
        job.active_item_id = None
        job.cancel_requested = False
        job.retryable = False
        job.discovery_complete = False
        job.finished_at = now
    @staticmethod
    def _is_numeric_legacy_item(item: DownloadItem) -> bool:
        media_id = item.media_id or item.id
        if not media_id.isdigit():
            return False
        title = item.title.strip()
        return title in {"", "Untitled", media_id, item.id}
    @classmethod
    def _is_unverifiable_legacy_douyin_queue(cls, job: DownloadJob) -> bool:
        if job.retryable is False and job.error == DOUYIN_UNVERIFIABLE_QUEUE_ERROR:
            return False
        if (
            job.platform != Platform.DOUYIN
            or job.source_kind != SourceKind.PROFILE
            or len(job.items) < 2
        ):
            return False
        unverified_numeric_items = [
            item
            for item in job.items
            if cls._is_numeric_legacy_item(item)
            and item.metadata.get("profile_owner_verified") is not True
        ]
        return len(unverified_numeric_items) >= 2 and len(
            unverified_numeric_items
        ) * 2 >= len(job.items)
    @staticmethod
    def _is_quarantined_legacy_douyin_profile_queue(job: DownloadJob) -> bool:
        return (
            job.platform == Platform.DOUYIN
            and job.source_kind == SourceKind.PROFILE
            and job.retryable is False
            and job.error == DOUYIN_UNVERIFIABLE_QUEUE_ERROR
            and job.discovery_complete is False
        )
    @staticmethod
    def _recoverable_douyin_profile_source(job: DownloadJob) -> str | None:
        if job.platform != Platform.DOUYIN or job.source_kind != SourceKind.PROFILE:
            return None
        try:
            parsed = urlsplit(job.source_url.strip())
            port = parsed.port
        except (TypeError, ValueError):
            return None
        hostname = (parsed.hostname or "").lower().rstrip(".")
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme.lower() != "https"
            or hostname not in {"douyin.com", "www.douyin.com"}
            or parsed.username
            or parsed.password
            or port is not None
            or parsed.fragment
            or any(key in query for key in ("modal_id", "vid"))
        ):
            return None
        match = re.fullmatch(r"/user/([A-Za-z0-9_-]{8,200})/?", parsed.path)
        if match is None:
            return None
        return f"https://www.douyin.com/user/{match.group(1)}"
    @classmethod
    def _is_complete_douyin_profile_item(
        cls,
        job: DownloadJob,
        item: DownloadItem,
    ) -> bool:
        if job.platform != Platform.DOUYIN or job.source_kind != SourceKind.PROFILE:
            return False
        owner_id = MediaDownloader._douyin_profile_id(job.source_url)
        media_id = str(item.media_id or "").strip()
        if not owner_id or not media_id.isdigit():
            return False
        canonical_url = f"https://www.douyin.com/video/{media_id}"
        try:
            source = identify_url(item.source_url)
        except (TypeError, ValueError):
            return False
        if (
            source.platform != Platform.DOUYIN
            or source.kind != SourceKind.ITEM
            or source.url != canonical_url
            or item.source_url != canonical_url
            or item.metadata.get("profile_url") != job.source_url
            or item.metadata.get("profile_owner_verified") is not True
        ):
            return False
        return is_complete_profile_media_metadata(
            item.metadata.get("douyin_profile_media"),
            media_id,
            owner_id,
        )
    @classmethod
    def _refresh_douyin_profile_item_from_cache(
        cls,
        job: DownloadJob,
        item: DownloadItem,
    ) -> bool:
        if not cls._is_complete_douyin_profile_item(job, item):
            return False
        cached = item.metadata["douyin_profile_media"]
        changed = False
        title = str(cached.get("title") or "").strip()
        if title and not title.isdigit() and item.title != title:
            item.title = title
            changed = True
        author = str(cached.get("author") or "").strip()
        if author and item.author != author:
            item.author = author
            changed = True
        create_time = cached.get("create_time")
        if type(create_time) is int and create_time > 0:
            upload_date = MediaDownloader._douyin_upload_date(create_time)
            if item.upload_date != upload_date:
                item.upload_date = upload_date
                changed = True
        media_type = (
            MediaType.IMAGE if cached.get("media_kind") == "image" else MediaType.VIDEO
        )
        if item.media_type != media_type:
            item.media_type = media_type
            changed = True
        return changed
    @classmethod
    def _is_incomplete_verified_douyin_profile_queue(
        cls,
        job: DownloadJob,
    ) -> bool:
        if job.retryable is False and job.error == DOUYIN_UNVERIFIABLE_QUEUE_ERROR:
            return False
        if (
            job.warning == DOUYIN_PROFILE_REDISCOVERY_MESSAGE
            and job.discovery_complete is False
        ):
            return False
        if cls._is_unverifiable_legacy_douyin_queue(job):
            return False
        if (
            job.platform != Platform.DOUYIN
            or job.source_kind != SourceKind.PROFILE
            or not job.items
        ):
            return False
        return any(
            item.metadata.get("profile_owner_verified") is True
            and item.metadata.get(DOUYIN_PROFILE_REMOVED_ITEM_MARKER) is not True
            and not cls._is_complete_douyin_profile_item(job, item)
            for item in job.items
        )
    @classmethod
    def _validate_discovery_result(
        cls,
        job: DownloadJob,
        result: DiscoveryResult,
    ) -> None:
        if job.platform == Platform.KUAISHOU:
            original_kind, original_id = kuaishou_source_identity(job.source_url)
            if original_kind != job.source_kind.value:
                raise DiscoveryError("Kuaishou task source identity changed")
            if len(result.items) > 10_000:
                raise DiscoveryError("Kuaishou discovery exceeded the item safety limit")
            if not result.items:
                if original_kind != "profile" or job.items or not result.discovery_complete:
                    raise TemporaryAccessError(
                        "Kuaishou returned no verified media for this task. Retry "
                        "the original link; saved entries were preserved."
                    )
                return
            source_kind = result.items[0].metadata.get("kuaishou_source_kind")
            source_id = result.items[0].metadata.get("kuaishou_source_id")
            if any(
                item.metadata.get("kuaishou_source_kind") != source_kind
                or item.metadata.get("kuaishou_source_id") != source_id
                for item in result.items
            ):
                raise DiscoveryError("Kuaishou discovery returned inconsistent source identities")
            if (
                not isinstance(source_kind, str)
                or source_kind not in {"item", "profile"}
                or not isinstance(source_id, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", source_id) is None
            ):
                raise DiscoveryError("Kuaishou discovery returned an invalid source identity")
            if original_kind != "short_link" and (
                source_kind != original_kind or source_id != original_id
            ):
                raise DiscoveryError("Kuaishou discovery changed the requested source")
            if job.resolved_source_kind or job.resolved_source_id:
                if (
                    job.resolved_source_kind is None
                    or source_kind != job.resolved_source_kind.value
                    or source_id != job.resolved_source_id
                ):
                    raise DiscoveryError(
                        "Kuaishou share link target changed; create a new task"
                    )
            if source_kind == "item" and len(result.items) != 1:
                raise DiscoveryError("Kuaishou item discovery returned multiple works")
            seen_media_ids: set[str] = set()
            for item in result.items:
                media_id = str(item.media_id or "").strip()
                try:
                    item_kind, item_id = kuaishou_source_identity(item.source_url)
                    refresh_url = item.metadata.get("kuaishou_refresh_url")
                    refresh_kind, refresh_id = kuaishou_source_identity(refresh_url)
                except (DiscoveryError, TypeError, ValueError) as exc:
                    raise DiscoveryError(
                        "Kuaishou discovery returned an untrusted work URL"
                    ) from exc
                refresh_is_original_share = (
                    original_kind == "short_link"
                    and refresh_url == job.source_url
                    and refresh_kind == "short_link"
                    and refresh_id == original_id
                )
                if (
                    item_kind != "item"
                    or item_id != media_id
                    or media_id in seen_media_ids
                    or item.extractor_key != "Kuaishou"
                    or item.media_type not in {MediaType.VIDEO, MediaType.IMAGE}
                    or not isinstance(item.metadata.get("kuaishou_author_id"), str)
                    or not item.metadata["kuaishou_author_id"]
                    or (
                        source_kind == "profile"
                        and item.metadata["kuaishou_author_id"] != source_id
                    )
                    or (
                        source_kind == "item" and media_id != source_id
                    )
                    or not (
                        (refresh_kind == "item" and refresh_id == media_id)
                        or refresh_is_original_share
                    )
                ):
                    raise DiscoveryError(
                        "Kuaishou discovery returned duplicate or cross-wired media"
                    )
                seen_media_ids.add(media_id)
            return

        if job.platform == Platform.XIAOHONGSHU:
            if not result.items:
                raise TemporaryAccessError(
                    "Xiaohongshu discovery temporarily returned no trusted notes. "
                    "Retry after a short wait; no placeholders were queued."
                )
            if job.source_kind == SourceKind.ITEM and len(result.items) != 1:
                raise DiscoveryError(XIAOHONGSHU_ITEM_BINDING_ERROR)
            if job.source_kind == SourceKind.SHORT_LINK:
                resolved_binding = cls._xiaohongshu_short_binding_from_items(
                    result.items
                )
                expected_binding = cls._xiaohongshu_expected_short_binding(job)
                if resolved_binding is None:
                    raise DiscoveryError(XIAOHONGSHU_ITEM_BINDING_ERROR)
                if (
                    cls._has_xiaohongshu_binding_rediscovery_pending(job)
                    and expected_binding is None
                ):
                    raise DiscoveryError(
                        "Xiaohongshu short-link retry could not verify the original "
                        "resolved target. Create a new task from the original link."
                    )
                if expected_binding and resolved_binding != expected_binding:
                    raise DiscoveryError(
                        "Xiaohongshu short-link retry resolved to a different note "
                        "or profile. The changed target was blocked before download."
                    )
                if resolved_binding[0] == SourceKind.ITEM and len(result.items) != 1:
                    raise DiscoveryError(XIAOHONGSHU_ITEM_BINDING_ERROR)
            seen_media_ids: set[str] = set()
            for item in result.items:
                media_id = str(item.media_id or "").strip()
                if (
                    not cls._is_bound_xiaohongshu_item(job, item)
                    or media_id.lower() in seen_media_ids
                ):
                    raise DiscoveryError(
                        "Xiaohongshu discovery returned an untrusted, duplicate, or "
                        "cross-wired note URL. The unexpected item was blocked."
                    )
                seen_media_ids.add(media_id.lower())
            return

        if job.platform == Platform.DOUYIN and job.source_kind == SourceKind.PROFILE:
            has_retryable_unfinished_item = any(
                item.retryable
                and item.status not in {ItemStatus.COMPLETED, ItemStatus.SKIPPED}
                for item in job.items
            )
            if (
                cls._has_douyin_profile_rediscovery_pending(job)
                or any(
                    item.metadata.get(DOUYIN_PROFILE_REFRESH_REQUIRED_MARKER) is True
                    for item in job.items
                )
                or has_retryable_unfinished_item
            ) and not result.discovery_complete:
                raise TemporaryAccessError(
                    "Douyin profile retry returned only a partial author feed. "
                    "Previously queued media entries were not reused; retry after a "
                    "short wait before downloading any item."
                )
            seen_media_ids: set[str] = set()
            invalid_profile_item = False
            for item in result.items:
                media_id = str(item.media_id or "").strip()
                if (
                    not cls._is_complete_douyin_profile_item(job, item)
                    or media_id in seen_media_ids
                ):
                    invalid_profile_item = True
                    break
                seen_media_ids.add(media_id)
            if invalid_profile_item:
                raise TemporaryAccessError(DOUYIN_PROFILE_METADATA_ERROR)
            if not result.items:
                raise TemporaryAccessError(
                    "Douyin profile discovery temporarily returned no verified media "
                    "items. Retry after a short wait; no placeholders were queued."
                )
            return

        target = cls._douyin_item_target(job)
        if not target:
            return
        canonical_url, expected_id = target
        if len(result.items) != 1:
            raise DiscoveryError(DOUYIN_ITEM_EXPANSION_MESSAGE)
        item = result.items[0]
        if item.media_id != expected_id or not cls._is_bound_douyin_item_url(
            item.source_url,
            canonical_url,
            expected_id,
        ):
            raise DiscoveryError(DOUYIN_ITEM_EXPANSION_MESSAGE)
        item.source_url = canonical_url
        cached = item.metadata.get("douyin_item_media")
        cached_media_kind = (
            str(cached.get("media_kind") or "").strip()
            if isinstance(cached, dict)
            else ""
        )
        cache_identity_is_bound = (
            item.metadata.get("item_identity_verified") is True
            and item.metadata.get("verification_url") == canonical_url
            and isinstance(cached, dict)
            and str(cached.get("media_id") or "").strip() == expected_id
        )
        video_cache_identity_is_bound = (
            cache_identity_is_bound
            and cached_media_kind != "image"
            and re.fullmatch(
                r"[A-Za-z0-9_-]{10,200}",
                str(cached.get("video_uri") or "").strip(),
            )
            is not None
        )
        if video_cache_identity_is_bound and not (
            MediaDownloader._douyin_direct_candidates_from_cache(cached)
        ):
            raise TemporaryAccessError(
                "Douyin item discovery returned no verified author-feed direct "
                "rendition. Retry after a short wait; no default-only fallback was "
                "queued."
            )
        if cls._discard_invalid_douyin_item_cache(
            item,
            canonical_url,
            expected_id,
        ):
            raise DiscoveryError(DOUYIN_ITEM_EXPANSION_MESSAGE)
        cls._bind_douyin_item_metadata(job, item)
    @classmethod
    def _bind_douyin_item_metadata(
        cls,
        job: DownloadJob,
        item: DownloadItem,
    ) -> tuple[bool, bool]:
        target = cls._douyin_item_target(job)
        if not target:
            return False, False
        canonical_url, expected_id = target
        if item.media_id != expected_id or not cls._is_bound_douyin_item_url(
            item.source_url,
            canonical_url,
            expected_id,
        ):
            return False, False

        changed = item.source_url != canonical_url
        item.source_url = canonical_url
        for key in (
            "profile_url",
            "profile_owner_verified",
            "douyin_profile_media",
        ):
            if key in item.metadata:
                item.metadata.pop(key, None)
                changed = True
        if item.metadata.get("verification_url") != canonical_url:
            item.metadata["verification_url"] = canonical_url
            changed = True
        return True, changed
    @staticmethod
    def _discard_invalid_douyin_item_cache(
        item: DownloadItem,
        canonical_url: str,
        expected_id: str,
    ) -> bool:
        if "douyin_item_media" not in item.metadata:
            return False
        cached = item.metadata.get("douyin_item_media")
        common_identity_is_valid = (
            item.metadata.get("item_identity_verified") is True
            and item.metadata.get("verification_url") == canonical_url
            and isinstance(cached, dict)
            and str(cached.get("media_id") or "").strip() == expected_id
        )
        cached_media_kind = (
            str(cached.get("media_kind") or "").strip()
            if isinstance(cached, dict)
            else ""
        )
        if common_identity_is_valid and cached_media_kind == "image":
            owner_id = str(cached.get("owner_id") or "").strip()
            valid = (
                item.media_type == MediaType.IMAGE
                and re.fullmatch(r"[A-Za-z0-9_-]{10,200}", owner_id) is not None
                and is_complete_profile_media_metadata(
                    cached,
                    expected_id,
                    owner_id,
                )
            )
        else:
            valid = (
                common_identity_is_valid
                and isinstance(cached, dict)
                and re.fullmatch(
                    r"[A-Za-z0-9_-]{10,200}",
                    str(cached.get("video_uri") or "").strip(),
                )
                is not None
                and bool(MediaDownloader._douyin_direct_candidates_from_cache(cached))
            )
        if valid:
            return False
        item.metadata.pop("douyin_item_media", None)
        item.metadata.pop("item_identity_verified", None)
        return True
    @staticmethod
    def _is_bound_douyin_item_url(
        value: str,
        canonical_url: str,
        expected_id: str,
    ) -> bool:
        try:
            source = identify_url(value)
        except (TypeError, ValueError):
            return False
        return (
            source.platform == Platform.DOUYIN
            and source.kind == SourceKind.ITEM
            and source.url == canonical_url
            and MediaDownloader._douyin_video_id(source.url) == expected_id
        )
    @staticmethod
    def _message_has_legacy_douyin_media_redirect(value: str | None) -> bool:
        if not value:
            return False
        if LEGACY_DOUYIN_MEDIA_REDIRECT_MARKER in value:
            return True
        has_reason = bool(
            re.search(
                r"(?:Redirect reason:\s*|reason:\s*)[a-z0-9-]+",
                value,
                re.IGNORECASE,
            )
        )
        return not has_reason and any(
            marker in value for marker in LEGACY_DOUYIN_MEDIA_REDIRECT_MARKERS[1:]
        )
    @staticmethod
    def _message_has_douyin_media_redirect(value: str | None) -> bool:
        return bool(
            value
            and any(marker in value for marker in LEGACY_DOUYIN_MEDIA_REDIRECT_MARKERS)
        )
    @classmethod
    def _has_legacy_douyin_media_redirect_error(cls, job: DownloadJob) -> bool:
        messages = [job.error, job.warning, job.auth_message]
        for item in job.items:
            messages.extend((item.error, item.auth_message))
        return any(
            cls._message_has_legacy_douyin_media_redirect(value) for value in messages
        )
    @staticmethod
    def _should_rediscover_on_retry(job: DownloadJob) -> bool:
        if job.platform == Platform.KUAISHOU:
            return (
                not job.items
                or not job.discovery_complete
                or job.status in {JobStatus.NEEDS_AUTH, JobStatus.INTERRUPTED}
            )
        if XiaohongshuMixin._has_xiaohongshu_binding_rediscovery_pending(job):
            return True
        if XiaohongshuMixin._has_xiaohongshu_profile_media_revalidation_pending(job):
            return True
        if job.platform == Platform.XIAOHONGSHU and job.source_kind in {
            SourceKind.ITEM,
            SourceKind.SHORT_LINK,
        }:
            return job.status in {
                JobStatus.FAILED,
                JobStatus.NEEDS_AUTH,
                JobStatus.INTERRUPTED,
                JobStatus.PARTIAL,
                JobStatus.CANCELLED,
            } or any(
                item.retryable and item.status in RETRYABLE_ITEM_STATUSES
                for item in job.items
            )
        if job.platform == Platform.DOUYIN and job.source_kind == SourceKind.ITEM:
            return job.status in {
                JobStatus.FAILED,
                JobStatus.NEEDS_AUTH,
                JobStatus.INTERRUPTED,
                JobStatus.PARTIAL,
                JobStatus.CANCELLED,
            } or any(
                item.retryable and item.status in RETRYABLE_ITEM_STATUSES
                for item in job.items
            )
        return (
            job.source_kind == SourceKind.PROFILE
            and job.platform in {Platform.XIAOHONGSHU, Platform.DOUYIN}
            and (
                job.status in {JobStatus.NEEDS_AUTH, JobStatus.INTERRUPTED}
                or any(item.status == ItemStatus.NEEDS_AUTH for item in job.items)
                or not job.discovery_complete
                or any(
                    item.retryable and item.status in RETRYABLE_ITEM_STATUSES
                    for item in job.items
                )
            )
        )
    @staticmethod
    def _has_douyin_profile_rediscovery_pending(job: DownloadJob) -> bool:
        return (
            job.platform == Platform.DOUYIN
            and job.source_kind == SourceKind.PROFILE
            and any(
                item.metadata.get(DOUYIN_PROFILE_REDISCOVERY_ITEM_MARKER) is True
                for item in job.items
            )
        )
    @staticmethod
    def _has_douyin_profile_refresh_required(job: DownloadJob) -> bool:
        return (
            job.platform == Platform.DOUYIN
            and job.source_kind == SourceKind.PROFILE
            and any(
                item.metadata.get(DOUYIN_PROFILE_REFRESH_REQUIRED_MARKER) is True
                for item in job.items
            )
        )
    @staticmethod
    def _merge_discovered_items(
        previous: list[DownloadItem],
        discovered: list[DownloadItem],
        *,
        retire_missing_douyin_profile_items: bool = False,
        preserve_unmatched_items: bool = False,
    ) -> list[DownloadItem]:
        previous_by_media_id = {
            item.media_id: item for item in previous if item.media_id
        }
        previous_by_id = {item.id: item for item in previous}
        matched_previous_ids: set[str] = set()
        merged: list[DownloadItem] = []

        for fresh in discovered:
            fresh_is_douyin = (
                fresh.extractor_key == "Douyin"
                or fresh.metadata.get("profile_owner_verified") is True
                or "douyin_item_media" in fresh.metadata
            )
            old = (
                previous_by_media_id.get(fresh.media_id)
                if fresh.media_id
                else previous_by_id.get(fresh.id)
            )
            if old is None:
                merged.append(fresh)
                continue

            matched_previous_ids.add(old.id)
            recovery_pending = (
                old.metadata.get(DOUYIN_PROFILE_REDISCOVERY_ITEM_MARKER) is True
            )
            xiaohongshu_media_revalidation_pending = (
                old.metadata.get(XIAOHONGSHU_PROFILE_MEDIA_REVALIDATION_MARKER) is True
            )
            removed_after_refresh = (
                old.metadata.get(DOUYIN_PROFILE_REMOVED_ITEM_MARKER) is True
            )
            if (
                recovery_pending
                or removed_after_refresh
                or xiaohongshu_media_revalidation_pending
            ):
                fresh.attempts = old.attempts
                fresh.created_at = old.created_at
                fresh.output_paths = list(old.output_paths)
                fresh.status = ItemStatus.QUEUED
                fresh.error = None
                fresh.auth_message = None
                fresh.retryable = True
                old_metadata = dict(old.metadata)
                old_metadata.pop(DOUYIN_PROFILE_REDISCOVERY_ITEM_MARKER, None)
                old_metadata.pop(DOUYIN_PROFILE_REFRESH_REQUIRED_MARKER, None)
                old_metadata.pop(DOUYIN_PROFILE_REMOVED_ITEM_MARKER, None)
                old_metadata.pop(
                    XIAOHONGSHU_PROFILE_MEDIA_REVALIDATION_MARKER,
                    None,
                )
                fresh.metadata = {**old_metadata, **fresh.metadata}
                merged.append(fresh)
                continue
            if old.status == ItemStatus.COMPLETED:
                refreshed = old.model_copy(deep=True)
                refreshed.id = fresh.id
                refreshed.source_url = fresh.source_url
                if fresh_is_douyin:
                    refreshed.title = fresh.title
                    refreshed.upload_date = fresh.upload_date
                    refreshed.author = fresh.author
                    refreshed.media_type = fresh.media_type
                refreshed.playlist_index = fresh.playlist_index
                refreshed.extractor_key = fresh.extractor_key
                refreshed.metadata.update(fresh.metadata)
                merged.append(refreshed)
                continue

            fresh.attempts = old.attempts
            fresh.created_at = old.created_at
            fresh.status = old.status
            fresh.progress = old.progress.model_copy(deep=True)
            fresh.output_paths = list(old.output_paths)
            fresh.selected_format = old.selected_format
            fresh.resolution = old.resolution
            fresh.error = old.error
            fresh.auth_message = old.auth_message
            fresh.retryable = old.retryable
            old_metadata = dict(old.metadata)
            old_metadata.pop(DOUYIN_PROFILE_REFRESH_REQUIRED_MARKER, None)
            fresh.metadata = {**old_metadata, **fresh.metadata}
            if not fresh_is_douyin and old.title and old.title != old.media_id:
                fresh.title = old.title
            if not fresh_is_douyin:
                fresh.upload_date = old.upload_date or fresh.upload_date
                fresh.author = old.author or fresh.author
                fresh.media_type = (
                    old.media_type
                    if old.media_type.value != "unknown"
                    else fresh.media_type
                )
            merged.append(fresh)

        for old in previous:
            if old.id in matched_previous_ids:
                continue
            retained = old.model_copy(deep=True)
            if (
                retained.metadata.pop(DOUYIN_PROFILE_REDISCOVERY_ITEM_MARKER, None)
                is True
            ):
                retained.status = ItemStatus.SKIPPED
                retained.error = DOUYIN_PROFILE_RETIRED_ITEM_MESSAGE
                retained.auth_message = None
                retained.retryable = False
                media_id = str(retained.media_id or "").strip()
                if (
                    not retained.title
                    or retained.title == media_id
                    or retained.title.isdigit()
                ):
                    retained.title = f"Recovered Douyin files {media_id or retained.id}"
                retained.updated_at = utc_now()
                merged.append(retained)
                continue
            if retire_missing_douyin_profile_items and retained.status not in {
                ItemStatus.COMPLETED,
                ItemStatus.SKIPPED,
            }:
                if not retained.output_paths:
                    continue
                retained.metadata.pop(
                    DOUYIN_PROFILE_REFRESH_REQUIRED_MARKER,
                    None,
                )
                retained.metadata.pop("douyin_profile_media", None)
                retained.metadata[DOUYIN_PROFILE_REMOVED_ITEM_MARKER] = True
                retained.status = ItemStatus.FAILED
                retained.error = DOUYIN_PROFILE_REMOVED_PARTIAL_ITEM_MESSAGE
                retained.auth_message = None
                retained.retryable = False
                retained.updated_at = utc_now()
                merged.append(retained)
                continue
            if (
                not preserve_unmatched_items
                and retained.status != ItemStatus.COMPLETED
                and retained.retryable
            ):
                retained.status = ItemStatus.FAILED
                retained.error = "Item was not found when the profile was refreshed"
                retained.updated_at = utc_now()
            merged.append(retained)

        return merged
    @classmethod
    def _mark_douyin_profile_redirect_refresh_required(
        cls,
        job: DownloadJob,
    ) -> bool:
        redirect_items = [
            item
            for item in job.items
            if item.status not in {ItemStatus.COMPLETED, ItemStatus.SKIPPED}
            and (
                cls._message_has_douyin_media_redirect(item.error)
                or cls._message_has_douyin_media_redirect(item.auth_message)
            )
        ]
        if not redirect_items and any(
            cls._message_has_douyin_media_redirect(value)
            for value in (job.error, job.warning, job.auth_message)
        ):
            active_item = next(
                (
                    item
                    for item in job.items
                    if item.id == job.active_item_id
                    and item.status not in {ItemStatus.COMPLETED, ItemStatus.SKIPPED}
                ),
                None,
            )
            fallback_item = next(
                (
                    item
                    for item in job.items
                    if item.status not in {ItemStatus.COMPLETED, ItemStatus.SKIPPED}
                ),
                None,
            )
            if active_item or fallback_item:
                redirect_items = [active_item or fallback_item]
        if not redirect_items:
            return False
        changed = False
        for item in redirect_items:
            if item.metadata.get(DOUYIN_PROFILE_REFRESH_REQUIRED_MARKER) is not True:
                item.metadata[DOUYIN_PROFILE_REFRESH_REQUIRED_MARKER] = True
                changed = True
            if not item.retryable:
                item.retryable = True
                changed = True
        if not job.retryable:
            job.retryable = True
            changed = True
        if job.discovery_complete:
            job.discovery_complete = False
            changed = True
        return changed
