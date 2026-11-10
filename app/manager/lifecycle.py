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


class LifecycleMixin:
    def _on_engine_event(self, job_id: str, item_id: str, event: EngineEvent) -> None:
        persist = event.event in {
            "asset_completed",
            "completed",
            "postprocessing",
            "metadata",
            "warning",
        }
        with self._lock:
            job = self._require_job(job_id)
            item = self._find_item(job, item_id)
            if event.event == "probing":
                item.progress = event.progress or TransferProgress()
            elif event.progress:
                item.progress = event.progress
            if event.title:
                item.title = event.title
            if event.upload_date:
                item.upload_date = event.upload_date
            if event.author:
                item.author = event.author
            if event.media_type:
                item.media_type = event.media_type
            if event.selected_format:
                item.selected_format = event.selected_format
            if event.resolution:
                item.resolution = event.resolution
            if event.output_paths:
                if event.event == "asset_completed":
                    item.output_paths = list(
                        dict.fromkeys([*item.output_paths, *event.output_paths])
                    )
                else:
                    item.output_paths = event.output_paths
            if (
                job.platform == Platform.KUAISHOU
                and event.event == "asset_completed"
                and event.asset_records
            ):
                previous = item.metadata.get(KUAISHOU_SAVED_ASSETS_KEY)
                receipts: dict[tuple[str, int], dict[str, object]] = {}
                for raw in [
                    *(previous if isinstance(previous, list) else []),
                    *event.asset_records,
                ]:
                    record = _public_kuaishou_saved_asset(raw)
                    if record is not None and record["media_id"] == item.media_id:
                        receipts[(record["media_kind"], record["index"])] = record
                item.metadata[KUAISHOU_SAVED_ASSETS_KEY] = list(receipts.values())
            if event.event == "postprocessing":
                item.status = ItemStatus.POSTPROCESSING
            elif event.event == "downloading":
                item.status = ItemStatus.DOWNLOADING
            if event.event == "probing" and event.message:
                item.progress.filename = safe_external_error_message(event.message)
            elif event.message:
                job.warning = safe_external_error_message(event.message)
            elif event.cookie_fallback_used and not job.warning:
                job.warning = (
                    "Chrome cookies could not be read. Anonymous access was used, "
                    "so the result may be incomplete or below the highest quality."
                )
            job.cookie_fallback_used |= event.cookie_fallback_used
            item.updated_at = utc_now()
            job.updated_at = utc_now()
            job.revision += 1
            now = time.monotonic()
            progress_save_interval = (
                5.0
                if job.platform == Platform.DOUYIN
                and job.source_kind == SourceKind.PROFILE
                and len(job.items) >= 100
                else 0.5
            )
            if (
                persist
                or now - self._last_progress_save.get(job_id, 0.0)
                >= progress_save_interval
            ):
                self.store.save(job)
                self._last_progress_save[job_id] = now
            snapshot = job.model_copy(deep=True)
            listeners = list(self._listeners)
            manager_event = ManagerEvent(
                job_id=job_id,
                item_id=item_id,
                event=event.event,
                revision=job.revision,
            )
        for listener in listeners:
            try:
                listener(manager_event, snapshot)
            except Exception:
                continue
    def _attach_discovery_callback(
        self,
        engine: Any,
        job_id: str,
        cancel_event: threading.Event,
    ) -> None:
        try:
            if not hasattr(engine, "discovery_callback"):
                return
            engine.discovery_callback = lambda event: self._on_discovery_event(
                job_id,
                event,
                cancel_event,
            )
        except (AttributeError, TypeError):
            return
    def _on_discovery_event(
        self,
        job_id: str,
        event: EngineEvent,
        cancel_event: threading.Event,
    ) -> None:
        if event.event != "probing" or not event.message:
            return
        message = safe_external_error_message(event.message)
        if not message:
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if (
                job is None
                or self._cancel_events.get(job_id) is not cancel_event
                or cancel_event.is_set()
                or job.cancel_requested
                or job.status != JobStatus.DISCOVERING
            ):
                return
            if job.activity_message != message or job.activity_started_at is None:
                job.activity_started_at = utc_now()
            job.activity_message = message
            job.refresh_counts()
            job.updated_at = utc_now()
            job.revision += 1
            now = time.monotonic()
            if now - self._last_progress_save.get(job_id, 0.0) >= 1.0:
                self.store.save(job)
                self._last_progress_save[job_id] = now
            snapshot = job.model_copy(deep=True)
        self._notify(snapshot, "activity")
    def _finish_job(self, job_id: str) -> None:
        with self._lock:
            job = self._require_job(job_id)
            job.active_item_id = None
            self._clear_activity_locked(job)
            job.cancel_requested = False
            # 暂停请求若在最后一个条目收尾后才到达，worker 不再检查它；
            # 任务已完成时该标志必须清除，否则界面与磁盘残留"暂停请求中"。
            job.pause_requested = False
            job.refresh_counts()
            if (
                job.total_items
                and job.completed_items == job.total_items
                and not job.discovery_complete
            ):
                job.status = JobStatus.PARTIAL
                job.error = job.warning or "Profile discovery may be incomplete"
            elif (
                job.total_items
                and job.completed_items == job.total_items
                and job.warning
                and DOUYIN_LIVE_PHOTO_STATIC_FALLBACK_WARNING in job.warning
            ):
                job.status = JobStatus.PARTIAL
                job.error = job.warning
            elif job.total_items and job.completed_items == job.total_items:
                job.status = JobStatus.COMPLETED
                job.error = None
                if job.warning:
                    self._record_issue_locked(job, job.warning)
                else:
                    job.issue_code = None
                    job.issue_message = None
            elif job.completed_items and job.failed_items:
                job.status = JobStatus.PARTIAL
                job.error = f"{job.failed_items} item(s) failed"
            elif job.failed_items:
                job.status = JobStatus.FAILED
                job.error = f"{job.failed_items} item(s) failed"
            elif not job.total_items:
                job.status = JobStatus.FAILED
                job.error = "No downloadable items were found"
            else:
                job.status = JobStatus.PARTIAL
                job.error = "Some items were not processed"
            if job.status in {JobStatus.FAILED, JobStatus.PARTIAL}:
                self._backfill_job_issue_locked(job)
            job.finished_at = utc_now()
            self._commit_locked(job)
            # The worker is done; drop the bookkeeping so long-lived processes
            # don't accumulate an entry per finished job.
            self._futures.pop(job_id, None)
            self._cancel_events.pop(job_id, None)
        self._notify(self.get_job(job_id), "finished")
    @staticmethod
    def _mark_cancelled_locked(job: DownloadJob) -> None:
        now = utc_now()
        job.status = JobStatus.CANCELLED
        LifecycleMixin._clear_activity_locked(job)
        job.error = "Cancelled by user"
        job.auth_message = None
        job.issue_code = None
        job.issue_message = None
        if job.platform == Platform.KUAISHOU:
            job.diagnostic_code = None
        job.verification_url = None
        job.active_item_id = None
        job.cancel_requested = False
        job.pause_requested = False
        job.finished_at = now
        for item in job.items:
            if item.status in {
                ItemStatus.QUEUED,
                ItemStatus.DOWNLOADING,
                ItemStatus.POSTPROCESSING,
                ItemStatus.NEEDS_AUTH,
            }:
                item.status = ItemStatus.CANCELLED
                item.error = "Cancelled by user"
                item.auth_message = None
                item.issue_code = None
                if job.platform == Platform.KUAISHOU:
                    item.diagnostic_code = None
                item.updated_at = now
        job.refresh_counts()
    @staticmethod
    def _mark_paused_locked(job: DownloadJob) -> None:
        now = utc_now()
        job.status = JobStatus.PAUSED
        LifecycleMixin._clear_activity_locked(job)
        job.error = "已暂停"
        job.auth_message = None
        job.issue_code = None
        job.issue_message = None
        if job.platform == Platform.KUAISHOU:
            job.diagnostic_code = None
        job.verification_url = None
        job.active_item_id = None
        job.cancel_requested = False
        job.pause_requested = False
        # PAUSED is resumable, so it must not carry a finished timestamp.
        job.finished_at = None
        for item in job.items:
            if item.status in {
                ItemStatus.QUEUED,
                ItemStatus.DOWNLOADING,
                ItemStatus.POSTPROCESSING,
                ItemStatus.NEEDS_AUTH,
            }:
                item.status = ItemStatus.PAUSED
                item.error = "已暂停"
                item.auth_message = None
                item.issue_code = None
                if job.platform == Platform.KUAISHOU:
                    item.diagnostic_code = None
                item.updated_at = now
        job.refresh_counts()
    @staticmethod
    def _clear_activity_locked(job: DownloadJob) -> None:
        job.activity_message = None
        job.activity_started_at = None
    @classmethod
    def _verification_url(cls, job: DownloadJob, fallback: str) -> str:
        target = cls._douyin_item_target(job)
        return target[0] if target else fallback
    def _require_job(self, job_id: str) -> DownloadJob:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise JobNotFoundError(job_id) from exc
    @staticmethod
    def _find_item(job: DownloadJob, item_id: str) -> DownloadItem:
        for item in job.items:
            if item.id == item_id:
                return item
        raise ItemNotFoundError(item_id)
    @staticmethod
    def _reset_item(item: DownloadItem) -> None:
        item.status = ItemStatus.QUEUED
        item.error = None
        item.auth_message = None
        item.issue_code = None
        item.diagnostic_code = None
        item.progress = item.progress.model_copy(
            update={
                "downloaded_bytes": 0,
                "total_bytes": None,
                "percent": None,
                "speed_bytes_per_second": None,
                "eta_seconds": None,
            }
        )
        item.updated_at = utc_now()
    def _commit_locked(self, job: DownloadJob) -> None:
        job.refresh_counts()
        job.updated_at = utc_now()
        job.revision += 1
        self.store.save(job)
    def _notify(self, job: DownloadJob, event: str, item_id: str | None = None) -> None:
        with self._lock:
            listeners = list(self._listeners)
        manager_event = ManagerEvent(
            job_id=job.id,
            item_id=item_id,
            event=event,
            revision=job.revision,
        )
        for listener in listeners:
            try:
                listener(manager_event, job.model_copy(deep=True))
            except Exception:
                continue
