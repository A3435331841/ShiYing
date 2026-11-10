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


from .douyin_profile import DouyinProfileMixin
from .issues import IssueMixin
from .lifecycle import LifecycleMixin
from .xiaohongshu import XiaohongshuMixin


class DownloadManager(
    LifecycleMixin,
    IssueMixin,
    DouyinProfileMixin,
    XiaohongshuMixin,
):

    def __init__(
        self,
        *,
        state_dir: str | Path = "data/state",
        default_output_root: str | Path = "downloads",
        max_workers: int = 2,
        downloader_config: DownloaderConfig | None = None,
        organized_output: bool = False,
    ) -> None:
        self.store = JsonJobStore(state_dir)
        self.default_output_root = Path(default_output_root).expanduser().resolve()
        self.default_output_root.mkdir(parents=True, exist_ok=True)
        self.downloader_config = downloader_config or DownloaderConfig()
        self.organized_output = organized_output
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="media-download"
        )
        self._lock = threading.RLock()
        self._jobs: dict[str, DownloadJob] = {}
        self._futures: dict[str, Future[None]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._listeners: list[Listener] = []
        self._last_progress_save: dict[str, float] = {}
        self.load_warnings: list[str] = []
        self._restore_jobs()
    def output_directory(self, job: DownloadJob, item: DownloadItem | None = None) -> Path:
        """Keep desktop downloads grouped by platform and author."""
        if self.organized_output:
            platforms = {Platform.DOUYIN: "抖音", Platform.KUAISHOU: "快手",
                         Platform.XIAOHONGSHU: "小红书", Platform.BILIBILI: "B站", Platform.WECHAT: "视频号",
                         Platform.YOUTUBE: "YouTube"}
            author = job.author or next((i.author for i in job.items if i.author), None)
            root = Path(job.output_root) / platforms[job.platform] / safe_component(author, fallback="未知作者", limit=72)
            if item is None and len(job.items) != 1:
                return root
            item = item or job.items[0]
            if item.media_type == MediaType.IMAGE:
                label = safe_component(item.title, fallback="图文作品", limit=72)
                identity = safe_component(item.media_id or item.id, limit=32)
                date = item.upload_date or "Unknown-Date"
                if re.fullmatch(r"\d{8}", date):
                    date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
                date = safe_component(date, fallback="Unknown-Date", limit=20)
                return root / f"{date}-{label} [{identity}]"
            return root
        return platform_output_directory(job.platform, job.output_root, job.author)
    def _restore_jobs(self) -> None:
        jobs, warnings = self.store.load_all()
        self.load_warnings = warnings
        for job in jobs:
            changed = False
            legacy_douyin_result = False
            migrated_douyin_item_source = False
            migrated_incomplete_douyin_profile_queue = False
            was_quarantined_douyin_profile_queue = (
                self._is_quarantined_legacy_douyin_profile_queue(job)
            )
            recovered_legacy_source = self._legacy_douyin_markdown_item_source(
                job.source_url
            )
            try:
                normalized_source = identify_url(
                    recovered_legacy_source or job.source_url
                )
            except (TypeError, ValueError):
                normalized_source = None
            if (
                not was_quarantined_douyin_profile_queue
                and job.platform == Platform.DOUYIN
                and normalized_source
                and normalized_source.platform == Platform.DOUYIN
                and normalized_source.kind == SourceKind.ITEM
            ):
                normalized_media_id = MediaDownloader._douyin_video_id(
                    normalized_source.url
                )
                requires_item_migration = (
                    recovered_legacy_source is not None
                    or job.source_kind != SourceKind.ITEM
                    or MediaDownloader._douyin_video_id(job.source_url)
                    != normalized_media_id
                )
                source_changed = (
                    job.source_url != normalized_source.url
                    or job.source_kind != SourceKind.ITEM
                )
                if source_changed:
                    job.source_url = normalized_source.url
                    job.source_kind = SourceKind.ITEM
                    migrated_douyin_item_source = requires_item_migration
                    changed = True
                if (
                    job.verification_url is not None
                    and job.verification_url != normalized_source.url
                ):
                    job.verification_url = normalized_source.url
                    changed = True
            if self._is_xiaohongshu_direct_job(job):
                if job.source_kind == SourceKind.SHORT_LINK:
                    short_binding = self._xiaohongshu_expected_short_binding(job)
                    if short_binding and (
                        job.resolved_source_kind != short_binding[0]
                        or job.resolved_source_id != short_binding[1]
                    ):
                        job.resolved_source_kind = short_binding[0]
                        job.resolved_source_id = short_binding[1]
                        changed = True
                binding_rediscovery_pending = False
                for item in job.items:
                    if not (
                        item.metadata.get(XIAOHONGSHU_BINDING_REDISCOVERY_MARKER)
                        is True
                        or item.error == XIAOHONGSHU_ITEM_BINDING_ERROR
                    ):
                        continue
                    binding_rediscovery_pending = True
                    if (
                        item.metadata.get(XIAOHONGSHU_BINDING_REDISCOVERY_MARKER)
                        is not True
                    ):
                        item.metadata[XIAOHONGSHU_BINDING_REDISCOVERY_MARKER] = True
                        changed = True
                    if not item.retryable:
                        item.retryable = True
                        changed = True
                if binding_rediscovery_pending:
                    if job.discovery_complete:
                        job.discovery_complete = False
                        changed = True
                    if not job.retryable:
                        job.retryable = True
                        changed = True
            if (
                job.platform == Platform.XIAOHONGSHU
                and job.source_kind == SourceKind.PROFILE
            ):
                now = utc_now()
                migrated_xiaohongshu_profile_media = False
                for item in job.items:
                    if not (
                        item.status == ItemStatus.COMPLETED
                        and item.media_type == MediaType.IMAGE
                        and XIAOHONGSHU_MEDIA_VERIFICATION_MARKER not in item.metadata
                    ):
                        continue
                    item.status = ItemStatus.FAILED
                    item.error = XIAOHONGSHU_PROFILE_MEDIA_REVALIDATION_MESSAGE
                    item.auth_message = None
                    item.retryable = True
                    item.metadata[XIAOHONGSHU_PROFILE_MEDIA_REVALIDATION_MARKER] = True
                    item.updated_at = now
                    migrated_xiaohongshu_profile_media = True
                    changed = True
                if migrated_xiaohongshu_profile_media:
                    job.status = JobStatus.INTERRUPTED
                    job.error = XIAOHONGSHU_PROFILE_MEDIA_REVALIDATION_MESSAGE
                    job.warning = XIAOHONGSHU_PROFILE_MEDIA_REVALIDATION_MESSAGE
                    job.auth_message = None
                    job.verification_url = None
                    job.active_item_id = None
                    job.cancel_requested = False
                    job.retryable = True
                    job.discovery_complete = False
                    job.finished_at = now
            if (
                job.platform == Platform.DOUYIN
                and job.source_kind == SourceKind.PROFILE
            ):
                for item in job.items:
                    changed |= self._refresh_douyin_profile_item_from_cache(job, item)
            legacy_media_redirect = self._has_legacy_douyin_media_redirect_error(job)
            if legacy_media_redirect and job.platform == Platform.DOUYIN:
                now = utc_now()
                if job.source_kind == SourceKind.PROFILE:
                    self._migrate_legacy_douyin_redirect_profile(job, now)
                    migrated_incomplete_douyin_profile_queue = True
                elif job.source_kind == SourceKind.ITEM:
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
                    for item in job.items:
                        if not self._message_has_legacy_douyin_media_redirect(
                            item.error
                        ) and not self._message_has_legacy_douyin_media_redirect(
                            item.auth_message
                        ):
                            continue
                        item.status = ItemStatus.FAILED
                        item.error = LEGACY_DOUYIN_MEDIA_REDIRECT_MESSAGE
                        item.auth_message = None
                        item.retryable = True
                        item.metadata.pop("douyin_item_media", None)
                        item.metadata.pop("item_identity_verified", None)
                        item.updated_at = now
                elif job.source_kind == SourceKind.SHORT_LINK:
                    self._retire_legacy_douyin_redirect_short_link(job, now)
                changed = job.source_kind in {
                    SourceKind.PROFILE,
                    SourceKind.ITEM,
                    SourceKind.SHORT_LINK,
                }
            elif (
                job.platform == Platform.DOUYIN
                and job.source_kind == SourceKind.PROFILE
            ):
                changed |= self._mark_douyin_profile_redirect_refresh_required(job)
            legacy_signing_messages = (job.error or "", job.auth_message or "")
            if (
                job.platform == Platform.DOUYIN
                and job.status == JobStatus.NEEDS_AUTH
                and any(
                    LEGACY_DOUYIN_GENERIC_SIGNING_MARKER in message
                    for message in legacy_signing_messages
                )
            ):
                now = utc_now()
                job.status = JobStatus.FAILED
                job.error = LEGACY_DOUYIN_GENERIC_SIGNING_MESSAGE
                job.auth_message = None
                job.verification_url = None
                job.active_item_id = None
                job.cancel_requested = False
                job.retryable = True
                job.discovery_complete = False
                job.finished_at = now
                for item in job.items:
                    if item.status != ItemStatus.NEEDS_AUTH:
                        continue
                    item.status = ItemStatus.FAILED
                    item.error = LEGACY_DOUYIN_GENERIC_SIGNING_MESSAGE
                    item.auth_message = None
                    item.retryable = True
                    item.updated_at = now
                changed = True
            if migrated_douyin_item_source:
                now = utc_now()
                job.status = JobStatus.INTERRUPTED
                job.error = DOUYIN_ITEM_MIGRATION_MESSAGE
                job.auth_message = None
                job.verification_url = normalized_source.url
                job.active_item_id = None
                job.cancel_requested = False
                job.retryable = True
                job.discovery_complete = False
                job.finished_at = now
                for item in job.items:
                    bound, metadata_changed = self._bind_douyin_item_metadata(job, item)
                    changed |= metadata_changed
                    if not bound:
                        continue
                    item.status = ItemStatus.FAILED
                    item.error = DOUYIN_ITEM_MIGRATION_MESSAGE
                    item.auth_message = None
                    item.retryable = True
                    item.updated_at = now
                changed = True
            if self._is_incomplete_verified_douyin_profile_queue(job):
                now = utc_now()
                self._migrate_douyin_profile_for_rediscovery(job, now)
                migrated_incomplete_douyin_profile_queue = True
                changed = True
            unverified_numeric_queue = self._is_unverifiable_legacy_douyin_queue(job)
            previously_quarantined_numeric_queue = (
                was_quarantined_douyin_profile_queue
                or self._is_quarantined_legacy_douyin_profile_queue(job)
            )
            if unverified_numeric_queue or previously_quarantined_numeric_queue:
                now = utc_now()
                recoverable_profile_source = self._recoverable_douyin_profile_source(
                    job
                )
                if recoverable_profile_source is not None:
                    job.source_url = recoverable_profile_source
                    self._migrate_douyin_profile_for_rediscovery(job, now)
                    migrated_incomplete_douyin_profile_queue = True
                    changed = True
                elif unverified_numeric_queue:
                    preserved_items = [item for item in job.items if item.output_paths]
                    for item in preserved_items:
                        item.status = ItemStatus.FAILED
                        item.error = DOUYIN_UNVERIFIABLE_QUEUE_ERROR
                        item.auth_message = None
                        item.retryable = False
                        item.updated_at = now
                    job.items = preserved_items
                    job.status = JobStatus.FAILED
                    job.error = DOUYIN_UNVERIFIABLE_QUEUE_ERROR
                    job.warning = DOUYIN_UNVERIFIABLE_QUEUE_ERROR
                    job.auth_message = None
                    job.verification_url = None
                    job.active_item_id = None
                    job.cancel_requested = False
                    job.retryable = False
                    job.discovery_complete = False
                    job.finished_at = now
                    changed = True
            if (
                job.platform == Platform.DOUYIN
                and job.source_kind == SourceKind.ITEM
                and (
                    normalized_source is None
                    or normalized_source.platform != Platform.DOUYIN
                    or normalized_source.kind != SourceKind.ITEM
                )
            ):
                now = utc_now()
                job.status = JobStatus.FAILED
                job.error = DOUYIN_ITEM_SOURCE_ERROR
                job.auth_message = None
                job.verification_url = None
                job.active_item_id = None
                job.cancel_requested = False
                job.retryable = False
                job.finished_at = now
                for item in job.items:
                    item.status = ItemStatus.FAILED
                    item.error = DOUYIN_ITEM_SOURCE_ERROR
                    item.retryable = False
                    item.updated_at = now
                changed = True
            target = (
                None
                if was_quarantined_douyin_profile_queue
                else self._douyin_item_target(job)
            )
            if target:
                requires_direct_rediscovery = False
                now = utc_now()
                for item in job.items:
                    had_profile_metadata = any(
                        key in item.metadata
                        for key in (
                            "profile_url",
                            "profile_owner_verified",
                            "douyin_profile_media",
                        )
                    )
                    cache_was_invalid = (
                        item.media_id == target[1]
                        and self._is_bound_douyin_item_url(
                            item.source_url,
                            target[0],
                            target[1],
                        )
                        and self._discard_invalid_douyin_item_cache(
                            item,
                            target[0],
                            target[1],
                        )
                    )
                    bound, metadata_changed = self._bind_douyin_item_metadata(job, item)
                    changed |= metadata_changed or cache_was_invalid
                    if bound and had_profile_metadata:
                        requires_direct_rediscovery = True
                    if bound and cache_was_invalid:
                        requires_direct_rediscovery = True
                    if bound and requires_direct_rediscovery:
                        item.status = ItemStatus.FAILED
                        item.error = DOUYIN_ITEM_MIGRATION_MESSAGE
                        item.auth_message = None
                        item.retryable = True
                        item.updated_at = now
                if requires_direct_rediscovery:
                    job.status = JobStatus.INTERRUPTED
                    job.error = DOUYIN_ITEM_MIGRATION_MESSAGE
                    job.auth_message = None
                    job.verification_url = target[0]
                    job.active_item_id = None
                    job.cancel_requested = False
                    job.retryable = True
                    job.discovery_complete = False
                    job.finished_at = now
                    changed = True
            if job.status in ACTIVE_JOB_STATUSES:
                job.status = JobStatus.INTERRUPTED
                job.error = "The application stopped before this task finished. Retry to continue."
                job.active_item_id = None
                job.cancel_requested = False
                job.finished_at = utc_now()
                changed = True
            for item in job.items:
                if (
                    job.platform == Platform.DOUYIN
                    and job.source_kind == SourceKind.PROFILE
                    and item.status == ItemStatus.COMPLETED
                    and not item.metadata.get("profile_owner_verified")
                ):
                    item.status = ItemStatus.FAILED
                    item.error = LEGACY_DOUYIN_RESULT_ERROR
                    item.retryable = False
                    item.updated_at = utc_now()
                    legacy_douyin_result = True
                    changed = True
                if item.status in {ItemStatus.DOWNLOADING, ItemStatus.POSTPROCESSING}:
                    item.status = ItemStatus.FAILED
                    item.error = "Interrupted when the application stopped"
                    item.retryable = True
                    item.updated_at = utc_now()
                    changed = True
            if (
                not was_quarantined_douyin_profile_queue
                and self._douyin_item_has_invalid_expansion(job)
            ):
                target = self._douyin_item_target(job)
                now = utc_now()
                if target:
                    canonical_url, expected_id = target
                    target_items = [
                        item
                        for item in job.items
                        if item.media_id == expected_id
                        and self._is_bound_douyin_item_url(
                            item.source_url,
                            canonical_url,
                            expected_id,
                        )
                        and (
                            item.output_paths or not self._is_numeric_legacy_item(item)
                        )
                    ]
                    target_item_ids = {item.id for item in target_items}
                    preserved_files = [
                        item
                        for item in job.items
                        if item.output_paths and item.id not in target_item_ids
                    ]
                    job.items = target_items[:1] + preserved_files
                for item in job.items:
                    item.status = ItemStatus.FAILED
                    item.error = DOUYIN_ITEM_EXPANSION_MESSAGE
                    item.auth_message = None
                    item.retryable = True
                    item.updated_at = now
                job.status = JobStatus.INTERRUPTED
                job.error = DOUYIN_ITEM_EXPANSION_MESSAGE
                job.auth_message = None
                job.verification_url = target[0] if target else job.source_url
                job.active_item_id = None
                job.cancel_requested = False
                job.retryable = True
                job.discovery_complete = False
                job.finished_at = now
                changed = True
            if job.activity_message is not None or job.activity_started_at is not None:
                self._clear_activity_locked(job)
                changed = True
            changed |= self._sanitize_persisted_errors(job)
            changed |= self._backfill_job_issue_locked(job)
            if changed:
                job.refresh_counts()
                if legacy_douyin_result:
                    job.status = (
                        JobStatus.PARTIAL if job.completed_items else JobStatus.FAILED
                    )
                    job.retryable = False
                    job.error = LEGACY_DOUYIN_RESULT_ERROR
                    job.finished_at = utc_now()
                elif (
                    not migrated_incomplete_douyin_profile_queue
                    and job.total_items
                    and job.completed_items == job.total_items
                ):
                    if job.discovery_complete:
                        job.status = JobStatus.COMPLETED
                        job.error = None
                    else:
                        job.status = JobStatus.PARTIAL
                        job.error = job.warning or "Profile discovery may be incomplete"
                job.updated_at = utc_now()
                job.revision += 1
                self.store.save(job)
            self._jobs[job.id] = job
    def create_job(
        self,
        url: str,
        *,
        output_root: str | Path | None = None,
        cookie_browser: str | None = "chrome",
        cookie_profile: str | None = None,
        auto_start: bool = True,
        target_item_ids: list[str] | None = None,
    ) -> DownloadJob:
        url_info = identify_url(url)
        cookie_profile, cookie_profile_auto_selected = (
            self._resolve_xiaohongshu_chrome_profile(
                url_info.platform,
                cookie_browser,
                cookie_profile,
                cookie_profile_auto_selected=(
                    url_info.platform == Platform.XIAOHONGSHU
                    and cookie_browser == "chrome"
                    and cookie_profile is None
                ),
            )
        )
        root = Path(output_root or self.default_output_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        job = DownloadJob(
            id=uuid.uuid4().hex,
            source_url=url_info.url,
            platform=url_info.platform,
            source_kind=url_info.kind,
            output_root=str(root),
            cookie_browser=cookie_browser,
            cookie_profile=cookie_profile,
            cookie_profile_auto_selected=cookie_profile_auto_selected,
            selected_item_ids=list(target_item_ids) if target_item_ids is not None else None,
        )
        with self._lock:
            self._jobs[job.id] = job
            self.store.save(job)
        self._notify(job, "created")
        if auto_start:
            self.start_job(job.id, target_item_ids=target_item_ids)
        return self.get_job(job.id)
    def start_job(self, job_id: str, *, target_item_ids: list[str] | None = None) -> DownloadJob:
        # An explicit selection must never collapse into "download everything":
        # an empty list means the user deselected every item, not "no filter".
        if target_item_ids is not None and not target_item_ids:
            raise ItemNotRetryableError("请至少选择一项再开始下载")
        with self._lock:
            job = self._require_job(job_id)
            self._ensure_not_running(job_id)
            if not job.retryable:
                raise ItemNotRetryableError(job.error or "This task cannot be retried")
            if self._prepare_douyin_item_rediscovery_locked(job):
                targets = None
                rediscover = True
            elif self._has_douyin_profile_rediscovery_pending(job):
                targets = None
                rediscover = True
            elif self._has_douyin_profile_refresh_required(job):
                targets = None
                rediscover = True
            elif (
                (
                    job.platform == Platform.DOUYIN
                    and job.source_kind == SourceKind.PROFILE
                )
                or job.platform == Platform.KUAISHOU
            ) and self._should_rediscover_on_retry(job):
                targets = None
                rediscover = True
            elif self._has_xiaohongshu_binding_rediscovery_pending(job):
                targets = None
                rediscover = True
            elif self._has_xiaohongshu_profile_media_revalidation_pending(job):
                targets = None
                rediscover = True
            elif job.items:
                if target_item_ids is not None:
                    allowed = set(target_item_ids)
                elif job.selected_item_ids:
                    allowed = set(job.selected_item_ids)
                else:
                    allowed = None
                targets = [
                    item.id
                    for item in job.items
                    if item.retryable
                    and (allowed is None or item.id in allowed)
                    and item.status
                    in {
                        ItemStatus.QUEUED,
                        ItemStatus.FAILED,
                        ItemStatus.NEEDS_AUTH,
                        ItemStatus.CANCELLED,
                        ItemStatus.PAUSED,
                        ItemStatus.SKIPPED,
                    }
                ]
                for item in job.items:
                    if item.id in targets:
                        self._reset_item(item)
                    elif allowed is not None and item.status == ItemStatus.QUEUED:
                        # Only a queued item may be retired by a selection change.
                        # COMPLETED and PAUSED items must survive untouched, and
                        # FAILED/NEEDS_AUTH/CANCELLED keep their status so they
                        # stay retryable when the scope changes again.
                        item.status = ItemStatus.SKIPPED
                rediscover = False
                job.cancel_requested = False
                job.pause_requested = False
                if target_item_ids is not None:
                    job.selected_item_ids = list(targets)
            else:
                # A fresh job has no items yet; carry the user's selection through
                # discovery so only the selected items are downloaded afterwards.
                targets = list(target_item_ids) if target_item_ids is not None else (list(job.selected_item_ids) if job.selected_item_ids else None)
                rediscover = True
                job.cancel_requested = False
                job.pause_requested = False
                if target_item_ids is not None:
                    job.selected_item_ids = list(targets)
            self._submit_locked(job, targets, rediscover=rediscover)
        return self.get_job(job_id)
    def cancel_job(self, job_id: str) -> DownloadJob:
        with self._lock:
            job = self._require_job(job_id)
            if job.status in {
                JobStatus.COMPLETED,
                JobStatus.PARTIAL,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }:
                return job.model_copy(deep=True)
            job.cancel_requested = True
            self._clear_activity_locked(job)
            job.updated_at = utc_now()
            event = self._cancel_events.setdefault(job_id, threading.Event())
            event.set()
            future = self._futures.get(job_id)
            future_cancelled = bool(future and future.cancel())
            finalized = (
                job.status == JobStatus.NEEDS_AUTH
                or future is None
                or future.done()
                or future_cancelled
            )
            if finalized:
                self._mark_cancelled_locked(job)
                self._commit_locked(job)
            else:
                self._commit_locked(job)
        self._notify(
            self.get_job(job_id), "cancelled" if finalized else "cancel_requested"
        )
        return self.get_job(job_id)
    def pause_job(self, job_id: str) -> DownloadJob:
        with self._lock:
            job = self._require_job(job_id)
            if job.status not in ACTIVE_JOB_STATUSES:
                return job.model_copy(deep=True)
            job.pause_requested = True
            self._clear_activity_locked(job)
            job.updated_at = utc_now()
            event = self._cancel_events.setdefault(job_id, threading.Event())
            event.set()
            future = self._futures.get(job_id)
            future_cancelled = bool(future and future.cancel())
            finalized = (
                job.status == JobStatus.NEEDS_AUTH
                or future is None
                or future.done()
                or future_cancelled
            )
            if finalized:
                self._mark_paused_locked(job)
                self._commit_locked(job)
            else:
                self._commit_locked(job)
        self._notify(
            self.get_job(job_id), "paused" if finalized else "pause_requested"
        )
        return self.get_job(job_id)
    def resume_job(self, job_id: str) -> DownloadJob:
        with self._lock:
            job = self._require_job(job_id)
            self._ensure_not_running(job_id)
            if job.status != JobStatus.PAUSED and not any(item.status == ItemStatus.PAUSED for item in job.items):
                raise ItemNotRetryableError("只有已暂停的任务可以继续下载")
            job.cancel_requested = False
            job.pause_requested = False
            resumable_statuses = {
                ItemStatus.PAUSED,
                ItemStatus.QUEUED,
                ItemStatus.DOWNLOADING,
                ItemStatus.FAILED,
            }
            if job.status == JobStatus.PAUSED:
                # Older versions marked the in-flight item CANCELLED when the user
                # paused. Resuming such a task must retry that item instead of
                # silently skipping the file the user paused on.
                resumable_statuses.add(ItemStatus.CANCELLED)
            targets = [
                item.id
                for item in job.items
                if item.status in resumable_statuses
            ]
            for item in job.items:
                if item.id in targets:
                    item.status = ItemStatus.QUEUED
                    item.error = None
            self._submit_locked(job, targets, rediscover=not job.items)
        return self.get_job(job_id)
    def retry_item(self, job_id: str, item_id: str) -> DownloadJob:
        with self._lock:
            job = self._require_job(job_id)
            self._ensure_not_running(job_id)
            if not job.retryable:
                raise ItemNotRetryableError(job.error or "This task cannot be retried")
            if self._prepare_douyin_item_rediscovery_locked(job):
                self._submit_locked(job, None, rediscover=True)
                return self.get_job(job_id)
            if self._has_douyin_profile_rediscovery_pending(job):
                self._find_item(job, item_id)
                self._submit_locked(job, None, rediscover=True)
                return self.get_job(job_id)
            if self._has_douyin_profile_refresh_required(job):
                self._find_item(job, item_id)
                self._submit_locked(job, None, rediscover=True)
                return self.get_job(job_id)
            item = self._find_item(job, item_id)
            if item.status not in RETRYABLE_ITEM_STATUSES or not item.retryable:
                raise ItemNotRetryableError(
                    f"Item {item_id} is not failed, cancelled, or waiting for authentication"
                )
            rediscover = self._should_rediscover_on_retry(job)
            if rediscover:
                requested_items = (
                    None
                    if self._has_xiaohongshu_binding_rediscovery_pending(job)
                    else [item.id]
                )
                self._submit_locked(job, requested_items, rediscover=True)
                return self.get_job(job_id)
            targets = [item_id]
            self._reset_item(item)
            self._submit_locked(job, targets, rediscover=False)
        return self.get_job(job_id)
    def retry_failed(self, job_id: str) -> DownloadJob:
        with self._lock:
            job = self._require_job(job_id)
            self._ensure_not_running(job_id)
            if not job.retryable:
                raise ItemNotRetryableError(job.error or "This task cannot be retried")
            if self._prepare_douyin_item_rediscovery_locked(job):
                self._submit_locked(job, None, rediscover=True)
                return self.get_job(job_id)
            resume_queued = job.status in {
                JobStatus.NEEDS_AUTH,
                JobStatus.INTERRUPTED,
                JobStatus.PARTIAL,
            }
            targets = [
                item.id
                for item in job.items
                if item.retryable
                and (
                    item.status in RETRYABLE_ITEM_STATUSES
                    or (resume_queued and item.status == ItemStatus.QUEUED)
                )
            ]
            rediscover = self._should_rediscover_on_retry(job)
            profile_can_continue = rediscover and all(
                item.status == ItemStatus.COMPLETED
                or item.metadata.get(DOUYIN_PROFILE_REDISCOVERY_ITEM_MARKER) is True
                or item.metadata.get(DOUYIN_PROFILE_REFRESH_REQUIRED_MARKER) is True
                for item in job.items
            )
            if job.items and not targets and not profile_can_continue:
                raise ItemNotRetryableError("This task has no failed items to retry")
            if rediscover:
                self._submit_locked(job, None, rediscover=True)
                return self.get_job(job_id)
            if not job.items and job.status in {
                JobStatus.FAILED,
                JobStatus.NEEDS_AUTH,
                JobStatus.INTERRUPTED,
                JobStatus.CANCELLED,
            }:
                self._submit_locked(job, None, rediscover=True)
            elif not targets:
                raise ItemNotRetryableError("This task has no failed items to retry")
            else:
                for item in job.items:
                    if item.id in targets:
                        self._reset_item(item)
                self._submit_locked(job, targets, rediscover=False)
        return self.get_job(job_id)
    def get_job(self, job_id: str) -> DownloadJob:
        with self._lock:
            return self._require_job(job_id).model_copy(deep=True)
    def delete_job_record(self, job_id: str) -> None:
        """Remove a finished task record without modifying any downloaded files."""
        with self._lock:
            job = self._require_job(job_id)
            self._ensure_not_running(job_id)
            if job.status in ACTIVE_JOB_STATUSES or job.cancel_requested:
                raise JobBusyError("任务仍在运行，请等待停止后再移除记录。")
            self.store.delete(job_id)
            del self._jobs[job_id]
            self._futures.pop(job_id, None)
            self._cancel_events.pop(job_id, None)
            self._last_progress_save.pop(job_id, None)
    def list_jobs(self) -> list[DownloadJob]:
        with self._lock:
            return [
                job.model_copy(deep=True)
                for job in sorted(
                    self._jobs.values(),
                    key=lambda value: value.created_at,
                    reverse=True,
                )
            ]
    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)
    def remove_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)
    def shutdown(self, wait: bool = True, cancel_running: bool = False) -> None:
        if cancel_running:
            with self._lock:
                job_ids = [
                    job_id
                    for job_id, future in self._futures.items()
                    if not future.done()
                ]
            for job_id in job_ids:
                self.cancel_job(job_id)
        self._executor.shutdown(wait=wait, cancel_futures=False)
    def _submit_locked(
        self,
        job: DownloadJob,
        item_ids: list[str] | None,
        *,
        rediscover: bool,
    ) -> None:
        (
            job.cookie_profile,
            job.cookie_profile_auto_selected,
        ) = self._resolve_xiaohongshu_chrome_profile(
            job.platform,
            job.cookie_browser,
            job.cookie_profile,
            cookie_profile_auto_selected=job.cookie_profile_auto_selected,
        )
        cancel_event = threading.Event()
        self._cancel_events[job.id] = cancel_event
        job.cancel_requested = False
        job.status = JobStatus.QUEUED
        self._clear_activity_locked(job)
        job.error = None
        job.auth_message = None
        job.issue_code = None
        job.issue_message = None
        if job.platform == Platform.KUAISHOU:
            job.diagnostic_code = None
        job.verification_url = None
        job.finished_at = None
        job.updated_at = utc_now()
        if rediscover:
            job.discovery_complete = False
        self._commit_locked(job)
        future = self._executor.submit(
            self._run_job, job.id, item_ids, rediscover, cancel_event
        )
        self._futures[job.id] = future
    def _run_job(
        self,
        job_id: str,
        item_ids: list[str] | None,
        rediscover: bool,
        cancel_event: threading.Event,
    ) -> None:
        try:
            requested_item_ids = set(item_ids) if item_ids is not None else None
            requested_media_ids: set[str] = set()
            with self._lock:
                job = self._require_job(job_id)
                (
                    job.cookie_profile,
                    job.cookie_profile_auto_selected,
                ) = self._resolve_xiaohongshu_chrome_profile(
                    job.platform,
                    job.cookie_browser,
                    job.cookie_profile,
                    cookie_profile_auto_selected=job.cookie_profile_auto_selected,
                )
                if requested_item_ids is not None:
                    requested_media_ids = {
                        item.media_id
                        for item in job.items
                        if item.id in requested_item_ids and item.media_id
                    }
                job.started_at = job.started_at or utc_now()
                job.finished_at = None
                job.status = (
                    JobStatus.DISCOVERING if rediscover else JobStatus.DOWNLOADING
                )
                if rediscover:
                    job.activity_message = "Starting media discovery"
                    job.activity_started_at = utc_now()
                else:
                    self._clear_activity_locked(job)
                self._commit_locked(job)
            self._notify(self.get_job(job_id), "started")

            engine = self._engine_for_job(self.get_job(job_id))
            if rediscover:
                self._attach_discovery_callback(engine, job_id, cancel_event)
                job_snapshot = self.get_job(job_id)
                result = engine.discover(
                    job_snapshot.source_url,
                    job_snapshot.platform,
                    job_snapshot.source_kind,
                    should_cancel=cancel_event.is_set,
                )
                self._validate_discovery_result(job_snapshot, result)
                if cancel_event.is_set():
                    raise DownloadCancelledError("Task cancelled")
                with self._lock:
                    job = self._require_job(job_id)
                    job.author = result.author
                    previous_items = job.items
                    if job.platform == Platform.KUAISHOU and result.items:
                        source_kind = SourceKind(
                            result.items[0].metadata["kuaishou_source_kind"]
                        )
                        source_id = result.items[0].metadata["kuaishou_source_id"]
                        job.resolved_source_kind = source_kind
                        job.resolved_source_id = source_id
                    if job.platform == Platform.XIAOHONGSHU:
                        for xhs_item in [*previous_items, *result.items]:
                            self._normalize_xiaohongshu_item_identity(xhs_item)
                    if (
                        job.platform == Platform.DOUYIN
                        and job.source_kind == SourceKind.ITEM
                    ):
                        expected_id = MediaDownloader._douyin_video_id(job.source_url)
                        previous_items = [
                            item
                            for item in previous_items
                            if item.media_id == expected_id
                            and self._is_bound_douyin_item_url(
                                item.source_url,
                                job.source_url,
                                expected_id,
                            )
                        ][:1]
                    elif self._has_xiaohongshu_binding_rediscovery_pending(job):
                        previous_items = self._trusted_xiaohongshu_previous_items(
                            job,
                            previous_items,
                            result.items,
                        )
                        for discovered_item in result.items:
                            discovered_item.metadata.pop(
                                XIAOHONGSHU_BINDING_REDISCOVERY_MARKER,
                                None,
                            )
                        requested_item_ids = None
                        requested_media_ids.clear()
                    if (
                        job.platform == Platform.XIAOHONGSHU
                        and job.source_kind == SourceKind.SHORT_LINK
                    ):
                        short_binding = self._xiaohongshu_short_binding_from_items(
                            result.items
                        )
                        if short_binding:
                            job.resolved_source_kind = short_binding[0]
                            job.resolved_source_id = short_binding[1]
                    job.items = self._merge_discovered_items(
                        previous_items,
                        result.items,
                        retire_missing_douyin_profile_items=(
                            job.platform == Platform.DOUYIN
                            and job.source_kind == SourceKind.PROFILE
                            and result.discovery_complete
                        ),
                        preserve_unmatched_items=(
                            job.platform == Platform.KUAISHOU
                            and (
                                job.source_kind == SourceKind.PROFILE
                                or job.resolved_source_kind == SourceKind.PROFILE
                            )
                            and not result.discovery_complete
                        ),
                    )
                    job.output_dir = str(self.output_directory(job))
                    Path(job.output_dir).mkdir(parents=True, exist_ok=True)
                    if (
                        job.platform == Platform.DOUYIN
                        and job.source_kind == SourceKind.ITEM
                    ):
                        for item in job.items:
                            self._bind_douyin_item_metadata(job, item)
                    job.cookie_fallback_used = result.cookie_fallback_used
                    job.discovery_complete = result.discovery_complete
                    job.warning = result.warning
                    if result.warning:
                        self._record_issue_locked(job, result.warning)
                    job.status = JobStatus.DOWNLOADING
                    self._clear_activity_locked(job)
                    job.refresh_counts()
                    if requested_item_ids is None:
                        target_items = [
                            item
                            for item in job.items
                            if item.retryable
                            and (
                                item.status == ItemStatus.QUEUED
                                or item.status in RETRYABLE_ITEM_STATUSES
                            )
                        ]
                    else:
                        target_items = [
                            item
                            for item in job.items
                            if (
                                item.id in requested_item_ids
                                or (
                                    item.media_id
                                    and item.media_id in requested_media_ids
                                )
                            )
                            and item.retryable
                            and (
                                item.status == ItemStatus.QUEUED
                                or item.status in RETRYABLE_ITEM_STATUSES
                            )
                        ]
                    for item in target_items:
                        self._reset_item(item)
                    if requested_item_ids is not None and job.selected_item_ids is not None:
                        # Only a user selection may retire unselected queued items.
                        # A single-item retry must leave the other queued items
                        # queued, otherwise they can never be retried later.
                        selected_ids = {item.id for item in target_items}
                        for item in job.items:
                            if item.id not in selected_ids and item.status == ItemStatus.QUEUED:
                                item.status = ItemStatus.SKIPPED
                    if requested_item_ids is not None and not target_items:
                        raise ValueError(
                            "所选内容与本次解析结果不一致，请重新预览并确认后再下载。"
                        )
                    if job.selected_item_ids is not None:
                        # A user selection stays the scope of the whole task. Map it
                        # onto the freshly discovered identities and never shrink it
                        # to "only the item that was just retried", otherwise a
                        # single retry would report the task as fully completed.
                        stored_scope = set(job.selected_item_ids)
                        job.selected_item_ids = [
                            item.id for item in job.items if item.id in stored_scope
                        ] or [item.id for item in target_items]
                    self._commit_locked(job)
                    item_ids = [item.id for item in target_items]
                self._notify(self.get_job(job_id), "discovered")

            job_snapshot = self.get_job(job_id)
            if not job_snapshot.output_dir:
                output_dir = self.output_directory(job_snapshot)
                with self._lock:
                    job = self._require_job(job_id)
                    job.output_dir = str(output_dir)
                    Path(job.output_dir).mkdir(parents=True, exist_ok=True)
                    self._commit_locked(job)

            targets = set(item_ids or [])
            for item_id in list(item_ids or []):
                if cancel_event.is_set():
                    raise DownloadCancelledError("Task cancelled")
                binding_error: str | None = None
                with self._lock:
                    job = self._require_job(job_id)
                    item = self._find_item(job, item_id)
                    if item.id not in targets or item.status != ItemStatus.QUEUED:
                        continue
                    item.status = ItemStatus.DOWNLOADING
                    item.attempts += 1
                    item.error = None
                    item.auth_message = None
                    item.issue_code = None
                    if job.platform == Platform.KUAISHOU:
                        item.diagnostic_code = None
                    item.updated_at = utc_now()
                    job.active_item_id = item.id
                    job.status = JobStatus.DOWNLOADING
                    self._commit_locked(job)
                    item_snapshot = item.model_copy(deep=True)
                    item_snapshot.metadata["_job_id"] = job_id
                    output_dir = self.output_directory(job, item) if self.organized_output else job.output_path
                    # Only use short names inside an already selected work folder.
                    item_snapshot.metadata["_compact_asset_names"] = bool(
                        self.organized_output and item.media_type == MediaType.IMAGE
                    )
                    platform = job.platform
                    if (
                        platform == Platform.DOUYIN
                        and job.source_kind == SourceKind.PROFILE
                    ):
                        item_snapshot.metadata["profile_url"] = job.source_url
                    elif (
                        platform == Platform.DOUYIN
                        and job.source_kind == SourceKind.ITEM
                    ):
                        bound, _ = self._bind_douyin_item_metadata(
                            job,
                            item_snapshot,
                        )
                        if not bound:
                            binding_error = DOUYIN_ITEM_EXPANSION_MESSAGE
                    elif platform == Platform.XIAOHONGSHU and not (
                        self._is_bound_xiaohongshu_item(job, item_snapshot)
                    ):
                        binding_error = XIAOHONGSHU_ITEM_BINDING_ERROR
                self._notify(self.get_job(job_id), "item_started", item_id)

                try:
                    if binding_error:
                        raise MediaDownloadError(binding_error)
                    automatic_refresh_used = False
                    while True:
                        try:
                            outcome = engine.download_item(
                                item_snapshot,
                                platform,
                                output_dir,
                                callback=(
                                    lambda event, current=item_id: (
                                        self._on_engine_event(
                                            job_id,
                                            current,
                                            event,
                                        )
                                    )
                                ),
                                should_cancel=cancel_event.is_set,
                            )
                            break
                        except DouyinMediaRefreshRequiredError:
                            if automatic_refresh_used or platform != Platform.DOUYIN:
                                raise
                            automatic_refresh_used = True
                            compact_names = item_snapshot.metadata.get("_compact_asset_names") is True
                            item_snapshot = self._refresh_douyin_media_during_run(
                                job_id,
                                item_id,
                                engine,
                                cancel_event,
                            )
                            item_snapshot.metadata["_compact_asset_names"] = compact_names
                    with self._lock:
                        job = self._require_job(job_id)
                        item = self._find_item(job, item_id)
                        item.status = ItemStatus.COMPLETED
                        item.output_paths = outcome.output_paths
                        item.title = outcome.title or item.title
                        item.upload_date = outcome.upload_date or item.upload_date
                        item.author = outcome.author or item.author
                        item.media_type = outcome.media_type or item.media_type
                        item.selected_format = outcome.selected_format
                        item.resolution = outcome.resolution
                        if (
                            job.platform == Platform.DOUYIN
                            and job.source_kind == SourceKind.PROFILE
                        ):
                            item.metadata["profile_url"] = job.source_url
                            item.metadata["profile_owner_verified"] = True
                        if job.platform == Platform.XIAOHONGSHU:
                            item.metadata[XIAOHONGSHU_MEDIA_VERIFICATION_MARKER] = (
                                XIAOHONGSHU_MEDIA_VERIFICATION_VERSION
                            )
                            item.metadata.pop(
                                XIAOHONGSHU_PROFILE_MEDIA_REVALIDATION_MARKER,
                                None,
                            )
                        item.progress.percent = 100.0
                        item.error = None
                        item.issue_code = None
                        if job.platform == Platform.KUAISHOU:
                            item.diagnostic_code = None
                        item.updated_at = utc_now()
                        job.cookie_fallback_used |= outcome.cookie_fallback_used
                        job.active_item_id = None
                        job.refresh_counts()
                        self._commit_locked(job)
                    self._notify(self.get_job(job_id), "item_completed", item_id)
                except TemporaryAccessError as exc:
                    safe_message = safe_external_error_message(exc)
                    with self._lock:
                        job = self._require_job(job_id)
                        item = self._find_item(job, item_id)
                        cancelled = cancel_event.is_set() or job.cancel_requested
                        paused = job.pause_requested
                        if cancelled:
                            if paused:
                                self._mark_paused_locked(job)
                            else:
                                self._mark_cancelled_locked(job)
                        else:
                            item.status = ItemStatus.FAILED
                            item.error = safe_message
                            item.auth_message = None
                            self._record_issue_locked(
                                job,
                                safe_message,
                                item=item,
                                cause=exc,
                            )
                            item.retryable = True
                            if (
                                job.platform == Platform.DOUYIN
                                and job.source_kind == SourceKind.PROFILE
                            ):
                                item.metadata[
                                    DOUYIN_PROFILE_REFRESH_REQUIRED_MARKER
                                ] = True
                                job.discovery_complete = False
                            item.updated_at = utc_now()
                            job.status = JobStatus.INTERRUPTED
                            job.error = safe_message
                            job.auth_message = None
                            job.verification_url = None
                            job.active_item_id = None
                            job.finished_at = utc_now()
                            job.retryable = True
                            job.refresh_counts()
                        self._commit_locked(job)
                    self._notify(
                        self.get_job(job_id),
                        "cancelled" if cancelled else "interrupted",
                        item_id,
                    )
                    return
                except AuthenticationRequiredError as exc:
                    with self._lock:
                        job = self._require_job(job_id)
                        item = self._find_item(job, item_id)
                        cancelled = cancel_event.is_set() or job.cancel_requested
                        paused = job.pause_requested
                        if cancelled:
                            if paused:
                                self._mark_paused_locked(job)
                            else:
                                self._mark_cancelled_locked(job)
                        else:
                            safe_message = safe_external_error_message(exc)
                            item.status = ItemStatus.NEEDS_AUTH
                            item.error = safe_message
                            item.auth_message = safe_message
                            self._record_issue_locked(
                                job,
                                safe_message,
                                item=item,
                                authentication_required=True,
                                cause=exc,
                            )
                            item.updated_at = utc_now()
                            job.active_item_id = None
                            job.status = JobStatus.NEEDS_AUTH
                            job.auth_message = safe_message
                            job.verification_url = self._verification_url(
                                job,
                                exc.verification_url or item.source_url,
                            )
                            job.error = safe_message
                            job.finished_at = utc_now()
                            job.refresh_counts()
                        self._commit_locked(job)
                    self._notify(
                        self.get_job(job_id),
                        "cancelled" if cancelled else "needs_auth",
                        item_id,
                    )
                    return
                except DownloadCancelledError:
                    with self._lock:
                        job = self._require_job(job_id)
                        item = self._find_item(job, item_id)
                        # A pause and a cancel both interrupt the transfer, but only
                        # a cancel may mark the in-flight item CANCELLED. A paused
                        # item must stay resumable, otherwise "继续" starts with the
                        # next item and silently skips the file the user paused on.
                        if job.pause_requested and not job.cancel_requested:
                            item.status = ItemStatus.PAUSED
                            item.error = "已暂停"
                        else:
                            item.status = ItemStatus.CANCELLED
                            item.error = "Cancelled by user"
                        item.issue_code = None
                        item.updated_at = utc_now()
                        job.active_item_id = None
                        job.refresh_counts()
                        self._commit_locked(job)
                    raise
                except Exception as exc:
                    safe_message = safe_external_error_message(exc)
                    pause_queue = False
                    with self._lock:
                        job = self._require_job(job_id)
                        if cancel_event.is_set() or job.cancel_requested:
                            raise DownloadCancelledError("Task cancelled") from exc
                        item = self._find_item(job, item_id)
                        item.status = ItemStatus.FAILED
                        item.error = safe_message
                        item.auth_message = None
                        self._record_issue_locked(
                            job,
                            safe_message,
                            item=item,
                            cause=exc,
                        )
                        item.retryable = (
                            item.issue_code not in NON_RETRYABLE_SITE_ISSUES
                        )
                        pause_queue = item.issue_code in QUEUE_PAUSE_SITE_ISSUES or (
                            job.platform in {Platform.DOUYIN, Platform.KUAISHOU}
                            and item.issue_code == SiteIssueCode.SITE_RESPONSE_CHANGED
                        )
                        if (
                            binding_error == XIAOHONGSHU_ITEM_BINDING_ERROR
                            and self._is_xiaohongshu_direct_job(job)
                        ):
                            item.metadata[XIAOHONGSHU_BINDING_REDISCOVERY_MARKER] = True
                            job.discovery_complete = False
                        item.updated_at = utc_now()
                        job.active_item_id = None
                        if pause_queue:
                            job.status = JobStatus.INTERRUPTED
                            job.error = safe_message
                            job.auth_message = None
                            job.verification_url = None
                            job.finished_at = utc_now()
                            job.retryable = True
                        job.refresh_counts()
                        self._commit_locked(job)
                    self._notify(
                        self.get_job(job_id),
                        "interrupted" if pause_queue else "item_failed",
                        item_id,
                    )
                    if pause_queue:
                        return

            self._finish_job(job_id)
        except TemporaryAccessError as exc:
            safe_message = safe_external_error_message(exc)
            with self._lock:
                job = self._require_job(job_id)
                cancelled = cancel_event.is_set() or job.cancel_requested
                paused = job.pause_requested
                if cancelled:
                    if paused:
                        self._mark_paused_locked(job)
                    else:
                        self._mark_cancelled_locked(job)
                else:
                    job.status = JobStatus.FAILED
                    self._clear_activity_locked(job)
                    job.error = safe_message
                    job.auth_message = None
                    self._record_issue_locked(job, safe_message, cause=exc)
                    job.verification_url = None
                    job.active_item_id = None
                    job.finished_at = utc_now()
                    for item in job.items:
                        if item.status != ItemStatus.NEEDS_AUTH:
                            continue
                        item.status = ItemStatus.FAILED
                        item.error = safe_message
                        item.auth_message = None
                        item.issue_code = job.issue_code
                        item.retryable = True
                        item.updated_at = utc_now()
                    job.refresh_counts()
                self._commit_locked(job)
            self._notify(self.get_job(job_id), "cancelled" if cancelled else "failed")
        except AuthenticationRequiredError as exc:
            with self._lock:
                job = self._require_job(job_id)
                cancelled = cancel_event.is_set() or job.cancel_requested
                paused = job.pause_requested
                if cancelled:
                    if paused:
                        self._mark_paused_locked(job)
                    else:
                        self._mark_cancelled_locked(job)
                else:
                    safe_message = safe_external_error_message(exc)
                    job.status = JobStatus.NEEDS_AUTH
                    self._clear_activity_locked(job)
                    job.error = safe_message
                    job.auth_message = safe_message
                    self._record_issue_locked(
                        job,
                        safe_message,
                        authentication_required=True,
                        cause=exc,
                    )
                    job.verification_url = self._verification_url(
                        job,
                        exc.verification_url or job.source_url,
                    )
                    job.active_item_id = None
                    job.finished_at = utc_now()
                self._commit_locked(job)
            event_type = ("paused" if paused else "cancelled") if cancelled else "needs_auth"
            self._notify(self.get_job(job_id), event_type)
        except DownloadCancelledError:
            with self._lock:
                job = self._require_job(job_id)
                paused = job.pause_requested
                if paused:
                    self._mark_paused_locked(job)
                else:
                    self._mark_cancelled_locked(job)
                self._commit_locked(job)
            self._notify(self.get_job(job_id), "paused" if paused else "cancelled")
        except Exception as exc:
            safe_message = safe_external_error_message(exc)
            with self._lock:
                job = self._require_job(job_id)
                cancelled = cancel_event.is_set() or job.cancel_requested
                paused = job.pause_requested
                if cancelled:
                    if paused:
                        self._mark_paused_locked(job)
                    else:
                        self._mark_cancelled_locked(job)
                else:
                    job.status = JobStatus.FAILED
                    self._clear_activity_locked(job)
                    job.error = safe_message
                    job.auth_message = None
                    self._record_issue_locked(job, safe_message, cause=exc)
                    job.retryable = job.issue_code not in NON_RETRYABLE_SITE_ISSUES
                    job.verification_url = None
                    job.active_item_id = None
                    job.finished_at = utc_now()
                    job.refresh_counts()
                self._commit_locked(job)
            self._notify(self.get_job(job_id), "cancelled" if cancelled else "failed")
    def _engine_for_job(self, job: DownloadJob) -> MediaDownloader:
        config = replace(
            self.downloader_config,
            cookie_browser=job.cookie_browser,
            cookie_profile=job.cookie_profile,
        )
        return MediaDownloader(config)
    def _ensure_not_running(self, job_id: str) -> None:
        future = self._futures.get(job_id)
        if future is not None and not future.done():
            raise JobBusyError(f"Job {job_id} is already running")
