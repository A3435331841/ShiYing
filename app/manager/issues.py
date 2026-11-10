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


class IssueMixin:
    @staticmethod
    def _sanitize_persisted_errors(job: DownloadJob) -> bool:
        changed = False
        for attribute in ("error", "warning", "auth_message", "issue_message"):
            value = getattr(job, attribute)
            if value is None:
                continue
            safe_value = safe_external_error_message(value)
            if safe_value != value:
                setattr(job, attribute, safe_value)
                changed = True
        for item in job.items:
            for attribute in ("error", "auth_message"):
                value = getattr(item, attribute)
                if value is None:
                    continue
                safe_value = safe_external_error_message(value)
                if safe_value != value:
                    setattr(item, attribute, safe_value)
                    changed = True
        return changed
    @staticmethod
    def _record_issue_locked(
        job: DownloadJob,
        message: str,
        *,
        item: DownloadItem | None = None,
        authentication_required: bool = False,
        cause: BaseException | None = None,
    ) -> None:
        code = classify_site_issue(
            cause or message,
            authentication_required=authentication_required,
        )
        if (
            job.platform == Platform.KUAISHOU
            and code == SiteIssueCode.UNKNOWN
            and _is_kuaishou_security_transfer_error(cause, message)
        ):
            code = SiteIssueCode.SECURITY_BLOCKED
        if item is not None:
            item.issue_code = code
            if job.platform == Platform.KUAISHOU:
                item.diagnostic_code = _kuaishou_cookie_diagnostic(cause)
        if code != SiteIssueCode.UNKNOWN or job.issue_code in {
            None,
            SiteIssueCode.UNKNOWN,
        }:
            job.issue_code = code
            job.issue_message = message
            if job.platform == Platform.KUAISHOU:
                job.diagnostic_code = _kuaishou_cookie_diagnostic(cause)
    @classmethod
    def _backfill_job_issue_locked(cls, job: DownloadJob) -> bool:
        """Backfill structured issue data for current and older persisted jobs."""

        changed = False
        for item in job.items:
            message = item.auth_message or item.error
            if job.platform == Platform.KUAISHOU:
                diagnostic = (
                    item.diagnostic_code
                    if item.diagnostic_code in COOKIE_DIAGNOSTIC_CODES
                    else _recover_cookie_diagnostic_code(message)
                )
                if item.diagnostic_code != diagnostic:
                    item.diagnostic_code = diagnostic
                    changed = True
            if not message:
                if item.issue_code is not None:
                    item.issue_code = None
                    changed = True
                continue
            if item.issue_code is not None:
                if item.issue_code in NON_RETRYABLE_SITE_ISSUES and item.retryable:
                    item.retryable = False
                    changed = True
                continue
            expected = classify_site_issue(
                message,
                authentication_required=item.status == ItemStatus.NEEDS_AUTH,
            )
            if item.issue_code != expected:
                item.issue_code = expected
                changed = True
            if expected in NON_RETRYABLE_SITE_ISSUES and item.retryable:
                item.retryable = False
                changed = True

        if job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED} and not job.warning:
            if job.issue_code is not None:
                job.issue_code = None
                changed = True
            if job.issue_message is not None:
                job.issue_message = None
                changed = True
            if job.platform == Platform.KUAISHOU and job.diagnostic_code is not None:
                job.diagnostic_code = None
                changed = True
            return changed

        if job.platform == Platform.KUAISHOU:
            diagnostic = (
                job.diagnostic_code
                if job.diagnostic_code in COOKIE_DIAGNOSTIC_CODES
                else _recover_cookie_diagnostic_code(
                    job.auth_message or job.issue_message or job.error
                )
            )
            if job.diagnostic_code != diagnostic:
                job.diagnostic_code = diagnostic
                changed = True

        if job.issue_code is not None and job.issue_message is not None:
            if (
                job.issue_code in NON_RETRYABLE_SITE_ISSUES
                and not any(item.retryable for item in job.items if item.error)
                and job.retryable
            ):
                job.retryable = False
                changed = True
            return changed

        summary_only = bool(
            job.error and re.fullmatch(r"\d+ item\(s\) failed", job.error.strip())
        )
        candidates = [job.auth_message, job.issue_message]
        candidates.extend(
            item.auth_message or item.error
            for item in job.items
            if item.auth_message or item.error
        )
        candidates.append(job.warning)
        if not summary_only:
            candidates.append(job.error)
        messages = [message for message in candidates if message]
        if not messages:
            return changed
        classified = [
            (
                message,
                classify_site_issue(
                    message,
                    authentication_required=(
                        job.status == JobStatus.NEEDS_AUTH
                        and message == job.auth_message
                    ),
                ),
            )
            for message in messages
        ]
        message, expected = next(
            (
                (candidate, code)
                for candidate, code in classified
                if code != SiteIssueCode.UNKNOWN
            ),
            classified[0],
        )
        if job.issue_code is None:
            job.issue_code = expected
            changed = True
        if job.issue_message is None:
            job.issue_message = message
            changed = True
        if (
            job.issue_code in NON_RETRYABLE_SITE_ISSUES
            and not any(item.retryable for item in job.items if item.error)
            and job.retryable
        ):
            job.retryable = False
            changed = True
        return changed
