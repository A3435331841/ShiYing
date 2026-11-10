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
from ..xiaohongshu import RemoteAsset, discover_profile as discover_xhs_profile
from ..xiaohongshu import (
    _XiaohongshuRedirectRejected,
    _open_xiaohongshu_response,
)
from ..xiaohongshu import is_trusted_xiaohongshu_asset_url
from ..xiaohongshu import parse_note as parse_xhs_note

from .common import *  # noqa: F401,F403


class PlatformItemsMixin:
    def _download_wechat_item(
        self,
        item: DownloadItem,
        output_dir: Path,
        *,
        callback: EventCallback | None,
        should_cancel: CancelCallback,
    ) -> DownloadOutcome:
        from ..wechat import WeChatUserError, download_stream_and_decrypt, load_yuanbao_cookie, media_selection, profile_yuanbao, verify_video, work_identity
        
        with threading.RLock():
            try:
                cookie = load_yuanbao_cookie()
            except WeChatUserError as exc:
                raise MediaDownloadError(safe_external_error_message(exc)) from exc
        if not cookie:
            raise MediaDownloadError("视频号下载需要配置腾讯元宝登录态")
        feed, _ = profile_yuanbao(item.source_url, cookie)
        current_id = work_identity(feed, item.source_url)
        if not current_id or current_id != item.media_id:
            raise MediaDownloadError("视频号作品身份发生变化，请重新创建任务。")
        media_url, decode_key = media_selection(feed)

        title = item.title or "视频号视频"
        author = item.author or "未知作者"
        folder = output_dir
        folder.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r'[<>:"/\|?*\x00-\x1f]', '_', title)[:70].rstrip('. ') or '视频号视频'
        date = item.upload_date or datetime.now(timezone.utc).strftime('%Y-%m-%d')
        identity = safe_component(str(item.media_id or item.id), limit=32)
        path = folder / f'{date}-{stem} [{identity}].mp4'
        if path.exists():
            raise MediaDownloadError('目标文件已存在，请检查已有文件，避免覆盖。')

        # The staging name must be unique per job, not just per work identity:
        # two tasks for the same work otherwise share one temporary file, and one
        # task's write, publish or cleanup can corrupt the other.
        job_scope = safe_component(
            str(item.metadata.get("_job_id") or "standalone"),
            fallback="standalone",
            limit=32,
        )
        staging = folder / f".{job_scope}-{safe_component(item.id, fallback='item', limit=64)}.checking.mp4"
        cancel_event = threading.Event()

        def on_progress(downloaded, total):
            if should_cancel():
                cancel_event.set()
                raise DownloadCancelledError("Task cancelled")
            pct = round(downloaded / total * 100, 1) if total else None
            if callback:
                callback(EngineEvent(event="downloading", progress=TransferProgress(
                    downloaded_bytes=downloaded, total_bytes=total or None, percent=pct,
                )))

        try:
            download_stream_and_decrypt(media_url, decode_key, staging, cancel_event, on_progress)
            if not verify_video(staging):
                if staging.exists():
                    staging.rename(staging.with_suffix('.unverified'))
                raise MediaDownloadError('文件未通过视频流校验，未标记为成功。')
            if should_cancel():
                raise DownloadCancelledError("Task cancelled")
            publish_no_replace(staging, path)
            return DownloadOutcome(output_paths=[str(path)], title=title, author=author, media_type=MediaType.VIDEO)
        finally:
            staging.unlink(missing_ok=True)
    def _download_bilibili_opus(
        self,
        item: DownloadItem,
        output_dir: Path,
        *,
        callback: EventCallback | None,
        should_cancel: CancelCallback,
    ) -> DownloadOutcome:
        from ..bilibili_opus import parse_bilibili_opus
        opus = parse_bilibili_opus(item.source_url)
        if not item.media_id or opus.opus_id != item.media_id:
            raise MediaDownloadError("B站图文作品身份发生变化，请重新创建任务。")
        if not opus.images and not opus.text_content.strip():
            raise MediaDownloadError("未能提取B站图文的正文或图片。")
        output_paths: list[str] = []
        total = len(opus.images)
        folder = output_dir
        folder.mkdir(parents=True, exist_ok=True)
        compact_names = item.metadata.get("_compact_asset_names") is True

        if opus.text_content.strip():
            if should_cancel():
                raise DownloadCancelledError("Task cancelled")
            descriptor, name = tempfile.mkstemp(prefix=".opus-", suffix=".tmp", dir=folder)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(opus.text_content)
                destination = folder / ("正文.txt" if compact_names else f"{safe_component(opus.title, limit=96)} [{opus.opus_id}].txt")
                output_paths.append(str(publish_or_reuse_identical(temporary, destination)))
            finally:
                temporary.unlink(missing_ok=True)

        with YoutubeDL(self._base_options(False)) as ydl:
            for idx, asset in enumerate(opus.images, 1):
                if should_cancel():
                    raise DownloadCancelledError("Task cancelled")
                path, chosen = self._download_first_available_asset(
                    ydl,
                    [asset],
                    output_dir,
                    None,
                    opus.title,
                    opus.opus_id,
                    item.source_url,
                    platform=Platform.BILIBILI,
                    media_type=MediaType.IMAGE,
                    callback=callback,
                    should_cancel=should_cancel,
                    asset_index=idx,
                    progress_index=idx,
                    progress_count=total,
                    compact_names=compact_names,
                )
                output_paths.append(str(path))
                if callback:
                    callback(EngineEvent(event="asset_completed", output_paths=list(output_paths)))

        return DownloadOutcome(
            output_paths=output_paths,
            title=opus.title,
            author=opus.author,
            media_type=MediaType.IMAGE,
        )
    def _download_kuaishou_item(
        self, item: DownloadItem, output_dir: Path, *,
        callback: EventCallback | None, should_cancel: CancelCallback,
    ) -> DownloadOutcome:
        if should_cancel():
            raise DownloadCancelledError("Task cancelled")
        source_kind, source_id = kuaishou_source_identity(item.source_url)
        if source_kind != "item" or source_id != item.media_id:
            raise MediaDownloadError("Kuaishou item identity changed; download was blocked")
        refresh_url = item.metadata.get("kuaishou_refresh_url") or item.source_url
        refresh_kind, refresh_id = kuaishou_source_identity(refresh_url)
        if refresh_kind not in {"item", "short_link"} or (
            refresh_kind == "item" and refresh_id != item.media_id
        ):
            raise MediaDownloadError("Kuaishou refresh URL identity changed; download was blocked")
        if callback:
            callback(EngineEvent(event="probing", message="Refreshing Kuaishou video links before download"))
        result = discover_kuaishou(
            refresh_url, cookie_profile=self.config.cookie_profile,
            use_browser_cookies=self.config.cookie_browser == "chrome",
            should_cancel=should_cancel,
            status_callback=(lambda message: callback(EngineEvent(event="probing", message=message))) if callback else None,
        )
        if len(result.videos) != 1 or result.videos[0].media_id != item.media_id:
            raise MediaDownloadError("Kuaishou returned a different video; download was blocked")
        video = result.videos[0]
        if not item.metadata.get("kuaishou_author_id") or video.author_id != item.metadata["kuaishou_author_id"]:
            raise MediaDownloadError("Kuaishou author identity changed; download was blocked")
        if not video.assets:
            raise MediaDownloadError("Kuaishou item has no trusted highest-quality media assets")
        media_type = MediaType.IMAGE if video.media_type == "image" else MediaType.VIDEO
        if callback:
            callback(EngineEvent(event="metadata", title=video.title, author=video.author,
                                 upload_date=video.upload_date, media_type=media_type))
        if media_type == MediaType.VIDEO:
            return self._download_kuaishou_video(
                item, video, output_dir,
                callback=callback, should_cancel=should_cancel,
            )
        return self._download_kuaishou_album(
            item, video, output_dir,
            callback=callback, should_cancel=should_cancel,
        )
    def _download_kuaishou_video(
        self, item: DownloadItem, video: Any, output_dir: Path, *,
        callback: EventCallback | None, should_cancel: CancelCallback,
    ) -> DownloadOutcome:
        """Download ONE video from its already quality-filtered candidate set.

        Video assets are alternate renditions of the SAME work, so they go to the
        shared transfer helper as one ordered set: the first candidate that
        passes verification wins and the attempt stops. Treating them as album
        members emitted two finished files for one video, and abandoned the
        remaining renditions after a single transfer failure.

        A previously completed file is reused only after FFprobe re-verifies it
        against the same declared quality, so a crash or a cancelled retry does
        not re-download work that is already on disk.
        """
        saved = self._kuaishou_saved_asset_records(item, video.media_id, "video")
        reused = None
        for asset in video.assets:
            reused = self._existing_kuaishou_video_asset(
                saved.get(1), output_dir, asset, should_cancel=should_cancel,
            )
            if reused is not None:
                break
        if reused is None:
            with YoutubeDL(self._base_options(False)) as ydl:
                path, chosen = self._download_first_available_asset(
                    ydl, list(video.assets), output_dir, video.upload_date, video.title,
                    video.media_id, video.url, platform=Platform.KUAISHOU,
                    media_type=MediaType.VIDEO, callback=callback,
                    should_cancel=should_cancel,
                    verify_declared_dimensions=True,
                )
        else:
            path, chosen = reused
        output_paths = [str(path)]
        resolution = f"{chosen.width}x{chosen.height}" if chosen.width and chosen.height else None
        record = self._kuaishou_completion_record(
            path, chosen, video.media_id, "video", 1,
            should_cancel=should_cancel, verified_record=saved.get(1) if reused else None,
        )
        if callback:
            callback(EngineEvent(
                event="asset_completed",
                output_paths=output_paths,
                asset_records=[record],
            ))
        return DownloadOutcome(output_paths=output_paths, title=video.title,
                               author=video.author, upload_date=video.upload_date,
                               media_type=MediaType.VIDEO, selected_format=chosen.format_id,
                               resolution=resolution)
    def _download_kuaishou_album(
        self, item: DownloadItem, video: Any, output_dir: Path, *,
        callback: EventCallback | None, should_cancel: CancelCallback,
    ) -> DownloadOutcome:
        """Download every album image separately, committing each one as it lands.

        Each verified image is reported through ``asset_completed`` together with
        a structured record immediately, so a later failure, a cancellation or a
        process restart cannot lose the knowledge that an earlier image already
        completed. Records carry no media URL and are persisted with the task.
        """
        total = len(video.assets)
        saved = self._kuaishou_saved_asset_records(item, video.media_id, "image")
        output_paths: list[str] = []
        records: list[dict[str, Any]] = []
        chosen_assets: list[RemoteAsset] = []
        with YoutubeDL(self._base_options(False)) as ydl:
            for asset_index, asset in enumerate(video.assets, 1):
                if should_cancel():
                    raise DownloadCancelledError("Task cancelled")
                asset.index = asset_index
                reused = self._existing_kuaishou_image_asset(
                    saved.get(asset_index),
                    output_dir,
                    asset,
                    should_cancel=should_cancel,
                )
                if reused is None:
                    path, chosen = self._download_first_available_asset(
                        ydl, [asset], output_dir, video.upload_date, video.title,
                        video.media_id, video.url, platform=Platform.KUAISHOU,
                        media_type=MediaType.IMAGE, callback=callback,
                        should_cancel=should_cancel, asset_index=asset_index,
                        progress_index=asset_index, progress_count=total,
                        verify_declared_dimensions=True,
                        compact_names=item.metadata.get("_compact_asset_names") is True,
                    )
                else:
                    path, chosen = reused
                record = self._kuaishou_completion_record(
                    path, chosen, video.media_id, "image", asset_index,
                    should_cancel=should_cancel,
                    verified_record=saved.get(asset_index) if reused else None,
                )
                output_paths.append(str(path))
                records.append(record)
                chosen_assets.append(chosen)
                # Commit this image before the next one starts, so a later
                # failure keeps an accurate record instead of re-downloading or
                # renaming an already saved file.
                if callback:
                    callback(EngineEvent(
                        event="asset_completed",
                        output_paths=list(output_paths),
                        asset_records=list(records),
                    ))
        chosen = chosen_assets[0]
        resolution = f"{chosen.width}x{chosen.height}" if chosen.width and chosen.height else None
        return DownloadOutcome(output_paths=output_paths, title=video.title,
                               author=video.author, upload_date=video.upload_date,
                               media_type=MediaType.IMAGE, selected_format=chosen.format_id,
                               resolution=resolution)
    @staticmethod
    def _kuaishou_source_fingerprint(asset: RemoteAsset, media_kind: str) -> str:
        """Bind a receipt to exact trusted sources without persisting signed URLs.

        Signature changes intentionally invalidate reuse. URL equality does not
        prove that a remote object has never changed; the local digest only
        proves that the completed local bytes have not changed since download.
        """
        if not asset.candidates or any(
            not is_kuaishou_media_url(url) for url in asset.candidates
        ):
            raise MediaDownloadError("Kuaishou asset has no trusted source identity")
        payload = {
            "version": 1,
            "media_kind": media_kind,
            "candidates": sorted(set(asset.candidates)),
            "video_codec": asset.video_codec,
            "audio_codec": asset.audio_codec,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    @staticmethod
    def _kuaishou_file_signature(facts: os.stat_result) -> tuple[int, ...]:
        return (facts.st_dev, facts.st_ino, facts.st_mode, facts.st_size,
                facts.st_mtime_ns, facts.st_ctime_ns)
    @staticmethod
    def _kuaishou_path_signature(facts: os.stat_result) -> tuple[int, ...]:
        # Windows Python 3.14 can report change time through fstat and creation
        # time through lstat for the same file. Keep ctime in fd-to-fd checks
        # everywhere and also in fd-to-path checks on other platforms.
        signature = (facts.st_dev, facts.st_ino, facts.st_mode, facts.st_size,
                     facts.st_mtime_ns)
        return signature if os.name == "nt" else signature + (facts.st_ctime_ns,)
    @classmethod
    def _kuaishou_local_fingerprint(
        cls, path: Path, *, should_cancel: CancelCallback,
    ) -> tuple[str, os.stat_result]:
        """Hash bounded chunks of one unchanged regular file, with cancellation."""
        if should_cancel():
            raise DownloadCancelledError("Task cancelled")
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise MediaDownloadError("Saved media is not a regular file")
            digest = hashlib.sha256()
            while True:
                if should_cancel():
                    raise DownloadCancelledError("Task cancelled")
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            signature = cls._kuaishou_file_signature(before)
            path_facts = path.lstat()
            if (signature != cls._kuaishou_file_signature(os.fstat(handle.fileno()))
                    or not stat.S_ISREG(path_facts.st_mode)
                    or cls._kuaishou_path_signature(before)
                    != cls._kuaishou_path_signature(path_facts)):
                raise MediaDownloadError("Saved media changed during verification")
        return digest.hexdigest(), before
    @classmethod
    def _kuaishou_completion_record(
        cls, path: Path, asset: RemoteAsset, media_id: str, media_kind: str, index: int,
        *, should_cancel: CancelCallback, verified_record: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if should_cancel():
            raise DownloadCancelledError("Task cancelled")
        if verified_record is None:
            digest, facts = cls._kuaishou_local_fingerprint(path, should_cancel=should_cancel)
        else:
            # The reuse gate already hashed and decoded this file in this attempt.
            digest, facts = verified_record["local_sha256"], path.lstat()
        return {
            "media_id": media_id, "media_kind": media_kind, "index": index,
            "path": str(path), "width": asset.width, "height": asset.height,
            "size": facts.st_size, "format_id": asset.format_id,
            "local_sha256": digest,
            "source_sha256": cls._kuaishou_source_fingerprint(asset, media_kind),
        }
    @classmethod
    def _kuaishou_receipt_matches(
        cls, record: dict[str, Any], asset: RemoteAsset, media_kind: str,
    ) -> bool:
        if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("local_sha256") or "")):
            return False
        return record.get("source_sha256") == cls._kuaishou_source_fingerprint(asset, media_kind)
    @staticmethod
    def _kuaishou_saved_asset_records(
        item: DownloadItem, media_id: str, media_kind: str
    ) -> dict[int, dict[str, Any]]:
        """Index persisted completion records by position for this exact work.

        Records are matched by work identity AND media kind, so a video record can
        never be reused as an album image or the other way round.
        """
        raw = item.metadata.get(KUAISHOU_SAVED_ASSETS_KEY)
        records: dict[int, dict[str, Any]] = {}
        if not isinstance(raw, list):
            return records
        for entry in raw:
            if not isinstance(entry, dict) or entry.get("media_id") != media_id:
                continue
            if entry.get("media_kind") != media_kind:
                continue
            index = entry.get("index")
            if isinstance(index, int) and not isinstance(index, bool) and index > 0:
                records[index] = entry
        return records
    def _existing_kuaishou_video_asset(
        self,
        record: dict[str, Any] | None,
        output_dir: Path,
        asset: RemoteAsset,
        *,
        should_cancel: CancelCallback,
    ) -> tuple[Path, RemoteAsset] | None:
        """Reuse a completed video only after FFprobe re-verifies it.

        Matching uses the persisted record's work identity, never a file name. The
        source and content digests must match the saved receipt. The file must
        still exist inside the output directory, be a real file rather
        than a symlink, and pass the same declared quality checks as a fresh
        download. A truncated, user-modified, moved or missing file is
        re-downloaded instead of being trusted or overwritten.
        """
        if not record:
            return None
        if should_cancel():
            raise DownloadCancelledError("Task cancelled")
        value = record.get("path")
        if not isinstance(value, str) or not value:
            return None
        candidate = Path(value)
        try:
            if not self._kuaishou_receipt_matches(record, asset, "video"):
                return None
            if candidate.is_symlink() or not candidate.is_file():
                return None
            resolved = candidate.resolve(strict=True)
            if resolved.parent != output_dir:
                return None
            digest, facts = self._kuaishou_local_fingerprint(resolved, should_cancel=should_cancel)
            if digest != record["local_sha256"] or facts.st_size != record.get("size"):
                return None
            verified = self._verify_local_video_asset(
                resolved,
                asset,
                should_cancel=should_cancel,
                require_quality_fingerprint=False,
            )
            if self._kuaishou_path_signature(facts) != self._kuaishou_path_signature(resolved.lstat()):
                return None
        except DownloadCancelledError:
            raise
        except (OSError, MediaDownloadError, ValueError):
            return None
        return resolved, verified
    def _existing_kuaishou_image_asset(
        self,
        record: dict[str, Any] | None,
        output_dir: Path,
        asset: RemoteAsset,
        *,
        should_cancel: CancelCallback,
    ) -> tuple[Path, RemoteAsset] | None:
        """Reuse a completed album image only after re-verifying it.

        Matching uses the work, position, exact source and local content digests,
        never a file name, because Kuaishou album files use date-and-title names
        and collision avoidance may append a suffix. The file must still exist
        inside the output directory, be a real file rather than a symlink, fully
        decode, and still meet the declared dimensions. A user-modified, moved or
        missing file is re-downloaded instead of being trusted or overwritten.
        """
        if not record:
            return None
        if should_cancel():
            raise DownloadCancelledError("Task cancelled")
        value = record.get("path")
        if not isinstance(value, str) or not value:
            return None
        candidate = Path(value)
        try:
            if not self._kuaishou_receipt_matches(record, asset, "image"):
                return None
            if candidate.is_symlink() or not candidate.is_file():
                return None
            resolved = candidate.resolve(strict=True)
            if resolved.parent != output_dir:
                return None
            digest, facts = self._kuaishou_local_fingerprint(resolved, should_cancel=should_cancel)
            if digest != record["local_sha256"] or facts.st_size != record.get("size"):
                return None
            with resolved.open("rb") as handle:
                dimensions = self._image_dimensions(handle.read(1024 * 1024))
            size = resolved.stat().st_size
            self._decode_local_image(resolved, should_cancel=should_cancel)
            if self._kuaishou_path_signature(facts) != self._kuaishou_path_signature(resolved.lstat()):
                return None
        except DownloadCancelledError:
            raise
        except (OSError, MediaDownloadError, ValueError):
            return None
        if not dimensions:
            return None
        width, height = dimensions
        if (
            asset.width
            and asset.height
            and (
                min(width, height) < min(asset.width, asset.height)
                or max(width, height) < max(asset.width, asset.height)
            )
        ):
            return None
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
    def _download_xhs_item(
        self,
        item: DownloadItem,
        output_dir: Path,
        *,
        callback: EventCallback | None,
        should_cancel: CancelCallback,
    ) -> DownloadOutcome:
        if should_cancel():
            raise DownloadCancelledError("Task cancelled")
        use_browser_cookies = self._xiaohongshu_browser_cookies_enabled()
        note, fallback = parse_xhs_note(
            item.source_url,
            cookie_profile=self.config.cookie_profile,
            use_browser_cookies=use_browser_cookies,
            allow_cookie_fallback=self.config.allow_cookie_fallback,
        )
        expected_note_id = str(item.media_id or "").strip().lower()
        if expected_note_id and note.note_id.lower() != expected_note_id:
            raise MediaDownloadError(
                "Xiaohongshu returned a different note from the requested item. "
                "The cross-wired response was blocked."
            )
        expected_profile_id = str(
            item.metadata.get("xiaohongshu_profile_id") or ""
        ).strip()
        membership_verified = item.metadata.get("profile_note_membership_verified")
        if expected_profile_id or membership_verified is not None:
            if (
                membership_verified is not True
                or not expected_profile_id
                or note.author_id != expected_profile_id
            ):
                raise MediaDownloadError(
                    "Xiaohongshu profile note belongs to a different or unverifiable "
                    "author. The cross-wired response was blocked before download."
                )
        media_type = self._xiaohongshu_note_media_type(note)
        if media_type == MediaType.VIDEO and not note.videos:
            raise MediaDownloadError(
                "Xiaohongshu identified this work as a video but returned no trusted "
                "video stream. The cover image was not downloaded as a substitute; "
                "retry the original work."
            )
        if not note.videos and not note.images and not note.live_photos:
            raise MediaDownloadError(
                "The Xiaohongshu note contains no downloadable media"
            )
        if callback:
            callback(
                EngineEvent(
                    event="metadata",
                    title=note.title,
                    upload_date=note.upload_date,
                    author=note.author,
                    media_type=media_type,
                    cookie_fallback_used=fallback,
                )
            )

        options = self._base_options(not fallback)
        output_paths: list[str] = []
        selected_format: str | None = None
        resolution: str | None = None
        completed_assets: list[RemoteAsset] = []
        static_paths: dict[int, Path] = {}
        try:
            with YoutubeDL(options) as ydl:
                if note.videos:
                    declared_assets = [
                        asset for asset in note.videos if asset.width and asset.height
                    ]
                    declared_floor = (
                        max(
                            declared_assets,
                            key=lambda asset: int(asset.width or 0)
                            * int(asset.height or 0),
                        )
                        if declared_assets
                        else None
                    )
                    highest_pixels = (
                        int(declared_floor.width or 0) * int(declared_floor.height or 0)
                        if declared_floor
                        else 0
                    )
                    # A failed highest rendition must not silently select a lower one.
                    # Original or incompletely sized streams must meet that floor.
                    flattened = [
                        RemoteAsset(
                            candidates=list(asset.candidates),
                            index=asset.index,
                            width=(
                                declared_floor.width
                                if declared_floor
                                and (
                                    asset.format_id == "original"
                                    or not (asset.width and asset.height)
                                )
                                else asset.width
                            ),
                            height=(
                                declared_floor.height
                                if declared_floor
                                and (
                                    asset.format_id == "original"
                                    or not (asset.width and asset.height)
                                )
                                else asset.height
                            ),
                            size=asset.size,
                            format_id=asset.format_id,
                            video_uri=asset.video_uri,
                            duration=asset.duration,
                        )
                        for asset in note.videos
                        if not highest_pixels
                        or asset.format_id == "original"
                        or not (asset.width and asset.height)
                        or int(asset.width or 0) * int(asset.height or 0)
                        == highest_pixels
                    ]
                    try:
                        path, chosen = self._download_first_available_asset(
                            ydl,
                            flattened,
                            output_dir,
                            note.upload_date,
                            note.title,
                            note.note_id,
                            item.source_url,
                            platform=Platform.XIAOHONGSHU,
                            media_type=MediaType.VIDEO,
                            callback=callback,
                            should_cancel=should_cancel,
                            verify_declared_dimensions=True,
                        )
                    except DownloadCancelledError:
                        raise
                    except Exception as exc:
                        raise MediaDownloadError(
                            f"Video failed: {safe_external_error_message(exc)}"
                        ) from exc
                    output_paths.append(str(path))
                    if callback:
                        callback(
                            EngineEvent(
                                event="asset_completed",
                                output_paths=list(output_paths),
                            )
                        )
                    selected_format = chosen.format_id
                    if chosen.width and chosen.height:
                        resolution = f"{chosen.width}x{chosen.height}"
                else:
                    for asset in note.images:
                        if should_cancel():
                            raise DownloadCancelledError("Task cancelled")
                        try:
                            path, chosen = self._download_first_available_asset(
                                ydl,
                                [asset],
                                output_dir,
                                note.upload_date,
                                note.title,
                                note.note_id,
                                item.source_url,
                                platform=Platform.XIAOHONGSHU,
                                media_type=MediaType.IMAGE,
                                callback=callback,
                                should_cancel=should_cancel,
                                asset_index=asset.index,
                                verify_declared_dimensions=True,
                                pair_image=any(live.index == asset.index for live in note.live_photos),
                                compact_names=item.metadata.get("_compact_asset_names") is True,
                            )
                        except DownloadCancelledError:
                            raise
                        except Exception as exc:
                            raise MediaDownloadError(
                                f"Image {asset.index} failed: {safe_external_error_message(exc)}"
                            ) from exc
                        output_paths.append(str(path))
                        static_paths[asset.index] = path
                        completed_assets.append(chosen)
                        if callback:
                            callback(
                                EngineEvent(
                                    event="asset_completed",
                                    output_paths=list(output_paths),
                                )
                            )
                    for asset in note.live_photos:
                        if should_cancel():
                            raise DownloadCancelledError("Task cancelled")
                        if asset.index not in static_paths:
                            raise MediaDownloadError(
                                "小红书未返回与实况视频对应的静态图片，无法保存完整配对。"
                            )
                        try:
                            path, chosen = self._download_first_available_asset(
                                ydl,
                                [asset],
                                output_dir,
                                note.upload_date,
                                note.title,
                                note.note_id,
                                item.source_url,
                                platform=Platform.XIAOHONGSHU,
                                media_type=MediaType.VIDEO,
                                callback=callback,
                                should_cancel=should_cancel,
                                asset_index=asset.index,
                                verify_declared_dimensions=True,
                                paired_stem=static_paths[asset.index].stem,
                                compact_names=item.metadata.get("_compact_asset_names") is True,
                            )
                        except DownloadCancelledError:
                            raise
                        except Exception as exc:
                            raise MediaDownloadError(
                                f"Live Photo video {asset.index} failed: {safe_external_error_message(exc)}"
                            ) from exc
                        output_paths.append(str(path))
                        completed_assets.append(chosen)
                        if callback:
                            callback(
                                EngineEvent(
                                    event="asset_completed",
                                    output_paths=list(output_paths),
                                )
                            )
                        selected_format = chosen.format_id
                    raw_images = note.raw.get("imageList") if isinstance(note.raw, dict) else None
                    if isinstance(raw_images, list):
                        static_indices = {asset.index for asset in note.images}
                        live_indices = {asset.index for asset in note.live_photos}
                        missing_static = [index for index, _ in enumerate(raw_images, 1) if index not in static_indices]
                        missing_live = [index for index, image in enumerate(raw_images, 1) if isinstance(image, dict) and image.get("livePhoto") is True and index not in live_indices]
                        if missing_static or missing_live:
                            raise MediaDownloadError(
                                "小红书未返回完整图片或实况动态资源，已保存可获取的文件；请稍后使用原分享链接重试。"
                            )
                    sized_assets = [
                        asset
                        for asset in completed_assets
                        if asset.width and asset.height
                    ]
                    if sized_assets:
                        largest = max(
                            sized_assets,
                            key=lambda asset: int(asset.width or 0)
                            * int(asset.height or 0),
                        )
                        resolution = f"{largest.width}x{largest.height}"
        except DownloadCancelledError:
            raise
        except Exception as exc:
            self._raise_download_error(exc, item.source_url)

        outcome = DownloadOutcome(
            output_paths=output_paths,
            title=note.title,
            upload_date=note.upload_date,
            author=note.author,
            media_type=media_type,
            selected_format=selected_format,
            resolution=resolution,
            cookie_fallback_used=fallback,
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
                    cookie_fallback_used=fallback,
                )
            )
        return outcome
    def _publish_kuaishou_asset(
        self,
        temporary: Path,
        output_dir: Path,
        upload_date: str | None,
        title: str,
        media_id: str,
        extension: str,
        asset_index: int | None,
        *,
        should_cancel: CancelCallback,
        compact_names: bool = False,
    ) -> Path:
        """Publish verified media without replacing an existing destination."""
        date_part = upload_date or "Unknown-Date"
        title_part = safe_component(
            title, fallback=media_id, limit=self.config.filename_limit
        )
        index_suffix = f"-{asset_index:03d}" if asset_index is not None else ""
        compact = compact_names and asset_index is not None
        base = f"{asset_index:03d}" if compact else f"{date_part}-{title_part}{index_suffix}"
        safe_id = _safe_media_id(media_id)

        def destination_for(collision: int) -> Path:
            suffix = (
                "" if collision == 0 else
                f" ({collision + 1})" if compact else
                f" [{safe_id}{'-' + str(collision) if collision > 1 else ''}]"
            )
            return output_dir / f"{base}{suffix}.{extension}"

        def reuse_identical(destination: Path) -> bool:
            try:
                before = destination.lstat()
                if not stat.S_ISREG(before.st_mode) or not are_files_identical(temporary, destination):
                    return False
                after = destination.lstat()
            except (FileNotFoundError, NotADirectoryError):
                return False
            # A symlink or a file replaced during comparison is not reusable.
            if (not stat.S_ISREG(after.st_mode)
                    or self._kuaishou_file_signature(before) != self._kuaishou_file_signature(after)):
                return False
            if should_cancel():
                raise DownloadCancelledError("Task cancelled")
            temporary.unlink(missing_ok=True)
            return True

        def output_error() -> TemporaryAccessError:
            return TemporaryAccessError(
                "Kuaishou output filesystem could not safely publish the "
                "verified file; check permissions and available space",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )

        if should_cancel():
            raise DownloadCancelledError("Task cancelled")
        # Look at all existing canonical names before filling any gap. Listing
        # the directory avoids probing all 10,001 possible paths on every asset.
        numbered = (
            r" \((?P<number>[0-9]+)\)" if compact else
            rf" \[{re.escape(os.path.normcase(safe_id))}(?:-(?P<number>[0-9]+))?\]"
        )
        pattern = re.compile(
            rf"{re.escape(os.path.normcase(base))}(?P<suffix>{numbered})?"
            rf"\.{re.escape(os.path.normcase(extension))}\Z"
        )
        candidates: set[int] = set()
        try:
            entries = os.scandir(output_dir)
        except OSError as exc:
            raise output_error() from exc
        with entries:
            while True:
                if should_cancel():
                    raise DownloadCancelledError("Task cancelled")
                try:
                    entry = next(entries)
                except StopIteration:
                    break
                except OSError as exc:
                    raise output_error() from exc
                name = os.path.normcase(entry.name)
                match = pattern.fullmatch(name)
                if not match:
                    continue
                number = match.group("number")
                collision = (
                    0 if match.group("suffix") is None else
                    int(number) - 1 if compact else
                    int(number) if number is not None else 1
                )
                if (0 <= collision <= 10_000
                        and name == os.path.normcase(destination_for(collision).name)):
                    candidates.add(collision)

        for collision in sorted(candidates):
            if should_cancel():
                raise DownloadCancelledError("Task cancelled")
            destination = destination_for(collision)
            if reuse_identical(destination):
                return destination

        for collision in range(10_001):
            if should_cancel():
                raise DownloadCancelledError("Task cancelled")
            destination = destination_for(collision)
            try:
                # Both names live in the same folder. The link is visible only
                # after the complete, verified temporary file has been flushed.
                # Unlike os.replace, link creation fails if another task or the
                # user created this name since the earlier path selection.
                publish_no_replace(temporary, destination)
            except FileExistsError:
                # A concurrent writer may have won since the directory scan.
                if reuse_identical(destination):
                    return destination
                continue
            except OSError as exc:
                raise output_error() from exc
            return destination
        raise MediaDownloadError("Kuaishou output has too many filename collisions")
