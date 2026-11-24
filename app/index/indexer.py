"""增量索引器：按 job_id + revision + mtime 对比，防抖刷新，失败回退 V1。"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .backend import PLATFORM_LABELS, IndexBackend, IndexedJob, entry_has_existing_file
from .sqlite_backend import (
    SORT_CREATED_ASC,
    SUPPORTED_SORTS,
    SQLiteIndexBackend,
    decode_cursor,
    encode_cursor,
)

STATUS_READY = "ready"
STATUS_REBUILDING = "rebuilding"
STATUS_FALLBACK = "fallback"
LIBRARY_STATUSES = frozenset({"completed", "partial"})


def _iso_from_mtime(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, timezone.utc).isoformat()


def _media_type(values: Iterable[str | None]) -> str:
    """按前端资料库分类口径归一 media_type（对齐 task-views.js:mediaType）。

    前端卡片与「视频/图文/音频」筛选都把「含视频」的任务归为 video；索引若把
    video+image 存成 mixed，下推 media_type=video 时该任务就会被漏掉。单一类型
    原样保留（例如 live_photo），只有多类型时按同样的优先级归并。
    """
    types = {value for value in values if value}
    if not types:
        return "unknown"
    if len(types) == 1:
        return next(iter(types))
    if "video" in types:
        return "video"
    if types <= {"image", "live_photo"}:
        return "image"
    if types <= {"audio"}:
        return "audio"
    return "mixed"


def build_entry(path: Path) -> IndexedJob | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        stat = path.stat()
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    job_id = payload.get("id")
    if not isinstance(job_id, str) or not job_id:
        return None
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    first = items[0] if items and isinstance(items[0], dict) else {}
    media_type = _media_type(
        item.get("media_type") for item in items if isinstance(item, dict)
    )
    paths: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for raw in item.get("output_paths") or []:
            if isinstance(raw, str) and raw not in paths:
                paths.append(raw)
    total_bytes = 0
    for raw in paths:
        try:
            candidate = Path(raw)
            if candidate.is_file():
                total_bytes += candidate.stat().st_size
        except OSError:
            continue
    created_at = payload.get("created_at")
    if not isinstance(created_at, str) or not created_at:
        created_at = _iso_from_mtime(stat.st_mtime)
    return IndexedJob(
        job_id=job_id,
        revision=int(payload.get("revision") or 0),
        file_mtime=stat.st_mtime,
        status=str(payload.get("status") or "queued"),
        platform=str(payload.get("platform") or ""),
        media_type=media_type,
        title=str(first.get("title") or payload.get("source_url") or job_id),
        author=str(payload.get("author") or first.get("author") or ""),
        source_url=str(payload.get("source_url") or ""),
        created_at=created_at,
        output_dir=str(payload.get("output_dir") or payload.get("output_root") or ""),
        total_items=int(payload.get("total_items") or 0),
        completed_items=int(payload.get("completed_items") or 0),
        failed_items=int(payload.get("failed_items") or 0),
        file_paths=tuple(paths),
        total_bytes=total_bytes,
    )


def entry_from_job(job) -> IndexedJob:
    items = list(job.items)
    first = items[0] if items else None
    media_type = _media_type(item.media_type.value for item in items if item.media_type)
    paths: list[str] = []
    total_bytes = 0
    for item in items:
        for raw in item.output_paths:
            if raw not in paths:
                paths.append(raw)
    for raw in paths:
        try:
            candidate = Path(raw)
            if candidate.is_file():
                total_bytes += candidate.stat().st_size
        except OSError:
            continue
    return IndexedJob(
        job_id=job.id,
        revision=job.revision,
        file_mtime=0.0,
        status=job.status.value,
        platform=job.platform.value,
        media_type=media_type,
        title=(first.title if first and first.title else job.source_url or job.id),
        author=job.author or (first.author if first and first.author else ""),
        source_url=job.source_url,
        created_at=job.created_at.isoformat(),
        output_dir=job.output_dir or job.output_root,
        total_items=job.total_items,
        completed_items=job.completed_items,
        failed_items=job.failed_items,
        file_paths=tuple(paths),
        total_bytes=total_bytes,
    )


def filter_entries(
    entries: Iterable[IndexedJob],
    *,
    q: str = "",
    platform: str | None = None,
    media_type: str | None = None,
    sort: str = "created_desc",
    cursor: str | None = None,
    limit: int = 50,
    exists_only: bool = False,
) -> tuple[list[IndexedJob], str | None, int]:
    if sort not in SUPPORTED_SORTS:
        raise ValueError(f"unsupported sort: {sort}")
    selected = [entry for entry in entries if entry.status in LIBRARY_STATUSES]
    if platform:
        selected = [entry for entry in selected if entry.platform == platform]
    if media_type:
        selected = [entry for entry in selected if entry.media_type == media_type]
    keyword = q.strip().lower()
    if keyword:
        selected = [
            entry
            for entry in selected
            if keyword
            in (
                f"{entry.title} {entry.author} {entry.source_url} "
                f"{PLATFORM_LABELS.get(entry.platform, '')}"
            ).lower()
        ]
    if exists_only:
        selected = [entry for entry in selected if entry_has_existing_file(entry)]
    reverse = sort != SORT_CREATED_ASC
    selected.sort(key=lambda entry: (entry.created_at, entry.job_id), reverse=reverse)
    total = len(selected)
    if cursor:
        decoded = decode_cursor(cursor)
        if decoded is None:
            return [], None, total
        created_at, job_id = decoded
        if sort == SORT_CREATED_ASC:
            selected = [
                entry
                for entry in selected
                if (entry.created_at, entry.job_id) > (created_at, job_id)
            ]
        else:
            selected = [
                entry
                for entry in selected
                if (entry.created_at, entry.job_id) < (created_at, job_id)
            ]
    page = selected[:limit]
    next_cursor = (
        encode_cursor(page[-1].created_at, page[-1].job_id)
        if len(selected) > limit and page
        else None
    )
    return page, next_cursor, total


class JobIndexer:
    def __init__(
        self,
        state_dir: str | Path,
        db_path: str | Path,
        *,
        enabled: bool = True,
        debounce_seconds: float = 2.0,
        backend: IndexBackend | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.db_path = Path(db_path)
        self.enabled = enabled
        self.debounce_seconds = debounce_seconds
        self._backend: IndexBackend | None = None
        self._injected_backend = backend is not None
        self._lock = threading.RLock()
        self._status = STATUS_READY if enabled else STATUS_FALLBACK
        self._last_scan = 0.0
        self._last_error: str | None = None
        self._last_rebuild_seconds: float | None = None
        self._rebuild_scheduled = False
        if backend is not None:
            self._backend = backend
        elif enabled:
            self._open_backend()

    @property
    def backend(self) -> IndexBackend | None:
        return self._backend

    def _open_backend(self) -> bool:
        try:
            self._backend = SQLiteIndexBackend(self.db_path)
            return True
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._backend = None
            self._status = STATUS_FALLBACK
            return False

    def _schedule_rebuild(self) -> None:
        if self._rebuild_scheduled or self._injected_backend:
            return
        self._rebuild_scheduled = True
        threading.Thread(
            target=self._rebuild_worker,
            name="job-index-rebuild",
            daemon=True,
        ).start()

    def _rebuild_worker(self) -> None:
        try:
            with self._lock:
                if self._backend is not None:
                    return
                if self.db_path.exists():
                    stale = self.db_path.with_name(
                        f"{self.db_path.name}.corrupt-{int(time.time())}"
                    )
                    self.db_path.replace(stale)
                if not self._open_backend():
                    return
                entries = []
                for path in sorted(self.state_dir.glob("*.json")):
                    entry = build_entry(path)
                    if entry is not None:
                        entries.append(entry)
                assert self._backend is not None
                self._backend.rebuild(entries)
                self._last_scan = time.monotonic()
                self._status = STATUS_READY
                self._last_error = None
        except Exception as exc:
            self._status = STATUS_FALLBACK
            self._last_error = f"{type(exc).__name__}: {exc}"
        finally:
            self._rebuild_scheduled = False

    def status(self) -> str:
        with self._lock:
            return self._status

    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def refresh(self, *, force: bool = False) -> str:
        with self._lock:
            if not self.enabled:
                self._status = STATUS_FALLBACK
                return self._status
            if self._backend is None:
                self._status = STATUS_FALLBACK
                self._schedule_rebuild()
                return self._status
            now = time.monotonic()
            if not force and now - self._last_scan < self.debounce_seconds:
                return self._status
            self._last_scan = now
            try:
                self._scan_incremental()
                self._status = STATUS_READY
                self._last_error = None
            except Exception as exc:
                self._status = STATUS_FALLBACK
                self._last_error = f"{type(exc).__name__}: {exc}"
            return self._status

    def _scan_incremental(self) -> None:
        known = self._backend.fingerprints()
        seen: set[str] = set()
        for path in sorted(self.state_dir.glob("*.json")):
            job_id = path.stem
            seen.add(job_id)
            entry = build_entry(path)
            if entry is None:
                self._backend.delete(job_id)
                continue
            previous = known.get(job_id)
            if (
                previous is not None
                and previous[0] == entry.revision
                and abs(previous[1] - entry.file_mtime) < 1e-6
            ):
                continue
            self._backend.upsert(entry)
        for job_id in set(known) - seen:
            self._backend.delete(job_id)

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
        if self._backend is None:
            raise RuntimeError("index backend unavailable")
        return self._backend.query(
            q=q,
            platform=platform,
            media_type=media_type,
            sort=sort,
            cursor=cursor,
            limit=limit,
            exists_only=exists_only,
        )

    def rebuild(self) -> int:
        with self._lock:
            if self._backend is None and not self._open_backend():
                self._status = STATUS_FALLBACK
                return 0
            self._status = STATUS_REBUILDING
            started = time.monotonic()
            entries = []
            for path in sorted(self.state_dir.glob("*.json")):
                entry = build_entry(path)
                if entry is not None:
                    entries.append(entry)
            assert self._backend is not None
            count = self._backend.rebuild(entries)
            self._last_rebuild_seconds = time.monotonic() - started
            self._last_scan = time.monotonic()
            self._status = STATUS_READY
            self._last_error = None
            return count

    def close(self) -> None:
        with self._lock:
            if self._backend is not None:
                self._backend.close()
                self._backend = None