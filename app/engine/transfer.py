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
from ..file_publish import (
    are_files_identical,
    publish_no_replace,
    publish_or_reuse_identical,
    publish_pair_image,
    publish_unique,
)

# 非抖音传输的网络类瞬态重试上限与退避基数（参照 DOUYIN_TRANSFER_ATTEMPTS）。
TRANSFER_RETRY_ATTEMPTS = 3
TRANSFER_RETRY_BASE_SECONDS = 0.8
from ..xiaohongshu import RemoteAsset, discover_profile as discover_xhs_profile
from ..xiaohongshu import (
    _XiaohongshuRedirectRejected,
    _open_xiaohongshu_response,
)
from ..xiaohongshu import is_trusted_xiaohongshu_asset_url
from ..xiaohongshu import parse_note as parse_xhs_note

from .common import *  # noqa: F401,F403


class _PairedAssetConflictError(MediaDownloadError):
    """The exact paired destination exists but cannot be safely reused."""


def _publish_paired_or_reuse_identical(temporary: Path, destination: Path) -> Path:
    """Publish at the exact paired path, or reuse it only after a full comparison."""
    try:
        publish_no_replace(temporary, destination)
    except FileExistsError as exc:
        if (
            destination.is_symlink()
            or not destination.is_file()
            or not are_files_identical(temporary, destination)
            or destination.is_symlink()
        ):
            raise _PairedAssetConflictError(
                "Paired media destination already exists with different content "
                "or is not a regular file"
            ) from exc
        temporary.unlink()
    return destination


class TransferMixin:
    def _decode_local_image(self, path: Path, *, should_cancel: CancelCallback) -> None:
        """Fully decode a saved image so a reused file is not trusted by header alone.

        Raises ``MediaDownloadError`` when FFmpeg is missing, cannot start, or
        reports a decode error. A truncated or corrupt image must be re-downloaded
        rather than silently reused as a completed album member.
        """
        executable = self._find_ffmpeg_executable()
        if not executable:
            raise MediaDownloadError(FFPROBE_REQUIRED_MESSAGE)
        try:
            process = subprocess.Popen(
                [
                    executable,
                    "-v",
                    "error",
                    "-xerror",
                    "-err_detect",
                    "explode",
                    "-i",
                    str(path),
                    "-f",
                    "null",
                    "-",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise MediaDownloadError(FFPROBE_START_MESSAGE) from exc
        deadline = time.monotonic() + DOUYIN_FFPROBE_FILE_TIMEOUT_SECONDS
        while True:
            if should_cancel():
                self._terminate_process(process)
                raise DownloadCancelledError("Task cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate_process(process)
                raise MediaDownloadError("Image decode timed out while verifying a saved file")
            try:
                _, stderr = process.communicate(
                    timeout=min(DOUYIN_PROCESS_POLL_SECONDS, remaining),
                )
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode != 0:
            detail = safe_external_error_message(
                MediaDownloadError((stderr or b"").decode("utf-8", "replace")[:400])
            )
            raise MediaDownloadError(f"Saved image failed full decode verification: {detail}")
    def _ytdlp_parts_directory(self, item: DownloadItem, output_dir: Path) -> tuple[Path, Path | None]:
        job_scope = safe_component(str(item.metadata.get("_job_id") or "standalone"), fallback="standalone", limit=80)
        item_scope = safe_component(item.id, fallback="item", limit=80)
        if self.config.temporary_root:
            root = Path(self.config.temporary_root).expanduser().absolute()
            # One stable directory per transfer; output path also isolates standalone calls.
            key = hashlib.sha256(f"{job_scope}\0{item_scope}\0{output_dir}".encode("utf-8")).hexdigest()[:32]
            return root / key, root
        return output_dir / ".parts" / job_scope / item_scope, None
    @staticmethod
    def _cleanup_ytdlp_parts_dir(
        parts_dir: Path,
        output_dir: Path,
        *,
        remove_contents: bool,
        temporary_root: Path | None = None,
    ) -> None:
        parts_root = temporary_root if temporary_root is not None else output_dir / ".parts"
        try:
            relative = parts_dir.relative_to(parts_root)
        except ValueError:
            return
        if len(relative.parts) != (1 if temporary_root is not None else 2):
            return
        scoped_directories = (parts_root, parts_dir) if temporary_root is not None else (parts_root, parts_dir.parent, parts_dir)
        try:
            if any(
                directory.is_symlink() or directory.resolve() != directory
                for directory in scoped_directories
            ):
                return
        except OSError:
            return
        if remove_contents:
            with contextlib.suppress(OSError):
                shutil.rmtree(parts_dir)
        else:
            with contextlib.suppress(OSError):
                parts_dir.rmdir()
        for directory in (() if temporary_root is not None else (parts_dir.parent, parts_root)):
            with contextlib.suppress(OSError):
                directory.rmdir()
    @staticmethod
    def _prepare_ytdlp_parts_dir(parts_dir: Path, output_dir: Path, *, temporary_root: Path | None = None) -> None:
        parts_root = temporary_root if temporary_root is not None else output_dir / ".parts"
        try:
            relative = parts_dir.relative_to(parts_root)
        except ValueError as exc:
            raise MediaDownloadError(
                "The temporary download directory is outside the output directory"
            ) from exc
        if len(relative.parts) != (1 if temporary_root is not None else 2):
            raise MediaDownloadError("The temporary download directory is invalid")
        directories = (parts_root, parts_dir) if temporary_root is not None else (parts_root, parts_dir.parent, parts_dir)
        for directory in directories:
            try:
                if directory.is_symlink():
                    raise MediaDownloadError(
                        "The temporary download directory contains a symbolic link"
                    )
                directory.mkdir(exist_ok=True)
                if (
                    directory.is_symlink()
                    or not directory.is_dir()
                    or directory.resolve() != directory
                ):
                    raise MediaDownloadError(
                        "The temporary download directory could not be verified"
                    )
            except MediaDownloadError:
                raise
            except OSError as exc:
                raise MediaDownloadError(
                    "The temporary download directory could not be created"
                ) from exc
    def _download_with_ytdlp(
        self,
        item: DownloadItem,
        output_dir: Path,
        *,
        platform: Platform,
        callback: EventCallback | None,
        should_cancel: CancelCallback,
    ) -> DownloadOutcome:
        final_paths: list[str] = []
        verification_url = str(
            item.metadata.get("profile_url")
            or item.metadata.get("verification_url")
            or item.source_url
        )
        expected_douyin_id: str | None = None
        expected_douyin_profile_id: str | None = None
        if platform == Platform.DOUYIN:
            expected_douyin_profile_id = self._douyin_profile_id(
                str(item.metadata.get("profile_url") or "")
            )
            expected_douyin_id = item.media_id or self._douyin_video_id(item.source_url)
            source_id = self._douyin_video_id(item.source_url)
            if not expected_douyin_id or source_id != expected_douyin_id:
                self._raise_douyin_mismatch(
                    expected_douyin_id or "unknown",
                    source_id or "missing",
                    verification_url,
                )

        def emit(event: EngineEvent) -> None:
            if callback:
                callback(event)

        def progress_hook(data: dict[str, Any]) -> None:
            if should_cancel():
                raise DownloadCancelled("Task cancelled")
            status = str(data.get("status") or "")
            info = data.get("info_dict") or {}
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = int(data.get("downloaded_bytes") or 0)
            percent = None
            if total:
                percent = min(100.0, downloaded * 100.0 / int(total))
            emit(
                EngineEvent(
                    event=status or "progress",
                    progress=TransferProgress(
                        downloaded_bytes=downloaded,
                        total_bytes=int(total) if total else None,
                        percent=percent,
                        speed_bytes_per_second=data.get("speed"),
                        eta_seconds=data.get("eta"),
                        fragment_index=data.get("fragment_index"),
                        fragment_count=data.get("fragment_count"),
                        filename=data.get("filename"),
                    ),
                    title=info.get("title"),
                    upload_date=self._normalize_date(info.get("upload_date")),
                    author=self._author_from_info(info, fallback=None),
                    media_type=self._media_type(info),
                )
            )

        def postprocessor_hook(data: dict[str, Any]) -> None:
            if should_cancel():
                raise DownloadCancelled("Task cancelled")
            info = data.get("info_dict") or {}
            emit(
                EngineEvent(
                    event="postprocessing",
                    title=info.get("title"),
                    selected_format=self._format_id(info),
                    resolution=self._resolution(info),
                )
            )

        def post_hook(filename: str) -> None:
            final_paths.append(str(Path(filename).resolve()))

        def match_filter(info: dict[str, Any], *, incomplete: bool = False) -> None:
            if not expected_douyin_id:
                return None
            if incomplete and not info.get("id"):
                return None
            self._validate_douyin_info(
                info,
                expected_douyin_id,
                verification_url,
                expected_douyin_profile_id,
            )
            return None

        parts_dir, temporary_root = self._ytdlp_parts_directory(item, output_dir)
        # Keep yt-dlp's finalization and merge away from user files. The parts
        # directory remains separate so interrupted downloads can still resume.
        staging_dir = (
            Path(tempfile.mkdtemp(prefix=".shiying-work-", dir=output_dir))
            if not expected_douyin_id else None
        )

        def operation(use_cookies: bool) -> tuple[dict[str, Any], list[str]]:
            final_paths.clear()
            self._prepare_ytdlp_parts_dir(parts_dir, output_dir, temporary_root=temporary_root)
            logger = _YdlLogger(callback)
            options = {
                **(
                    self._douyin_ytdlp_options(use_cookies)
                    if expected_douyin_id
                    else self._base_options(use_cookies)
                ),
                **self._download_format_options(platform),
                "paths": {
                    "home": str(staging_dir or output_dir),
                    "temp": str(parts_dir),
                },
                "outtmpl": {"default": OUTPUT_TEMPLATE},
                "windowsfilenames": True,
                "continuedl": platform != Platform.DOUYIN,
                "overwrites": True,
                "ignoreerrors": False,
                "noplaylist": True,
                "concurrent_fragment_downloads": self.config.concurrent_fragments,
                "progress_hooks": [progress_hook],
                "postprocessor_hooks": [postprocessor_hook],
                "post_hooks": [post_hook],
                "logger": logger,
            }
            if expected_douyin_id:
                options["match_filter"] = match_filter
            with YoutubeDL(options) as ydl:
                ydl.add_post_processor(
                    _SafeFilenamePostProcessor(
                        ydl,
                        title_byte_limit=self.config.filename_limit,
                        fallback_media_id=item.media_id or item.id,
                    ),
                    when="pre_process",
                )
                if expected_douyin_id:
                    raw_result = self._extract_douyin_raw_info(
                        ydl,
                        item.source_url,
                        expected_id=expected_douyin_id,
                        expected_profile_id=expected_douyin_profile_id,
                        verification_url=verification_url,
                        profile_metadata=item.metadata,
                        fallback_title=item.title,
                        should_cancel=should_cancel,
                        status_callback=(
                            lambda message: emit(
                                EngineEvent(event="probing", message=message)
                            )
                        ),
                    )
                    self._validate_douyin_info(
                        raw_result,
                        expected_douyin_id,
                        verification_url,
                        expected_douyin_profile_id,
                    )
                    probe_added = self._add_douyin_probe_formats(
                        ydl,
                        raw_result,
                        expected_id=expected_douyin_id,
                        verification_url=verification_url,
                        expected_profile_id=expected_douyin_profile_id,
                        callback=emit,
                        should_cancel=should_cancel,
                    )
                    if not probe_added:
                        probe_failure = str(
                            raw_result.get("_douyin_probe_failure")
                            or "no playable candidate was returned"
                        )
                        raise DownloadError(
                            "Douyin media was discovered, but its highest "
                            "quality could not be verified. Retry this item; no "
                            "lower-quality fallback was downloaded. Probe details: "
                            f"{probe_failure}."
                        )
                    selected = self._highest_verified_douyin_format(raw_result)
                    if not selected:
                        raise DownloadError(
                            "Douyin quality probes returned no verified downloadable "
                            "format"
                        )
                    selected_size = int(selected.get("filesize") or 0)
                    selected_bit_rate = int(
                        selected.get("_douyin_probe_bit_rate")
                        or round(float(selected.get("tbr") or 0) * 1_000)
                    )
                    redirect_source_url = str(
                        selected.get("_douyin_probe_source_url") or ""
                    ).strip()
                    selected_candidates = list(
                        dict.fromkeys(
                            value
                            for value in (
                                redirect_source_url,
                                str(selected["url"]),
                                *(selected.get("_douyin_probe_source_urls") or []),
                            )
                            if isinstance(value, str) and value
                        )
                    )
                    selected_candidates = self._bounded_douyin_transfer_candidates(
                        selected_candidates
                    )
                    title = str(raw_result.get("title") or item.title or "").strip()
                    if not title or title == expected_douyin_id or title.isdigit():
                        title = "Untitled Douyin video"
                    selected_asset = RemoteAsset(
                        candidates=selected_candidates,
                        index=1,
                        width=int(selected.get("width") or 0) or None,
                        height=int(selected.get("height") or 0) or None,
                        size=selected_size or None,
                        format_id=str(selected.get("format_id") or "verified"),
                        duration=(
                            self._float_or_none(selected.get("duration"))
                            or self._float_or_none(raw_result.get("duration"))
                        ),
                        bit_rate=selected_bit_rate or None,
                        video_codec=str(selected.get("vcodec") or "").lower() or None,
                        audio_codec=str(selected.get("acodec") or "").lower() or None,
                        probe_prefix_size=int(
                            selected.get("_douyin_probe_prefix_size") or 0
                        )
                        or None,
                        probe_prefix_sha256=str(
                            selected.get("_douyin_probe_prefix_sha256") or ""
                        )
                        or None,
                        redirect_source_url=redirect_source_url or None,
                    )
                    path, chosen = self._download_first_available_asset(
                        ydl,
                        [selected_asset],
                        output_dir,
                        self._normalize_date(raw_result.get("upload_date")),
                        title,
                        expected_douyin_id,
                        item.source_url,
                        platform=Platform.DOUYIN,
                        media_type=MediaType.VIDEO,
                        callback=callback,
                        should_cancel=should_cancel,
                        verify_declared_dimensions=True,
                        require_quality_fingerprint=True,
                    )
                    result = {
                        **raw_result,
                        "title": title,
                        "format_id": chosen.format_id,
                        "width": chosen.width,
                        "height": chosen.height,
                        "filepath": str(path),
                        "requested_formats": [dict(selected)],
                        "requested_downloads": [
                            {**dict(selected), "filepath": str(path)}
                        ],
                        "_verified_local_resolution": (
                            f"{chosen.width}x{chosen.height}"
                        ),
                    }
                    candidates = [str(path)]
                else:
                    result = ydl.extract_info(item.source_url, download=True)
                    if not isinstance(result, dict):
                        raise DownloadError("The URL returned no downloadable media")
                    candidates = self._paths_from_info(result, ydl)
                if not isinstance(result, dict):
                    raise DownloadError("The URL returned no downloadable media")
                if expected_douyin_id:
                    self._validate_douyin_info(
                        result,
                        expected_douyin_id,
                        verification_url,
                        expected_douyin_profile_id,
                    )
            paths = [*final_paths, *candidates]
            return result, paths

        operation_succeeded = False
        try:
            try:
                (info, paths), fallback = self._run_with_cookie_fallback(
                    operation, url=item.source_url
                )
                if staging_dir is not None:
                    staged_paths = self._existing_unique_paths(paths)
                    if not staged_paths:
                        raise MediaDownloadError("yt-dlp finished but no output file was found")
                    published = []
                    for value in staged_paths:
                        source = Path(value)
                        if source.is_symlink() or source.resolve().parent != staging_dir.resolve():
                            raise MediaDownloadError("yt-dlp returned an unexpected output path")
                        if should_cancel():
                            raise DownloadCancelledError("Task cancelled")
                        published.append(
                            str(publish_or_reuse_identical(source, output_dir / source.name))
                        )
                    paths = published
                operation_succeeded = True
            except AuthenticationRequiredError as exc:
                if (
                    platform == Platform.DOUYIN
                    and exc.verification_url != verification_url
                ):
                    raise AuthenticationRequiredError(
                        str(exc),
                        verification_url=verification_url,
                        issue_code=exc.issue_code,
                    ) from exc
                raise
        finally:
            try:
                self._cleanup_ytdlp_parts_dir(
                    parts_dir,
                    output_dir,
                    remove_contents=(platform == Platform.DOUYIN or operation_succeeded),
                    temporary_root=temporary_root,
                )
            finally:
                if staging_dir is not None:
                    # 成功与失败都整体清理：失败残留的已暂存文件可由 .part
                    # 缓存或重试重新获得，而随机命名的隐藏目录一旦滞留
                    # （rmdir 只删得动空目录）就永远不会再被访问。
                    shutil.rmtree(staging_dir, ignore_errors=True)
        unique_paths = self._existing_unique_paths(paths)
        if not unique_paths:
            raise MediaDownloadError("yt-dlp finished but no output file was found")
        outcome = DownloadOutcome(
            output_paths=unique_paths,
            title=info.get("title"),
            upload_date=self._normalize_date(info.get("upload_date")),
            author=self._author_from_info(info, fallback=None),
            media_type=self._media_type(info),
            selected_format=self._format_id(info),
            resolution=(
                str(info.get("_verified_local_resolution"))
                if expected_douyin_id
                else self._resolution(info)
            ),
            cookie_fallback_used=fallback,
        )
        emit(
            EngineEvent(
                event="completed",
                title=outcome.title,
                upload_date=outcome.upload_date,
                author=outcome.author,
                media_type=outcome.media_type,
                selected_format=outcome.selected_format,
                resolution=outcome.resolution,
                output_paths=outcome.output_paths,
                cookie_fallback_used=fallback,
            )
        )
        return outcome

    def _reuse_paired_douyin_probe_file(
        self,
        probe_path: Path,
        asset: RemoteAsset,
        output_dir: Path,
        paired_stem: str,
        *,
        callback: EventCallback | None,
        should_cancel: CancelCallback,
        progress_index: int | None,
        progress_count: int | None,
    ) -> tuple[Path, RemoteAsset]:
        """Verify a complete probe and publish it only at its paired stem."""
        if should_cancel():
            raise DownloadCancelledError("Task cancelled")
        try:
            file_size = probe_path.stat().st_size
        except OSError as exc:
            raise MediaDownloadError(
                "The verified Douyin probe file could not be read"
            ) from exc
        path = (output_dir / "motion.mp4").with_name(paired_stem + ".mp4")
        started_at = time.monotonic()

        def emit_progress(completed: bool) -> None:
            if not callback:
                return
            fraction = 1.0 if completed else 0.0
            if (
                progress_index is not None
                and progress_count is not None
                and 0 < progress_index <= progress_count
            ):
                percent = ((progress_index - 1) + fraction) * 100.0 / progress_count
            else:
                percent = fraction * 100.0
            elapsed = max(time.monotonic() - started_at, 0.001)
            callback(
                EngineEvent(
                    event="downloading",
                    progress=TransferProgress(
                        downloaded_bytes=file_size if completed else 0,
                        total_bytes=file_size,
                        percent=min(100.0, percent),
                        speed_bytes_per_second=file_size / elapsed if completed else None,
                        eta_seconds=0.0 if completed else None,
                        fragment_index=progress_index,
                        fragment_count=progress_count,
                        filename=str(path),
                    ),
                )
            )

        emit_progress(False)
        try:
            chosen = self._verify_local_video_asset(
                probe_path,
                asset,
                should_cancel=should_cancel,
                require_quality_fingerprint=True,
            )
            if should_cancel():
                raise DownloadCancelledError("Task cancelled")
            path = _publish_paired_or_reuse_identical(probe_path, path)
        except BaseException:
            probe_path.unlink(missing_ok=True)
            raise
        emit_progress(True)
        return path.resolve(), chosen

    def _download_first_available_asset(
        self,
        ydl: YoutubeDL,
        assets: list[RemoteAsset],
        output_dir: Path,
        upload_date: str | None,
        title: str,
        media_id: str,
        source_url: str,
        *,
        platform: Platform,
        media_type: MediaType,
        callback: EventCallback | None,
        should_cancel: CancelCallback,
        asset_index: int | None = None,
        progress_index: int | None = None,
        progress_count: int | None = None,
        verify_declared_dimensions: bool = False,
        require_quality_fingerprint: bool = False,
        pair_image: bool = False,
        paired_stem: str | None = None,
        compact_names: bool = False,
        _douyin_transfer_attempt: int = 1,
        _douyin_transfer_budget: _DouyinNoProgressBudget | None = None,
        _transfer_attempt: int = 1,
    ) -> tuple[Path, RemoteAsset]:
        errors: list[str] = []
        douyin_transient_errors: list[str] = []
        douyin_redirect_errors: list[TemporaryAccessError] = []
        douyin_refresh_errors: list[DouyinMediaRefreshRequiredError] = []
        transfer_transient_errors: list[str] = []
        transient_positions: set[int] = set()
        is_xiaohongshu_source = platform == Platform.XIAOHONGSHU
        is_douyin_source = platform == Platform.DOUYIN
        is_kuaishou_source = platform == Platform.KUAISHOU
        is_bilibili_source = platform == Platform.BILIBILI
        transfer_budget = _douyin_transfer_budget
        for asset_position, asset in enumerate(assets):
            allow_verified_douyin_redirect = bool(
                is_douyin_source
                and media_type == MediaType.VIDEO
                and require_quality_fingerprint
                and (asset.size is not None or asset.bit_rate is not None)
                and asset.duration is not None
                and asset.duration > 0
                and asset.video_codec
                and asset.probe_prefix_size is not None
                and 12 <= asset.probe_prefix_size <= DOUYIN_PROBE_BYTES
                and re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(asset.probe_prefix_sha256 or ""),
                )
                and asset.redirect_source_url is not None
                and self._is_trusted_douyin_asset_url(
                    asset.redirect_source_url,
                    MediaType.VIDEO,
                )
            )
            if allow_verified_douyin_redirect:
                probe_path = self._take_douyin_probe_file(asset)
                if probe_path is not None:
                    try:
                        if paired_stem is not None:
                            return self._reuse_paired_douyin_probe_file(
                                probe_path,
                                asset,
                                output_dir,
                                paired_stem,
                                callback=callback,
                                should_cancel=should_cancel,
                                progress_index=progress_index,
                                progress_count=progress_count,
                            )
                        return self._reuse_verified_douyin_probe_file(
                            probe_path,
                            asset,
                            output_dir,
                            upload_date,
                            title,
                            media_id,
                            callback=callback,
                            should_cancel=should_cancel,
                            asset_index=asset_index,
                            progress_index=progress_index,
                            progress_count=progress_count,
                            paired_stem=paired_stem,
                            compact_names=compact_names,
                        )
                    except (DownloadCancelledError, _PairedAssetConflictError):
                        raise
                    except Exception as exc:
                        errors.append(
                            "Verified probe file: "
                            f"{safe_external_error_message(exc)}"
                        )
            candidates = (
                self._bounded_douyin_transfer_candidates(
                    [
                        *(
                            [asset.redirect_source_url]
                            if asset.redirect_source_url
                            else []
                        ),
                        *asset.candidates,
                    ]
                )
                if is_douyin_source
                else asset.candidates
            )
            for candidate_index, candidate in enumerate(candidates, start=1):
                if should_cancel():
                    raise DownloadCancelledError("Task cancelled")
                response = None
                try:
                    if is_douyin_source:
                        if transfer_budget is None:
                            transfer_budget = _DouyinNoProgressBudget(
                                DOUYIN_TRANSFER_IDLE_TIMEOUT_SECONDS,
                                "media transfer",
                            )
                        request_timeout = transfer_budget.request_timeout(
                            max(
                                0.1,
                                min(
                                    float(self.config.socket_timeout_seconds),
                                    DOUYIN_TRANSFER_HTTP_TIMEOUT_SECONDS,
                                ),
                            )
                        )
                    if is_xiaohongshu_source and not (
                        is_trusted_xiaohongshu_asset_url(candidate)
                    ):
                        raise MediaDownloadError(
                            "Untrusted Xiaohongshu media URL was blocked"
                        )
                    if is_kuaishou_source and not is_kuaishou_media_url(candidate):
                        raise TemporaryAccessError(
                            "Untrusted Kuaishou media URL was blocked",
                            issue_code=SiteIssueCode.SECURITY_BLOCKED,
                        )
                    if is_bilibili_source and not is_trusted_bilibili_asset_url(
                        candidate
                    ):
                        raise MediaDownloadError(
                            "Untrusted Bilibili media URL was blocked"
                        )
                    candidate_redirect_reason = (
                        self._douyin_media_redirect_rejection_reason(
                            candidate,
                            media_type,
                            allow_verified_regional=allow_verified_douyin_redirect,
                        )
                        if is_douyin_source
                        else None
                    )
                    if is_douyin_source and candidate_redirect_reason is not None:
                        redirect_error = self._douyin_redirect_rejection(
                            candidate,
                            candidate_redirect_reason,
                        )
                        raise DouyinMediaRefreshRequiredError(
                            "Untrusted Douyin media URL was blocked before reading "
                            "the response. The current item must be refreshed from "
                            "its original task link. Redirect host: "
                            f"{redirect_error.redirect_host or 'unavailable'}; "
                            "Redirect host fingerprint: "
                            f"{redirect_error.redirect_host_fingerprint or 'unavailable'}; "
                            "Redirect port: "
                            f"{redirect_error.redirect_port or 'unavailable'}; "
                            "Redirect reason: "
                            f"{redirect_error.redirect_reason}",
                            issue_code=SiteIssueCode.SECURITY_BLOCKED,
                        )
                    referer = source_url
                    if is_xiaohongshu_source:
                        referer = "https://www.xiaohongshu.com/"
                    elif is_douyin_source:
                        referer = DOUYIN_MEDIA_HEADERS["Referer"]
                    elif is_kuaishou_source:
                        referer = "https://www.kuaishou.com/"
                    request_headers = {
                        "Referer": referer,
                        "Accept": "*/*",
                        # 媒体按原始字节下载与校验：显式关闭传输层压缩，
                        # 避免 gzip 下 Content-Length（压缩长度）与实际写入
                        # 字节数错配，把完整响应误判为不完整。
                        "Accept-Encoding": "identity",
                    }
                    if is_douyin_source:
                        request_headers["User-Agent"] = DOUYIN_MEDIA_HEADERS[
                            "User-Agent"
                        ]
                    media_request = Request(
                        candidate,
                        headers=request_headers,
                        extensions=(
                            {"timeout": request_timeout} if is_douyin_source else {}
                        ),
                    )
                    if is_douyin_source:
                        try:
                            response = self._open_douyin_media_response(
                                ydl,
                                media_request,
                                redirect_rejection_reason=lambda value: (
                                    self._douyin_media_redirect_rejection_reason(
                                        value,
                                        media_type,
                                        allow_verified_regional=(
                                            allow_verified_douyin_redirect
                                        ),
                                    )
                                ),
                                should_cancel=should_cancel,
                            )
                        except DownloadCancelled as exc:
                            raise DownloadCancelledError("Task cancelled") from exc
                        except _DouyinRedirectRejected as exc:
                            raise DouyinMediaRefreshRequiredError(
                                "Douyin media redirect could not be trusted. The task "
                                "was paused before downloading later items. Redirect "
                                f"host: {exc.redirect_host or 'unavailable'}; Redirect "
                                "host fingerprint: "
                                f"{exc.redirect_host_fingerprint or 'unavailable'}; "
                                f"Redirect port: {exc.redirect_port or 'unavailable'}; "
                                f"reason: {exc.redirect_reason or 'unrecognized-host'}"
                            ) from exc
                    elif is_xiaohongshu_source or is_kuaishou_source or is_bilibili_source:
                        try:
                            response = _open_xiaohongshu_response(
                                ydl,
                                media_request,
                                is_trusted_url=(
                                    is_kuaishou_media_url
                                    if is_kuaishou_source
                                    else is_trusted_bilibili_asset_url
                                    if is_bilibili_source
                                    else is_trusted_xiaohongshu_asset_url
                                ),
                            )
                        except _XiaohongshuRedirectRejected as exc:
                            if is_kuaishou_source:
                                raise TemporaryAccessError(
                                    "Kuaishou media redirect was blocked before "
                                    "requesting an untrusted target",
                                    issue_code=SiteIssueCode.SECURITY_BLOCKED,
                                ) from exc
                            if is_bilibili_source:
                                raise MediaDownloadError(
                                    "Bilibili media redirect was blocked before "
                                    "requesting an untrusted target"
                                ) from exc
                            raise MediaDownloadError(
                                "Xiaohongshu media redirect was blocked before "
                                "requesting an untrusted target"
                            ) from exc
                    else:
                        response = ydl.urlopen(media_request)
                    final_url = str(getattr(response, "url", None) or candidate)
                    if is_kuaishou_source and not is_kuaishou_media_url(final_url):
                        raise TemporaryAccessError(
                            "Kuaishou media request redirected to an untrusted URL",
                            issue_code=SiteIssueCode.SECURITY_BLOCKED,
                        )
                    if is_xiaohongshu_source and not (
                        is_trusted_xiaohongshu_asset_url(final_url)
                    ):
                        raise MediaDownloadError(
                            "Xiaohongshu media request redirected to an untrusted URL"
                        )
                    if is_bilibili_source and not is_trusted_bilibili_asset_url(
                        final_url
                    ):
                        raise MediaDownloadError(
                            "Bilibili media request redirected to an untrusted URL"
                        )
                    redirect_reason = (
                        self._douyin_media_redirect_rejection_reason(
                            final_url,
                            media_type,
                            allow_verified_regional=(allow_verified_douyin_redirect),
                        )
                        if is_douyin_source
                        else None
                    )
                    if is_douyin_source and redirect_reason is not None:
                        redirect_error = self._douyin_redirect_rejection(
                            final_url,
                            redirect_reason,
                        )
                        raise DouyinMediaRefreshRequiredError(
                            "Douyin media redirect could not be trusted. The task was "
                            "paused before downloading later items. Redirect host: "
                            f"{redirect_error.redirect_host or 'unavailable'}; "
                            "Redirect host fingerprint: "
                            f"{redirect_error.redirect_host_fingerprint or 'unavailable'}; "
                            "Redirect port: "
                            f"{redirect_error.redirect_port or 'unavailable'}; "
                            "Redirect reason: "
                            f"{redirect_error.redirect_reason}"
                        )
                    content_type = (
                        (response.headers.get("Content-Type") or "")
                        .split(";", 1)[0]
                        .lower()
                    )
                    if content_type.startswith("text/") or content_type in {
                        "application/json",
                        "application/problem+json",
                    }:
                        if is_douyin_source:
                            raise DouyinMediaRefreshRequiredError(
                                "Douyin media endpoint returned text or metadata "
                                "instead of the verified media file. The site may "
                                "have expired or replaced this media route; refresh "
                                "the current item from its original task link.",
                                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
                            )
                        raise MediaDownloadError(
                            "Media server returned a text or metadata response "
                            "instead of a media file"
                        )
                    transfer_started_at = time.monotonic()
                    last_transfer_report_at = 0.0
                    if callback and is_douyin_source:
                        callback(
                            EngineEvent(
                                event="downloading",
                                progress=TransferProgress(
                                    filename="Starting Douyin original media transfer",
                                ),
                            )
                        )
                    stream_chunk_size = (
                        DOUYIN_STREAM_CHUNK_BYTES if is_douyin_source else 1024 * 1024
                    )
                    first_chunk = (
                        self._read_douyin_stream_chunk(response, stream_chunk_size)
                        if is_douyin_source
                        else response.read(stream_chunk_size)
                    )
                    if not first_chunk:
                        if transfer_budget is not None:
                            transfer_budget.remaining()
                        if is_douyin_source:
                            raise DouyinMediaRefreshRequiredError(
                                "Douyin media endpoint returned an empty response "
                                "instead of the verified media file. The site may "
                                "have expired or replaced this media route; refresh "
                                "the current item from its original task link.",
                                issue_code=SiteIssueCode.MEDIA_LINK_EXPIRED,
                            )
                        raise MediaDownloadError(
                            "Media server returned an empty response"
                        )
                    if transfer_budget is not None:
                        transfer_budget.refresh()
                    chosen = asset
                    if verify_declared_dimensions and media_type == MediaType.IMAGE:
                        actual_dimensions = self._image_dimensions(first_chunk)
                        if not actual_dimensions:
                            # 巨型 EXIF 或后置元数据盒会把真实尺寸推到首 1MB
                            # 之外；在上限内继续读流累积扫描，而不是直接否决
                            # 合法图片。多读的字节并入 first_chunk，写入路径不变。
                            image_scan_limit = 8 * 1024 * 1024
                            scanned = first_chunk
                            while not actual_dimensions and len(scanned) < image_scan_limit:
                                if should_cancel():
                                    raise DownloadCancelledError("Task cancelled")
                                more = response.read(1024 * 1024)
                                if not more:
                                    break
                                scanned += more
                                actual_dimensions = self._image_dimensions(scanned)
                            first_chunk = scanned
                        if not actual_dimensions:
                            raise MediaDownloadError(
                                "Highest-available image dimensions could not be verified"
                            )
                        actual_width, actual_height = actual_dimensions
                        if (
                            asset.width
                            and asset.height
                            and (
                                min(actual_width, actual_height)
                                < min(asset.width, asset.height)
                                or max(actual_width, actual_height)
                                < max(asset.width, asset.height)
                            )
                        ):
                            raise MediaDownloadError(
                                "Media server returned an image below its declared "
                                f"{asset.width}x{asset.height} resolution"
                            )
                        chosen = RemoteAsset(
                            candidates=list(asset.candidates),
                            index=asset.index,
                            width=actual_width,
                            height=actual_height,
                            size=asset.size,
                            format_id=asset.format_id,
                        )
                    extension = self._asset_extension(
                        candidate, content_type, media_type, first_chunk
                    )
                    path = self._xhs_output_path(
                        output_dir,
                        upload_date,
                        title,
                        media_id,
                        extension,
                        asset_index,
                        platform,
                        compact_names=compact_names,
                    )
                    if paired_stem is not None:
                        path = path.with_name(paired_stem + path.suffix)
                    total_header = response.headers.get("Content-Length")
                    # 部分代理会把重复的 Content-Length 合并成 "123, 123"；
                    # 取第一段并去空白，避免可解析的长度被误判为未知，
                    # 否则完整性与进度检查会被整体跳过。
                    total_value = (
                        str(total_header).split(",")[0].strip()
                        if total_header
                        else ""
                    )
                    total = int(total_value) if total_value.isdigit() else None
                    downloaded = len(first_chunk)

                    def progress_percent() -> float | None:
                        if not total:
                            return None
                        local_fraction = min(1.0, downloaded / total)
                        if (
                            progress_index is not None
                            and progress_count is not None
                            and 0 < progress_index <= progress_count
                        ):
                            return min(
                                100.0,
                                ((progress_index - 1) + local_fraction)
                                * 100.0
                                / progress_count,
                            )
                        return min(100.0, local_fraction * 100.0)

                    temporary_fd, temporary_name = tempfile.mkstemp(
                        prefix=".original-media-",
                        suffix=".part",
                        dir=path.parent,
                    )
                    temporary = Path(temporary_name)

                    def emit_transfer_progress(*, force: bool = False) -> None:
                        nonlocal last_transfer_report_at
                        if not callback:
                            return
                        now = time.monotonic()
                        if (
                            is_douyin_source
                            and not force
                            and now - last_transfer_report_at
                            < DOUYIN_PROGRESS_INTERVAL_SECONDS
                        ):
                            return
                        elapsed = max(now - transfer_started_at, 0.001)
                        speed = downloaded / elapsed if downloaded else None
                        eta = (
                            max(0.0, (total - downloaded) / speed)
                            if total is not None and speed is not None and speed > 0
                            else None
                        )
                        callback(
                            EngineEvent(
                                event="downloading",
                                progress=TransferProgress(
                                    downloaded_bytes=downloaded,
                                    total_bytes=total,
                                    percent=progress_percent(),
                                    speed_bytes_per_second=speed,
                                    eta_seconds=eta,
                                    fragment_index=progress_index,
                                    fragment_count=progress_count,
                                    filename=str(path),
                                ),
                            )
                        )
                        last_transfer_report_at = now

                    try:
                        with os.fdopen(temporary_fd, "wb") as handle:
                            temporary_fd = -1
                            handle.write(first_chunk)
                            emit_transfer_progress(force=True)
                            while True:
                                if should_cancel():
                                    raise DownloadCancelledError("Task cancelled")
                                chunk = (
                                    self._read_douyin_stream_chunk(
                                        response,
                                        stream_chunk_size,
                                    )
                                    if is_douyin_source
                                    else response.read(stream_chunk_size)
                                )
                                if not chunk:
                                    if transfer_budget is not None:
                                        transfer_budget.remaining()
                                    break
                                handle.write(chunk)
                                downloaded += len(chunk)
                                if transfer_budget is not None:
                                    transfer_budget.refresh()
                                emit_transfer_progress()
                            if total is not None and downloaded != total:
                                raise MediaDownloadError(
                                    f"Incomplete media response: expected {total} bytes, "
                                    f"received {downloaded}"
                                )
                            if asset.size is not None and downloaded != asset.size:
                                raise MediaDownloadError(
                                    "Media response changed after quality verification: "
                                    f"expected {asset.size} bytes, received {downloaded}"
                                )
                            handle.flush()
                            os.fsync(handle.fileno())
                        if verify_declared_dimensions and media_type == MediaType.VIDEO:
                            chosen = self._verify_local_video_asset(
                                temporary,
                                asset,
                                should_cancel=should_cancel,
                                require_quality_fingerprint=(
                                    require_quality_fingerprint
                                ),
                            )
                        if is_kuaishou_source and media_type == MediaType.IMAGE:
                            self._decode_local_image(temporary, should_cancel=should_cancel)
                            chosen.size = downloaded
                        if should_cancel():
                            raise DownloadCancelledError("Task cancelled")
                        if paired_stem is not None:
                            path = _publish_paired_or_reuse_identical(temporary, path)
                        elif is_kuaishou_source:
                            path = self._publish_kuaishou_asset(
                                temporary,
                                output_dir,
                                upload_date,
                                title,
                                media_id,
                                extension,
                                asset_index,
                                should_cancel=should_cancel,
                                compact_names=compact_names,
                            )
                        elif pair_image:
                            path = publish_pair_image(temporary, path)
                        else:
                            path = publish_or_reuse_identical(temporary, path)
                    except BaseException:
                        if temporary_fd >= 0:
                            with contextlib.suppress(OSError):
                                os.close(temporary_fd)
                        temporary.unlink(missing_ok=True)
                        raise
                    if total is None:
                        total = downloaded
                    emit_transfer_progress(force=True)
                    return path.resolve(), chosen
                except (DownloadCancelledError, _PairedAssetConflictError):
                    raise
                except TemporaryAccessError as exc:
                    if (
                        is_douyin_source
                        and "Douyin media redirect could not be trusted" in str(exc)
                    ):
                        douyin_redirect_errors.append(exc)
                        errors.append(
                            f"Candidate {candidate_index}: "
                            f"{safe_external_error_message(exc)}"
                        )
                        continue
                    if is_douyin_source and isinstance(
                        exc,
                        DouyinMediaRefreshRequiredError,
                    ):
                        douyin_refresh_errors.append(exc)
                        errors.append(
                            f"Candidate {candidate_index}: "
                            f"{safe_external_error_message(exc)}"
                        )
                        continue
                    raise
                except Exception as exc:
                    self._close_douyin_probe_error(exc)
                    if is_douyin_source and transfer_budget is not None:
                        transfer_budget.remaining()
                    if is_douyin_source and self._is_retryable_douyin_transfer_error(
                        exc
                    ):
                        douyin_transient_errors.append(
                            self._safe_douyin_probe_failure(exc)
                        )
                    if (
                        not is_douyin_source
                        and self._is_retryable_network_transfer_error(exc)
                    ):
                        transient_positions.add(asset_position)
                        transfer_transient_errors.append(
                            f"Candidate {candidate_index}: "
                            f"{safe_external_error_message(exc)}"
                        )
                    errors.append(
                        f"Candidate {candidate_index}: "
                        f"{safe_external_error_message(exc)}"
                    )
                finally:
                    if response is not None:
                        with contextlib.suppress(Exception):
                            response.close()
        if douyin_transient_errors:
            detail = douyin_transient_errors[-1]
            if _douyin_transfer_attempt < DOUYIN_TRANSFER_ATTEMPTS:
                if callback:
                    callback(
                        EngineEvent(
                            event="probing",
                            message=(
                                "Retrying Douyin media transfer after a temporary "
                                "network error "
                                f"({_douyin_transfer_attempt + 1}/"
                                f"{DOUYIN_TRANSFER_ATTEMPTS})"
                            ),
                        )
                    )
                try:
                    retry_delay = DOUYIN_PROBE_RETRY_BASE_SECONDS * (
                        2 ** (_douyin_transfer_attempt - 1)
                    )
                    if transfer_budget is not None:
                        retry_delay = min(
                            retry_delay,
                            transfer_budget.remaining(),
                        )
                    self._wait_for_douyin_probe_retry(retry_delay, should_cancel)
                    if transfer_budget is not None:
                        transfer_budget.remaining()
                except DownloadCancelled as exc:
                    raise DownloadCancelledError("Task cancelled") from exc
                return self._download_first_available_asset(
                    ydl,
                    assets,
                    output_dir,
                    upload_date,
                    title,
                    media_id,
                    source_url,
                    platform=platform,
                    media_type=media_type,
                    callback=callback,
                    should_cancel=should_cancel,
                    asset_index=asset_index,
                    progress_index=progress_index,
                    progress_count=progress_count,
                    verify_declared_dimensions=verify_declared_dimensions,
                    require_quality_fingerprint=require_quality_fingerprint,
                    pair_image=pair_image,
                    paired_stem=paired_stem,
                    compact_names=compact_names,
                    _douyin_transfer_attempt=_douyin_transfer_attempt + 1,
                    _douyin_transfer_budget=transfer_budget,
                )
            if douyin_redirect_errors:
                raise douyin_redirect_errors[-1]
            if douyin_refresh_errors:
                raise douyin_refresh_errors[-1]
            raise TemporaryAccessError(
                "Douyin media transfer was temporarily unavailable. The task was "
                "paused before downloading later items; wait briefly and continue "
                "the task. Completed files were preserved. Transfer details: "
                f"{detail}",
                issue_code=classify_site_issue(detail),
            )
        if (
            not is_douyin_source
            and transfer_transient_errors
            and transient_positions
            and _transfer_attempt < TRANSFER_RETRY_ATTEMPTS
        ):
            # 非抖音路径的一次网络抖动此前会永久判死整个资产；
            # 参照抖音既有模式做最小有界重试（仅网络类瞬态，
            # 指数退避，取消信号随时直通）。
            if callback:
                callback(
                    EngineEvent(
                        event="probing",
                        message=(
                            "Retrying media transfer after a temporary "
                            "network error "
                            f"({_transfer_attempt + 1}/"
                            f"{TRANSFER_RETRY_ATTEMPTS})"
                        ),
                    )
                )
            remaining_wait = TRANSFER_RETRY_BASE_SECONDS * (
                2 ** (_transfer_attempt - 1)
            )
            while remaining_wait > 0:
                if should_cancel():
                    raise DownloadCancelledError("Task cancelled")
                slice_sleep = min(0.1, remaining_wait)
                time.sleep(slice_sleep)
                remaining_wait -= slice_sleep
            return self._download_first_available_asset(
                ydl,
                # 从首个瞬态失败处续跑：其后资产的确定性失败会随重跑
                # 再次失败（有界浪费），但不会丢失任何后续资产的机会。
                assets,
                output_dir,
                upload_date,
                title,
                media_id,
                source_url,
                platform=platform,
                media_type=media_type,
                callback=callback,
                should_cancel=should_cancel,
                asset_index=asset_index,
                progress_index=progress_index,
                progress_count=progress_count,
                verify_declared_dimensions=verify_declared_dimensions,
                require_quality_fingerprint=require_quality_fingerprint,
                pair_image=pair_image,
                paired_stem=paired_stem,
                compact_names=compact_names,
                _transfer_attempt=_transfer_attempt + 1,
            )
        if douyin_redirect_errors:
            raise douyin_redirect_errors[-1]
        if douyin_refresh_errors:
            raise douyin_refresh_errors[-1]
        detail = errors[-1] if errors else "No asset URLs were available"
        raise MediaDownloadError(f"All highest-available media URLs failed: {detail}")
    def _is_retryable_network_transfer_error(self, exc: BaseException) -> bool:
        """非抖音传输的网络类瞬态错误：一次抖动不应永久判死一个资产。
        TemporaryAccessError 不在此列——它在候选循环被直接上抛，
        走队列暂停语义（那本身就是它的重试机制）。"""
        if isinstance(exc, (TransportError, IncompleteRead)):
            return True
        if isinstance(exc, MediaDownloadError):
            message = str(exc).lower()
            return (
                "incomplete media response" in message
                or "empty response" in message
            )
        return False

    def _verify_local_video_asset(
        self,
        path: Path,
        asset: RemoteAsset,
        *,
        should_cancel: CancelCallback,
        require_quality_fingerprint: bool = False,
    ) -> RemoteAsset:
        if (
            require_quality_fingerprint
            and asset.size is None
            and asset.bit_rate is None
        ):
            raise MediaDownloadError(
                "The verified highest-quality video has no bitrate or complete "
                "size fingerprint"
            )
        if require_quality_fingerprint and (
            asset.duration is None or asset.duration <= 0
        ):
            raise MediaDownloadError(
                "The verified highest-quality video has no duration fingerprint"
            )
        if require_quality_fingerprint and (
            asset.probe_prefix_size is None
            or not (12 <= asset.probe_prefix_size <= DOUYIN_PROBE_BYTES)
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(asset.probe_prefix_sha256 or ""),
            )
        ):
            raise MediaDownloadError(
                "The verified highest-quality video has no content fingerprint"
            )
        if require_quality_fingerprint and not asset.video_codec:
            raise MediaDownloadError(
                "The verified highest-quality video has no codec fingerprint"
            )
        if asset.probe_prefix_size and asset.probe_prefix_sha256:
            try:
                with path.open("rb") as handle:
                    prefix = handle.read(asset.probe_prefix_size)
            except OSError as exc:
                raise MediaDownloadError(
                    "Downloaded video content fingerprint could not be read"
                ) from exc
            if (
                len(prefix) != asset.probe_prefix_size
                or hashlib.sha256(prefix).hexdigest() != asset.probe_prefix_sha256
            ):
                raise MediaDownloadError(
                    "Downloaded video content did not match the verified Douyin "
                    "media endpoint"
                )
        executable = self._find_ffprobe_executable()
        if not executable:
            raise MediaDownloadError(FFPROBE_REQUIRED_MESSAGE)
        try:
            payload = self._run_ffprobe(
                [
                    executable,
                    "-v",
                    "error",
                    "-show_entries",
                    (
                        "stream=codec_type,codec_name,width,height,bit_rate,duration:"
                        "format=duration,bit_rate,size"
                    ),
                    "-of",
                    "json",
                    "-i",
                    str(path),
                ],
                timeout_seconds=DOUYIN_FFPROBE_FILE_TIMEOUT_SECONDS,
                should_cancel=should_cancel,
            )
        except DownloadCancelled as exc:
            raise DownloadCancelledError("Task cancelled") from exc
        media = self._parse_ffprobe_payload(payload)
        actual_width = int((media or {}).get("width") or 0)
        actual_height = int((media or {}).get("height") or 0)
        if actual_width <= 0 or actual_height <= 0:
            raise MediaDownloadError(
                "Highest-available video dimensions could not be verified"
            )
        if (
            asset.width
            and asset.height
            and (
                min(actual_width, actual_height) < min(asset.width, asset.height)
                or max(actual_width, actual_height) < max(asset.width, asset.height)
            )
        ):
            raise MediaDownloadError(
                "Media server returned a video below its declared "
                f"{asset.width}x{asset.height} resolution"
            )
        actual_video_codec = str((media or {}).get("vcodec") or "").lower()
        actual_audio_codec = str((media or {}).get("acodec") or "").lower()
        if asset.video_codec and actual_video_codec != asset.video_codec.lower():
            raise MediaDownloadError(
                "Downloaded video codec did not match its verified media endpoint"
            )
        if asset.audio_codec and actual_audio_codec != asset.audio_codec.lower():
            raise MediaDownloadError(
                "Downloaded audio codec did not match its verified media endpoint"
            )
        if asset.size is not None:
            actual_size = path.stat().st_size
            tolerance = max(64 * 1024, int(asset.size * 0.01))
            if abs(actual_size - asset.size) > tolerance:
                raise MediaDownloadError(
                    "Downloaded video size did not match its verified highest-quality "
                    "media endpoint"
                )
        if asset.bit_rate is not None:
            actual_bit_rate = int((media or {}).get("bit_rate") or 0)
            if actual_bit_rate <= 0 or actual_bit_rate < int(asset.bit_rate * 0.9):
                raise MediaDownloadError(
                    "Downloaded video bitrate was below its verified highest-quality "
                    "media endpoint"
                )
        if asset.duration is not None:
            actual_duration = self._float_or_none((media or {}).get("duration"))
            tolerance = self._douyin_duration_tolerance(asset.duration)
            if (
                actual_duration is None
                or abs(actual_duration - asset.duration) > tolerance
            ):
                raise MediaDownloadError(
                    "Downloaded video duration did not match its verified media "
                    "metadata"
                )
        return RemoteAsset(
            candidates=list(asset.candidates),
            index=asset.index,
            width=actual_width,
            height=actual_height,
            size=asset.size,
            format_id=asset.format_id,
            video_uri=asset.video_uri,
            duration=asset.duration,
            bit_rate=asset.bit_rate,
            quality_candidates=asset.quality_candidates,
            video_codec=asset.video_codec,
            audio_codec=asset.audio_codec,
            probe_prefix_size=asset.probe_prefix_size,
            probe_prefix_sha256=asset.probe_prefix_sha256,
            redirect_source_url=asset.redirect_source_url,
        )
    @staticmethod
    def _image_dimensions(data: bytes) -> tuple[int, int] | None:
        if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
            width = int.from_bytes(data[16:20], "big")
            height = int.from_bytes(data[20:24], "big")
            return (width, height) if width > 0 and height > 0 else None
        if len(data) >= 10 and data.startswith((b"GIF87a", b"GIF89a")):
            width = int.from_bytes(data[6:8], "little")
            height = int.from_bytes(data[8:10], "little")
            return (width, height) if width > 0 and height > 0 else None
        if len(data) >= 30 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8X":
                width = 1 + int.from_bytes(data[24:27], "little")
                height = 1 + int.from_bytes(data[27:30], "little")
                return width, height
            if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
                width = int.from_bytes(data[26:28], "little") & 0x3FFF
                height = int.from_bytes(data[28:30], "little") & 0x3FFF
                return (width, height) if width > 0 and height > 0 else None
            if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
                packed = int.from_bytes(data[21:25], "little")
                width = (packed & 0x3FFF) + 1
                height = ((packed >> 14) & 0x3FFF) + 1
                return width, height
        iso_dimensions = TransferMixin._iso_bmff_image_dimensions(data)
        if iso_dimensions:
            return iso_dimensions
        if len(data) < 4 or not data.startswith(b"\xff\xd8"):
            return None
        offset = 2
        start_of_frame_markers = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        while offset + 4 <= len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                return None
            marker = data[offset]
            offset += 1
            if marker in {0x01, *range(0xD0, 0xDA)}:
                continue
            if offset + 2 > len(data):
                return None
            segment_length = int.from_bytes(data[offset : offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(data):
                return None
            if marker in start_of_frame_markers and segment_length >= 7:
                height = int.from_bytes(data[offset + 3 : offset + 5], "big")
                width = int.from_bytes(data[offset + 5 : offset + 7], "big")
                return (width, height) if width > 0 and height > 0 else None
            offset += segment_length
        return None
    @staticmethod
    def _iso_bmff_image_dimensions(data: bytes) -> tuple[int, int] | None:
        image_brands = {
            b"avif",
            b"avis",
            b"heic",
            b"heix",
            b"hevc",
            b"hevx",
            b"mif1",
            b"msf1",
        }

        def boxes(start: int, end: int):
            offset = start
            while offset + 8 <= end:
                size = int.from_bytes(data[offset : offset + 4], "big")
                box_type = data[offset + 4 : offset + 8]
                header_size = 8
                if size == 1:
                    if offset + 16 > end:
                        return
                    size = int.from_bytes(data[offset + 8 : offset + 16], "big")
                    header_size = 16
                elif size == 0:
                    size = end - offset
                if size < header_size:
                    return
                box_end = offset + size
                if box_end > end:
                    return
                yield box_type, offset + header_size, box_end
                offset = box_end

        top_level = list(boxes(0, len(data)))
        ftyp = next(
            (
                (payload_start, box_end)
                for box_type, payload_start, box_end in top_level
                if box_type == b"ftyp"
            ),
            None,
        )
        if not ftyp:
            return None
        ftyp_start, ftyp_end = ftyp
        if ftyp_end - ftyp_start < 8:
            return None
        brands = {data[ftyp_start : ftyp_start + 4]}
        brands.update(
            data[offset : offset + 4]
            for offset in range(ftyp_start + 8, ftyp_end, 4)
            if offset + 4 <= ftyp_end
        )
        if not brands.intersection(image_brands):
            return None

        dimensions: list[tuple[int, int]] = []
        container_offsets = {b"meta": 4, b"iprp": 0, b"ipco": 0}

        def collect(start: int, end: int, depth: int) -> None:
            if depth > 8:
                return
            for box_type, payload_start, box_end in boxes(start, end):
                if box_type == b"ispe":
                    if box_end - payload_start < 12:
                        continue
                    width = int.from_bytes(
                        data[payload_start + 4 : payload_start + 8], "big"
                    )
                    height = int.from_bytes(
                        data[payload_start + 8 : payload_start + 12], "big"
                    )
                    if width > 0 and height > 0:
                        dimensions.append((width, height))
                    continue
                child_offset = container_offsets.get(box_type)
                if child_offset is None or payload_start + child_offset > box_end:
                    continue
                collect(payload_start + child_offset, box_end, depth + 1)

        collect(0, len(data), 0)
        return (
            max(dimensions, key=lambda value: value[0] * value[1])
            if dimensions
            else None
        )
