"""多媒体文件命名清洗、目录归档规划与格式魔数探测。

本模块承担下载资产落盘前的标准化命名与物理路径解析职责：
1. 文件名清洗与截断：清洗跨平台非法字符（Windows / Linux），限制 UTF-8 字节长度防止超长报错；
2. 格式魔数（Magic Bytes）强校验：通过首部二进制魔数精确嗅探真实多媒体格式（JPEG, PNG, WebP, AVIF, HEIC, MP4 等），
   拒绝单纯依赖 URL 后缀或上游不可信 Content-Type；
3. 多平台归档规范：统一生成 "{YYYY-MM-DD}-{Title} [{ID}].{ext}" 或合集紧凑序号 "{Index:03d}.{ext}" 标准路径。
"""
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


class NamingMixin:
    @staticmethod
    def _asset_resolution(asset: RemoteAsset) -> str | None:
        if asset.width and asset.height:
            return f"{asset.width}x{asset.height}"
        return None
    @staticmethod
    def _xiaohongshu_note_media_type(note: Any) -> MediaType:
        raw = note.raw if isinstance(getattr(note, "raw", None), dict) else {}
        raw_type = (
            str(raw.get("type") or raw.get("noteType") or raw.get("note_type") or "")
            .strip()
            .lower()
        )
        if raw_type == "video" or note.videos:
            return MediaType.VIDEO
        return MediaType.IMAGE
    def _xhs_output_path(
        self,
        output_dir: Path,
        upload_date: str | None,
        title: str,
        media_id: str,
        extension: str,
        asset_index: int | None,
        platform: Platform | None = None,
        *,
        compact_names: bool = False,
    ) -> Path:
        if compact_names and asset_index is not None:
            return output_dir / f"{asset_index:03d}.{extension}"
        date_part = upload_date or "Unknown-Date"
        title_part = safe_component(
            title, fallback=media_id, limit=self.config.filename_limit
        )
        suffix = f"-{asset_index:03d}" if asset_index is not None else ""
        if platform == Platform.KUAISHOU:
            base = f"{date_part}-{title_part}{suffix}"
            path = output_dir / f"{base}.{extension}"
            if path.exists():
                # Never replace an existing user file; use the stable media ID
                # only when the human-readable name collides.
                path = output_dir / f"{base} [{_safe_media_id(media_id)}].{extension}"
                counter = 2
                while path.exists():
                    path = output_dir / f"{base} [{_safe_media_id(media_id)}-{counter}].{extension}"
                    counter += 1
            return path
        return output_dir / f"{date_part}-{title_part} [{media_id}]{suffix}.{extension}"
    @staticmethod
    def _asset_extension(
        url: str,
        content_type: str,
        media_type: MediaType,
        first_bytes: bytes = b"",
    ) -> str:
        """基于二进制首部魔数 (Magic Bytes) 与 MIME 类型精确探测媒体文件扩展名。

        探测优先级：
        1. 物理二进制魔数嗅探（最高优先级）：精确判定 JPEG、PNG、GIF、WebP、AVIF、HEIC、MP4；
        2. HTTP 响应头部 Content-Type 映射兜底；
        3. URL 原始路径扩展名回退；
        4. 根据 media_type 降级为 mp4 或 jpg。

        Args:
            url: 媒体资源的远程下载 URL。
            content_type: HTTP 响应头部的 Content-Type 字段。
            media_type: 媒体归类（VIDEO / IMAGE）。
            first_bytes: 传输开始时捕获的媒体文件前部分字节（至少 12~32 字节）。

        Returns:
            不带句点的规范化小写扩展名（如 'mp4', 'heic', 'webp', 'jpg'）。
        """
        ambiguous_iso_image = False
        if first_bytes.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if first_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if first_bytes.startswith((b"GIF87a", b"GIF89a")):
            return "gif"
        if first_bytes.startswith(b"RIFF") and first_bytes[8:12] == b"WEBP":
            return "webp"
        if len(first_bytes) >= 12 and first_bytes[4:8] == b"ftyp":
            box_size = int.from_bytes(first_bytes[:4], "big")
            box_end = min(len(first_bytes), box_size) if box_size >= 12 else 12
            brands = {first_bytes[8:12]}
            brands.update(
                first_bytes[offset : offset + 4] for offset in range(16, box_end - 3, 4)
            )
            if brands & {b"avif", b"avis"}:
                return "avif"
            if brands & {b"heic", b"heix", b"hevc", b"hevx"}:
                return "heic"
            if media_type == MediaType.VIDEO:
                return "mp4"
            ambiguous_iso_image = bool(brands & {b"mif1", b"msf1"})
        extensions = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
            "image/avif": "avif",
            "image/heic": "heic",
            "image/heif": "heic",
            "image/gif": "gif",
            "video/mp4": "mp4",
            "video/webm": "webm",
            "video/quicktime": "mov",
        }
        if content_type in extensions:
            return extensions[content_type]
        suffix = Path(unquote(urlsplit(url).path)).suffix.lower().lstrip(".")
        if ambiguous_iso_image:
            if suffix in {"avif", "avis"}:
                return "avif"
            if suffix in {"heic", "heif", "heix", "hevc", "hevx"}:
                return "heic"
            return "bin"
        if re.fullmatch(r"[a-z0-9]{2,5}", suffix):
            return suffix
        guessed = mimetypes.guess_extension(content_type or "")
        if guessed:
            return guessed.lstrip(".").replace("jpeg", "jpg")
        return "mp4" if media_type == MediaType.VIDEO else "jpg"
    @staticmethod
    def _media_id(url: str) -> str | None:
        path = urlsplit(url).path.rstrip("/")
        value = path.rsplit("/", 1)[-1]
        return value or None
    @staticmethod
    def _author_from_info(
        info: dict[str, Any], fallback: str | None = "Unknown Author"
    ) -> str | None:
        extractor = " ".join(
            str(info.get(key) or "") for key in ("extractor", "extractor_key")
        ).lower()
        keys = (
            ("channel", "uploader", "playlist_uploader", "creator", "artist")
            if "douyin" in extractor
            else ("uploader", "channel", "playlist_uploader", "creator", "artist")
        )
        for key in keys:
            value = info.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return fallback
    @staticmethod
    def _normalize_date(value: Any) -> str | None:
        if not value:
            return None
        text = re.sub(r"\D", "", str(value))
        if len(text) >= 8:
            return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        return None
    @staticmethod
    def _media_type(info: dict[str, Any]) -> MediaType:
        if info.get("vcodec") not in (None, "none") or info.get("formats"):
            return MediaType.VIDEO
        if info.get("thumbnails") and not info.get("formats"):
            return MediaType.IMAGE
        return MediaType.UNKNOWN
    @staticmethod
    def _entry_url(entry: dict[str, Any], platform: Platform, original_url: str) -> str:
        for key in ("webpage_url", "original_url"):
            value = entry.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        value = entry.get("url")
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
        media_id = str(entry.get("id") or value or "").strip()
        if platform == Platform.YOUTUBE and media_id:
            return f"https://www.youtube.com/watch?v={media_id}"
        if platform == Platform.BILIBILI and media_id:
            return f"https://www.bilibili.com/video/{media_id}"
        if platform == Platform.DOUYIN and media_id.isdigit():
            return f"https://www.douyin.com/video/{media_id}"
        if media_id and len(media_id) > 5:
            return media_id
        return original_url
    @staticmethod
    def _format_id(info: dict[str, Any]) -> str | None:
        requested = info.get("requested_formats") or []
        values = [
            str(value.get("format_id")) for value in requested if value.get("format_id")
        ]
        if values:
            return "+".join(values)
        value = info.get("format_id")
        return str(value) if value else None
    @staticmethod
    def _resolution(info: dict[str, Any]) -> str | None:
        candidates = [info, *(info.get("requested_formats") or [])]
        dimensions = [
            (int(value.get("width") or 0), int(value.get("height") or 0))
            for value in candidates
            if isinstance(value, dict)
        ]
        width, height = max(
            dimensions, key=lambda pair: pair[0] * pair[1], default=(0, 0)
        )
        return f"{width}x{height}" if width and height else None
    @staticmethod
    def _paths_from_info(info: dict[str, Any], ydl: YoutubeDL) -> list[str]:
        paths: list[str] = []
        for key in ("filepath", "_filename"):
            if info.get(key):
                paths.append(str(info[key]))
        for requested in info.get("requested_downloads") or []:
            for key in ("filepath", "filename"):
                if requested.get(key):
                    paths.append(str(requested[key]))
        with contextlib.suppress(Exception):
            paths.append(ydl.prepare_filename(info))
        return paths
    @staticmethod
    def _existing_unique_paths(paths: Iterable[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in paths:
            path = Path(value).expanduser().resolve()
            text = str(path)
            if text not in seen and path.is_file() and path.stat().st_size > 0:
                seen.add(text)
                result.append(text)
        return result
