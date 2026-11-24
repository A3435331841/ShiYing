"""索引后端契约：默认 SQLite 实现，测试可用内存实现。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence


# 平台中文标签：资料库搜索词会下推后端，而前端本地匹配串包含平台中文名
# （app/static/task-views.js:select），索引层必须用同一口径才能搜到「抖音」等。
PLATFORM_LABELS: dict[str, str] = {
    "douyin": "抖音",
    "selected-douyin": "抖音精选",
    "kuaishou": "快手",
    "xiaohongshu": "小红书",
    "bilibili": "B站",
    "youtube": "YouTube",
    "wechat": "视频号",
}


def platform_keys_for_query(q: str) -> list[str]:
    """返回 q 作为子串命中的平台键，如「抖音」命中 douyin 与 selected-douyin。"""
    needle = q.strip().lower()
    if not needle:
        return []
    return [key for key, label in PLATFORM_LABELS.items() if needle in label.lower()]


@dataclass(frozen=True)
class IndexedJob:
    job_id: str
    revision: int
    file_mtime: float
    status: str
    platform: str
    media_type: str
    title: str
    author: str = ""
    source_url: str = ""
    created_at: str = ""
    output_dir: str = ""
    total_items: int = 0
    completed_items: int = 0
    failed_items: int = 0
    file_paths: tuple[str, ...] = field(default_factory=tuple)
    total_bytes: int = 0


def entry_has_existing_file(entry: IndexedJob) -> bool:
    try:
        return any(Path(path).is_file() for path in entry.file_paths)
    except OSError:
        return False


class IndexBackend(Protocol):
    def upsert(self, entry: IndexedJob) -> None: ...

    def delete(self, job_id: str) -> None: ...

    def fingerprints(self) -> dict[str, tuple[int, float]]: ...

    def query(
        self,
        *,
        q: str = "",
        platform: str | None = None,
        media_type: str | None = None,
        sort: str = "created_desc",
        cursor: str | None = None,
        limit: int = 50,
        exists_only: bool = False,
    ) -> tuple[list[IndexedJob], str | None, int]: ...

    def rebuild(self, entries: Sequence[IndexedJob]) -> int: ...

    def close(self) -> None: ...


class InMemoryIndexBackend:
    """单测用内存实现，语义与 SQLite 后端保持一致。"""

    def __init__(self) -> None:
        self._entries: dict[str, IndexedJob] = {}

    def upsert(self, entry: IndexedJob) -> None:
        self._entries[entry.job_id] = entry

    def delete(self, job_id: str) -> None:
        self._entries.pop(job_id, None)

    def fingerprints(self) -> dict[str, tuple[int, float]]:
        return {
            job_id: (entry.revision, entry.file_mtime)
            for job_id, entry in self._entries.items()
        }

    def query(
        self,
        *,
        q: str = "",
        platform: str | None = None,
        media_type: str | None = None,
        sort: str = "created_desc",
        cursor: str | None = None,
        limit: int = 50,
        exists_only: bool = False,
    ) -> tuple[list[IndexedJob], str | None, int]:
        from .indexer import filter_entries

        return filter_entries(
            list(self._entries.values()),
            q=q,
            platform=platform,
            media_type=media_type,
            sort=sort,
            cursor=cursor,
            limit=limit,
            exists_only=exists_only,
        )

    def rebuild(self, entries: Sequence[IndexedJob]) -> int:
        self._entries = {entry.job_id: entry for entry in entries}
        return len(self._entries)

    def close(self) -> None:
        self._entries.clear()