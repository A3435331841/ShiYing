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
from ..platforms import UnsupportedUrlError, identify_url
from ..file_publish import publish_no_replace, publish_pair_image, publish_unique
from ..xiaohongshu import RemoteAsset, discover_profile as discover_xhs_profile
from ..xiaohongshu import (
    _XiaohongshuRedirectRejected,
    _open_xiaohongshu_response,
)
from ..xiaohongshu import is_trusted_xiaohongshu_asset_url
from ..xiaohongshu import parse_note as parse_xhs_note

from .common import *  # noqa: F401,F403


class DiscoveryMixin:
    def _report_discovery(self, message: str) -> None:
        if self.discovery_callback:
            self.discovery_callback(EngineEvent(event="probing", message=message))
    def discover(
        self,
        url: str,
        platform: Platform,
        kind: SourceKind,
        *,
        should_cancel: CancelCallback | None = None,
    ) -> DiscoveryResult:
        should_cancel = should_cancel or (lambda: False)
        if should_cancel():
            raise DownloadCancelledError("Task cancelled")
        if platform == Platform.KUAISHOU:
            return self._discover_kuaishou(url, should_cancel)
        if platform == Platform.BILIBILI and re.search(r"/(?:read/cv|opus/)", url):
            from ..bilibili_opus import parse_bilibili_opus
            opus = parse_bilibili_opus(url)
            item = DownloadItem(
                id=_item_key(Platform.BILIBILI, opus.opus_id, url, 1),
                media_id=opus.opus_id,
                source_url=url,
                title=opus.title,
                author=opus.author,
                media_type=MediaType.IMAGE,
                metadata={"bilibili_opus_id": opus.opus_id, "bilibili_author_id": opus.author_id},
            )
            return DiscoveryResult(author=opus.author, items=[item])
        if platform == Platform.WECHAT:
            from ..wechat import WeChatUserError, profile_yuanbao, media_selection, load_yuanbao_cookie, work_identity
            
            with threading.RLock():
                try:
                    cookie = load_yuanbao_cookie()
                except WeChatUserError as exc:
                    raise DiscoveryError(str(exc)) from exc
            if not cookie:
                raise DiscoveryError("视频号解析需要配置腾讯元宝登录态，请在设置中配置后重试。")
            feed, author_info = profile_yuanbao(url, cookie)
            media_selection(feed)
            author = author_info.get("nickname") or feed.get("nickname") or "微信视频号创作者"
            title = feed.get("description") or "视频号作品"
            media_id = work_identity(feed, url)
            if not media_id:
                raise DiscoveryError("视频号未返回可验证的作品身份。")
            item_meta = {}
            cover_url = feed.get("coverUrl") or feed.get("thumbUrl") or feed.get("cover_url") or feed.get("thumb_url")
            if cover_url:
                item_meta["cover_url"] = str(cover_url)
            item = DownloadItem(
                id=_item_key(Platform.WECHAT, media_id, url, 1),
                media_id=media_id,
                source_url=url,
                title=title,
                author=author,
                media_type=MediaType.VIDEO,
                metadata=item_meta,
            )
            return DiscoveryResult(author=author, items=[item])
        xiaohongshu_browser_cookies = False
        if platform == Platform.XIAOHONGSHU:
            xiaohongshu_browser_cookies = self._xiaohongshu_browser_cookies_enabled()

        short_link_fallback = False
        if kind == SourceKind.SHORT_LINK:
            resolved_url, short_link_fallback = self._resolve_short_url(url)
            if resolved_url != url:
                resolved = identify_url(resolved_url)
                if resolved.platform != platform:
                    raise DiscoveryError(
                        "The short link redirected to a different platform and was "
                        "blocked"
                    )
                result = self.discover(
                    resolved.url,
                    resolved.platform,
                    resolved.kind,
                    should_cancel=should_cancel,
                )
                if platform == Platform.XIAOHONGSHU:
                    if resolved.kind not in {
                        SourceKind.ITEM,
                        SourceKind.PROFILE,
                    }:
                        raise DiscoveryError(
                            "The Xiaohongshu short link resolved to an unsupported "
                            "target"
                        )
                    parsed_resolved = urlsplit(resolved.url)
                    binding_url = urlunsplit(
                        parsed_resolved._replace(query="", fragment="")
                    )
                    for discovered_item in result.items:
                        discovered_item.metadata.update(
                            {
                                "xiaohongshu_resolved_source_url": binding_url,
                                "xiaohongshu_resolved_source_kind": (
                                    resolved.kind.value
                                ),
                            }
                        )
                result.cookie_fallback_used |= short_link_fallback
                if result.cookie_fallback_used and not result.warning:
                    result.warning = COOKIE_FALLBACK_WARNING
                return result

        if platform == Platform.XIAOHONGSHU and kind == SourceKind.PROFILE:
            profile = discover_xhs_profile(
                url,
                cookie_profile=self.config.cookie_profile,
                use_browser_cookies=xiaohongshu_browser_cookies,
                allow_cookie_fallback=self.config.allow_cookie_fallback,
                should_cancel=should_cancel,
            )
            items = [
                DownloadItem(
                    id=_item_key(platform, self._media_id(entry), entry, index),
                    media_id=self._media_id(entry),
                    source_url=entry,
                    title="Untitled Xiaohongshu note",
                    author=profile.author,
                    playlist_index=index,
                    extractor_key="XiaoHongShu",
                    metadata={
                        "xiaohongshu_profile_id": profile.profile_id,
                        "profile_note_membership_verified": True,
                    },
                )
                for index, entry in enumerate(profile.note_urls, start=1)
            ]
            return DiscoveryResult(
                author=profile.author,
                items=items,
                cookie_fallback_used=profile.cookie_fallback_used | short_link_fallback,
                warning=(
                    " ".join(
                        value
                        for value in (
                            (
                                COOKIE_FALLBACK_WARNING
                                if profile.cookie_fallback_used or short_link_fallback
                                else None
                            ),
                            profile.warning,
                        )
                        if value
                    )
                    or None
                ),
                discovery_complete=profile.discovery_complete,
            )

        if platform == Platform.DOUYIN and kind == SourceKind.PROFILE:
            profile = discover_douyin_profile(
                url,
                cookie_profile=self.config.cookie_profile,
                use_browser_cookies=self.config.cookie_browser == "chrome",
                allow_cookie_fallback=self.config.allow_cookie_fallback,
                should_cancel=should_cancel,
                status_callback=self._report_discovery,
            )
            profile_id = self._douyin_profile_id(url)
            if not profile_id:
                raise DiscoveryError("The Douyin profile identity is missing")
            items: list[DownloadItem] = []
            seen_media_ids: set[str] = set()
            for index, entry in enumerate(profile.video_urls, start=1):
                media_id = self._media_id(entry)
                cached_media = profile.media_metadata.get(media_id or "")
                if (
                    not media_id
                    or media_id in seen_media_ids
                    or not isinstance(cached_media, dict)
                    or not is_complete_profile_media_metadata(
                        cached_media,
                        media_id,
                        profile_id,
                    )
                ):
                    raise TemporaryAccessError(
                        "Douyin returned incomplete profile media metadata. "
                        "No numeric placeholder queue was created; retry the "
                        "original profile after the temporary response clears."
                    )
                seen_media_ids.add(media_id)
                media_kind = str(cached_media.get("media_kind") or "").strip()
                title = str(cached_media.get("title") or "").strip()
                if not title or title == media_id or title.isdigit():
                    title = f"Untitled Douyin {media_kind}"
                author = str(cached_media.get("author") or profile.author).strip()
                create_time = cached_media.get("create_time")
                if (
                    not author
                    or type(create_time) is not int
                    or create_time <= 0
                    or media_kind not in {"video", "image"}
                ):
                    raise TemporaryAccessError(
                        "Douyin returned incomplete profile titles or ownership "
                        "metadata. No placeholder items were created; retry after "
                        "a short wait."
                    )
                metadata: dict[str, Any] = {
                    "profile_url": url,
                    "profile_owner_verified": True,
                    "douyin_profile_media": dict(cached_media),
                }
                items.append(
                    DownloadItem(
                        id=_item_key(platform, media_id, entry, index),
                        media_id=media_id,
                        source_url=entry,
                        title=title,
                        upload_date=self._douyin_upload_date(create_time),
                        author=author,
                        playlist_index=index,
                        extractor_key="Douyin",
                        media_type=(
                            MediaType.IMAGE
                            if media_kind == "image"
                            else MediaType.VIDEO
                        ),
                        metadata=metadata,
                    )
                )
            return DiscoveryResult(
                author=profile.author,
                items=items,
                cookie_fallback_used=profile.cookie_fallback_used | short_link_fallback,
                warning=(
                    " ".join(
                        value
                        for value in (
                            (
                                COOKIE_FALLBACK_WARNING
                                if profile.cookie_fallback_used or short_link_fallback
                                else None
                            ),
                            profile.warning,
                            (
                                DOUYIN_LIVE_PHOTO_STATIC_FALLBACK_WARNING
                                if any(
                                    isinstance(
                                        item.metadata.get("douyin_profile_media"),
                                        dict,
                                    )
                                    and item.metadata["douyin_profile_media"].get(
                                        "live_photo_static_fallback_indexes"
                                    )
                                    for item in items
                                )
                                else None
                            ),
                        )
                        if value
                    )
                    or None
                ),
                discovery_complete=profile.discovery_complete,
            )

        if platform == Platform.DOUYIN and kind == SourceKind.ITEM:
            return self._discover_douyin_item(url, should_cancel)

        if platform == Platform.XIAOHONGSHU and kind in {
            SourceKind.ITEM,
            SourceKind.SHORT_LINK,
        }:
            note, fallback = parse_xhs_note(
                url,
                cookie_profile=self.config.cookie_profile,
                use_browser_cookies=xiaohongshu_browser_cookies,
                allow_cookie_fallback=self.config.allow_cookie_fallback,
            )
            media_type = self._xiaohongshu_note_media_type(note)
            if media_type == MediaType.VIDEO and not note.videos:
                raise TemporaryAccessError(
                    "Xiaohongshu identified this work as a video but returned no "
                    "trusted video stream. Retry the original link; the cover image "
                    "was not downloaded as a substitute."
                )
            xhs_meta = {}
            if getattr(note, "cover_url", None):
                xhs_meta["cover_url"] = str(note.cover_url)
            elif getattr(note, "image_urls", None) and note.image_urls:
                xhs_meta["cover_url"] = str(note.image_urls[0])
            item = DownloadItem(
                id=_item_key(platform, note.note_id, url, 1),
                media_id=note.note_id,
                source_url=url,
                title=note.title,
                upload_date=note.upload_date,
                author=note.author,
                playlist_index=1,
                extractor_key="XiaoHongShu",
                media_type=media_type,
                metadata=xhs_meta,
            )
            return DiscoveryResult(
                author=note.author or "Xiaohongshu Author",
                items=[item],
                cookie_fallback_used=fallback | short_link_fallback,
                warning=(
                    COOKIE_FALLBACK_WARNING if fallback or short_link_fallback else None
                ),
            )

        return self._discover_with_ytdlp(url, platform, kind, should_cancel)
    def _discover_kuaishou(self, url: str, should_cancel: CancelCallback) -> DiscoveryResult:
        if self.config.cookie_browser not in {None, "chrome"}:
            raise TemporaryAccessError("Kuaishou supports Chrome Cookie or explicit anonymous access only")
        result = discover_kuaishou(
            url, cookie_profile=self.config.cookie_profile,
            use_browser_cookies=self.config.cookie_browser == "chrome",
            should_cancel=should_cancel, status_callback=self._report_discovery,
        )
        items = [DownloadItem(
            id=_item_key(Platform.KUAISHOU, video.media_id, video.url, index),
            media_id=video.media_id, source_url=video.url, title=video.title,
            author=video.author, upload_date=video.upload_date, playlist_index=index,
            extractor_key="Kuaishou", media_type=(MediaType.IMAGE if video.media_type == "image" else MediaType.VIDEO),
            metadata={
                "kuaishou_author_id": video.author_id,
                "kuaishou_source_kind": result.source_kind,
                "kuaishou_source_id": result.source_id,
                "kuaishou_refresh_url": url if result.source_kind == "item" else video.url,
            },
        ) for index, video in enumerate(result.videos, start=1)]
        return DiscoveryResult(
            author=result.videos[0].author if result.videos else "Kuaishou Author",
            items=items, warning=result.warning, discovery_complete=result.complete,
        )
    def _resolve_short_url(self, url: str) -> tuple[str, bool]:
        parsed = urlsplit(url)
        if parsed.hostname in {"xhslink.cn", "xhslink.com"}:
            if parsed.username or parsed.password or not ((parsed.scheme == "https" and parsed.port in (None, 443)) or (parsed.scheme == "http" and parsed.port in (None, 80))):
                raise DiscoveryError("Invalid Xiaohongshu share link")
            url = urlunsplit(parsed._replace(scheme="https", netloc=parsed.hostname))
            try:
                response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=False, timeout=15)
                try:
                    target = urljoin(url, response.headers.get("Location", ""))
                    resolved = identify_url(target)
                    if response.status_code not in {301, 302, 303, 307, 308} or resolved.platform != Platform.XIAOHONGSHU or resolved.kind not in {SourceKind.ITEM, SourceKind.PROFILE} or urlsplit(target).scheme != "https":
                        raise DiscoveryError("小红书分享链接未返回有效作品地址，请重新复制分享链接。")
                    return resolved.url, False
                finally:
                    response.close()
            except DiscoveryError:
                raise
            except UnsupportedUrlError as exc:
                # 短链跳到了非作品地址（常见于分享链接过期后回首页），
                # 网络本身没问题，不应归为网络错误误导用户。
                raise DiscoveryError(
                    "小红书分享链接可能已过期或未指向作品，请从小红书重新复制分享链接。",
                    issue_code=SiteIssueCode.CONTENT_UNAVAILABLE,
                ) from exc
            except Exception as exc:
                raise DiscoveryError(
                    "无法解析小红书分享链接，请检查网络并重新复制链接。",
                    issue_code=SiteIssueCode.NETWORK_ERROR,
                ) from exc

        def operation(use_cookies: bool) -> str:
            try:
                with YoutubeDL(self._base_options(use_cookies)) as ydl:
                    response = ydl.urlopen(
                        Request(url, headers={"Accept": "text/html,*/*"})
                    )
                    try:
                        return str(response.url)
                    finally:
                        response.close()
            except DownloadError:
                raise
            except Exception as exc:
                raise DownloadError(str(exc)) from exc

        return self._run_with_cookie_fallback(operation, url=url)
    def _discover_with_ytdlp(
        self,
        url: str,
        platform: Platform,
        kind: SourceKind,
        should_cancel: CancelCallback,
    ) -> DiscoveryResult:
        def operation(use_cookies: bool) -> tuple[dict[str, Any], str | None]:
            if should_cancel():
                raise DownloadCancelled("Task cancelled")
            expand_bilibili_item = (
                platform == Platform.BILIBILI and kind == SourceKind.ITEM
            )
            options = {
                **self._base_options(use_cookies),
                "skip_download": True,
                "extract_flat": False if expand_bilibili_item else "in_playlist",
                "lazy_playlist": False,
                "ignoreerrors": False,
                "logger": _YdlLogger(),
            }
            with YoutubeDL(options) as ydl:
                result = ydl.extract_info(url, download=False)
                if not isinstance(result, dict):
                    raise DownloadError("The URL returned no downloadable media")
                if platform == Platform.DOUYIN:
                    expected_id = self._douyin_video_id(url)
                    if not expected_id:
                        raise DownloadError("The Douyin URL has no video identifier")
                    self._validate_douyin_info(result, expected_id, url)

                probed_author: str | None = None
                raw_entries = result.get("entries")
                if raw_entries is not None and str(
                    self._author_from_info(result)
                ).startswith("Unknown"):
                    entries = [
                        entry for entry in list(raw_entries) if isinstance(entry, dict)
                    ]
                    result["entries"] = entries
                    for entry in entries[:5]:
                        probed_author = self._author_from_info(
                            entry,
                            fallback=None,
                        )
                        if probed_author:
                            break
                    for entry in entries[:5]:
                        if probed_author:
                            break
                        if should_cancel():
                            raise DownloadCancelled("Task cancelled")
                        entry_url = self._entry_url(entry, platform, original_url=url)
                        try:
                            details = ydl.extract_info(entry_url, download=False)
                        except DownloadError:
                            continue
                        if isinstance(details, dict):
                            probed_author = self._author_from_info(
                                details, fallback=None
                            )
                        if probed_author:
                            break
            return result, probed_author

        (info, probed_author), fallback = self._run_with_cookie_fallback(
            operation, url=url
        )
        raw_entries = info.get("entries")
        entries = list(raw_entries) if raw_entries is not None else [info]
        entries = [entry for entry in entries if isinstance(entry, dict)]
        if not entries:
            raise DiscoveryError("No downloadable items were found")

        author = self._author_from_info(info)
        if author.startswith("Unknown"):
            author = probed_author or self._author_from_info(entries[0])
        items: list[DownloadItem] = []
        for index, entry in enumerate(entries, start=1):
            if should_cancel():
                raise DownloadCancelledError("Task cancelled")
            media_id = str(entry.get("id") or "").strip() or None
            entry_url = self._entry_url(entry, platform, original_url=url)
            entry_meta = {"verification_url": url} if platform == Platform.DOUYIN else {}
            thumb_url = entry.get("thumbnail")
            if thumb_url and isinstance(thumb_url, str):
                entry_meta["cover_url"] = thumb_url
            items.append(
                DownloadItem(
                    id=_item_key(platform, media_id, entry_url, index),
                    media_id=media_id,
                    source_url=entry_url,
                    title=str(entry.get("title") or media_id or f"Item {index}"),
                    upload_date=self._normalize_date(entry.get("upload_date")),
                    author=self._author_from_info(entry, fallback=None) or author,
                    extractor_key=str(entry.get("extractor_key") or "") or None,
                    playlist_index=index,
                    media_type=self._media_type(entry),
                    metadata=entry_meta,
                )
            )
        return DiscoveryResult(
            author=author,
            items=items,
            cookie_fallback_used=fallback,
            warning=COOKIE_FALLBACK_WARNING if fallback else None,
        )
    @staticmethod
    def _download_format_options(platform: Platform) -> dict[str, Any]:
        options: dict[str, Any] = {"format": "bestvideo*+bestaudio/best"}
        if platform == Platform.DOUYIN:
            options["format_sort"] = ["res"]
        return options
