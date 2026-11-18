"""YouTube 搜索后端：复用 yt-dlp 内置 `ytsearchN:` extractor。

扁平条目已包含标题、频道、时长（封面按 video id 从 i.ytimg.com 推导），
不需要逐条完整解析。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Protocol

from ...errors import DiscoveryError, SiteIssueCode
from ...models import Platform
from ..models import SearchHit

_YOUTUBE_ID_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")
_YOUTUBE_COVER = "https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


class EnginePort(Protocol):
    def search(self, query: str, platform: Platform, limit: int = 10) -> list[dict[str, Any]]: ...


def _youtube_url(entry: dict[str, Any]) -> str | None:
    video_id = entry.get("id")
    if isinstance(video_id, str) and _YOUTUBE_ID_RE.match(video_id):
        return f"https://www.youtube.com/watch?v={video_id}"
    for key in ("webpage_url", "url"):
        value = entry.get(key)
        if isinstance(value, str):
            match = re.search(r"(?:v=|youtu\.be/|shorts/)([0-9A-Za-z_-]{11})", value)
            if match:
                return f"https://www.youtube.com/watch?v={match.group(1)}"
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _published(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if re.fullmatch(r"\d{8}", text):
        try:
            return datetime.strptime(text, "%Y%m%d").date().isoformat()
        except ValueError:
            return None
    return text[:20]


def _cover(entry: dict[str, Any], video_id: str) -> str:
    value = entry.get("thumbnail")
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    thumbnails = entry.get("thumbnails")
    if isinstance(thumbnails, list):
        for thumb in reversed(thumbnails):
            if isinstance(thumb, dict) and isinstance(thumb.get("url"), str):
                return thumb["url"]
    # ytsearch 的扁平条目通常不带缩略图；按 video id 使用 YouTube 固定封面地址。
    return _YOUTUBE_COVER.format(video_id=video_id)


def to_hit(entry: dict[str, Any], keyword: str) -> SearchHit | None:
    url = _youtube_url(entry)
    if not url:
        return None
    video_id = url.split("v=")[-1]
    title = str(entry.get("title") or "").strip() or "未命名作品"
    author = entry.get("uploader") or entry.get("channel") or entry.get("creator")
    return SearchHit(
        platform=Platform.YOUTUBE,
        source_url=url,
        media_id=video_id,
        title=title[:200],
        author=str(author).strip()[:80] if author else None,
        cover_url=_cover(entry, video_id),
        duration_seconds=_int_or_none(entry.get("duration")),
        published_at=_published(entry.get("upload_date")),
        keyword=keyword,
    )


class YtDlpSearchBackend:
    """YouTube 搜索（引擎实例可注入，测试不触网）。"""

    platforms = (Platform.YOUTUBE,)

    def __init__(self, engine: EnginePort) -> None:
        self.engine = engine

    def search(self, platform: Platform, keyword: str, limit: int = 10) -> list[SearchHit]:
        if platform is not Platform.YOUTUBE:
            raise DiscoveryError(
                f"{platform.value} 不走 yt-dlp 搜索",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )
        entries = self.engine.search(keyword, platform, limit)
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for entry in entries:
            hit = to_hit(entry, keyword)
            if hit is None or hit.source_url in seen:
                continue
            seen.add(hit.source_url)
            hits.append(hit)
            if len(hits) >= limit:
                break
        return hits
