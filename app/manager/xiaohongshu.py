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


class XiaohongshuMixin:
    def bind_xiaohongshu_verification_profile(self, job_id: str) -> DownloadJob:
        """Bind a legacy Xiaohongshu verification action to one persisted profile."""
        with self._lock:
            job = self._require_job(job_id)
            if (
                job.platform == Platform.XIAOHONGSHU
                and job.status == JobStatus.NEEDS_AUTH
                and job.cookie_browser == "chrome"
            ):
                old_profile = job.cookie_profile
                old_auto_selected = job.cookie_profile_auto_selected
                (
                    job.cookie_profile,
                    job.cookie_profile_auto_selected,
                ) = self._resolve_xiaohongshu_chrome_profile(
                    job.platform,
                    job.cookie_browser,
                    job.cookie_profile,
                    cookie_profile_auto_selected=(
                        job.cookie_profile_auto_selected or job.cookie_profile is None
                    ),
                )
                if (
                    job.cookie_profile != old_profile
                    or job.cookie_profile_auto_selected != old_auto_selected
                ):
                    self._commit_locked(job)
            return job.model_copy(deep=True)
    @staticmethod
    def _resolve_xiaohongshu_chrome_profile(
        platform: Platform,
        cookie_browser: str | None,
        cookie_profile: str | None,
        *,
        cookie_profile_auto_selected: bool = False,
    ) -> tuple[str | None, bool]:
        if platform != Platform.XIAOHONGSHU:
            return cookie_profile, False
        if cookie_browser is None:
            return None, False
        if cookie_browser != "chrome":
            raise TemporaryAccessError(XIAOHONGSHU_COOKIE_BROWSER_ERROR)
        if cookie_profile is not None:
            profile = cookie_profile.strip()
            if chrome_profile_has_cookies(
                profile,
                "xiaohongshu.com",
                XIAOHONGSHU_AUTH_COOKIE_NAMES,
            ):
                return profile, cookie_profile_auto_selected
            if not cookie_profile_auto_selected:
                raise TemporaryAccessError(XIAOHONGSHU_CHROME_PROFILE_ERROR)
        profile = select_chrome_profile_with_cookies(
            "xiaohongshu.com",
            XIAOHONGSHU_AUTH_COOKIE_NAMES,
        )
        if profile is None:
            raise TemporaryAccessError(XIAOHONGSHU_CHROME_PROFILE_ERROR)
        return profile, True
    @staticmethod
    def _is_bound_xiaohongshu_item(
        job: DownloadJob,
        item: DownloadItem,
    ) -> bool:
        if job.platform != Platform.XIAOHONGSHU:
            return False
        media_id = str(item.media_id or "").strip()
        source_note_id = xiaohongshu_note_id(item.source_url)
        if not source_note_id or not media_id or media_id.lower() != source_note_id:
            return False
        binding_kind = job.source_kind
        binding_url = job.source_url
        if binding_kind == SourceKind.SHORT_LINK:
            binding_url = str(
                item.metadata.get("xiaohongshu_resolved_source_url") or ""
            )
            try:
                binding_kind = SourceKind(
                    str(item.metadata.get("xiaohongshu_resolved_source_kind") or "")
                )
            except ValueError:
                return False
        if binding_kind == SourceKind.ITEM:
            return xiaohongshu_note_id(binding_url) == source_note_id
        if binding_kind != SourceKind.PROFILE:
            return False
        expected_profile_id = xiaohongshu_profile_id(binding_url)
        return bool(
            expected_profile_id
            and item.metadata.get("profile_note_membership_verified") is True
            and item.metadata.get("xiaohongshu_profile_id") == expected_profile_id
        )
    @staticmethod
    def _xiaohongshu_short_item_binding(
        item: DownloadItem,
    ) -> tuple[SourceKind, str] | None:
        try:
            kind = SourceKind(
                str(item.metadata.get("xiaohongshu_resolved_source_kind") or "")
            )
        except ValueError:
            return None
        resolved_url = str(item.metadata.get("xiaohongshu_resolved_source_url") or "")
        if kind == SourceKind.ITEM:
            resolved_id = xiaohongshu_note_id(resolved_url)
        elif kind == SourceKind.PROFILE:
            resolved_id = xiaohongshu_profile_id(resolved_url)
        else:
            return None
        return (kind, resolved_id) if resolved_id else None
    @classmethod
    def _xiaohongshu_short_binding_from_items(
        cls,
        items: list[DownloadItem],
    ) -> tuple[SourceKind, str] | None:
        bindings = [cls._xiaohongshu_short_item_binding(item) for item in items]
        if not bindings or any(binding is None for binding in bindings):
            return None
        unique = set(bindings)
        return unique.pop() if len(unique) == 1 else None
    @classmethod
    def _xiaohongshu_expected_short_binding(
        cls,
        job: DownloadJob,
    ) -> tuple[SourceKind, str] | None:
        if job.source_kind != SourceKind.SHORT_LINK:
            return None
        if (
            job.resolved_source_kind in {SourceKind.ITEM, SourceKind.PROFILE}
            and str(job.resolved_source_id or "").strip()
        ):
            return job.resolved_source_kind, str(job.resolved_source_id).strip()
        return cls._xiaohongshu_short_binding_from_items(job.items)
    @staticmethod
    def _normalize_xiaohongshu_item_identity(item: DownloadItem) -> None:
        note_id = xiaohongshu_note_id(item.source_url)
        media_id = str(item.media_id or "").strip()
        if note_id and media_id.lower() == note_id:
            item.media_id = note_id
    @staticmethod
    def _is_xiaohongshu_direct_job(job: DownloadJob) -> bool:
        return job.platform == Platform.XIAOHONGSHU and job.source_kind in {
            SourceKind.ITEM,
            SourceKind.SHORT_LINK,
        }
    @classmethod
    def _has_xiaohongshu_binding_rediscovery_pending(
        cls,
        job: DownloadJob,
    ) -> bool:
        return cls._is_xiaohongshu_direct_job(job) and any(
            item.metadata.get(XIAOHONGSHU_BINDING_REDISCOVERY_MARKER) is True
            or item.error == XIAOHONGSHU_ITEM_BINDING_ERROR
            for item in job.items
        )
    @staticmethod
    def _has_xiaohongshu_profile_media_revalidation_pending(
        job: DownloadJob,
    ) -> bool:
        return (
            job.platform == Platform.XIAOHONGSHU
            and job.source_kind == SourceKind.PROFILE
            and any(
                item.metadata.get(XIAOHONGSHU_PROFILE_MEDIA_REVALIDATION_MARKER) is True
                for item in job.items
            )
        )
    @classmethod
    def _trusted_xiaohongshu_previous_items(
        cls,
        job: DownloadJob,
        previous: list[DownloadItem],
        discovered: list[DownloadItem],
    ) -> list[DownloadItem]:
        discovered_media_ids = {
            str(item.media_id or "").strip().lower()
            for item in discovered
            if str(item.media_id or "").strip()
        }
        trusted: list[DownloadItem] = []
        seen_media_ids: set[str] = set()
        for item in previous:
            media_id = str(item.media_id or "").strip().lower()
            if item.status == ItemStatus.COMPLETED and (
                not media_id or media_id not in discovered_media_ids
            ):
                completed_key = media_id or f"item:{item.id}"
                if completed_key not in seen_media_ids:
                    seen_media_ids.add(completed_key)
                    trusted.append(item)
                continue
            if (
                not media_id
                or media_id not in discovered_media_ids
                or media_id in seen_media_ids
                or item.metadata.get(XIAOHONGSHU_BINDING_REDISCOVERY_MARKER) is True
                or not cls._is_bound_xiaohongshu_item(job, item)
            ):
                continue
            seen_media_ids.add(media_id)
            trusted.append(item)
        return trusted
