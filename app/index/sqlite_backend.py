"""SQLite/FTS5 派生索引实现；损坏或版本不符时整表重建。"""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
from pathlib import Path
from typing import Sequence

from .backend import IndexedJob, platform_keys_for_query
from .schema import ensure_schema

LIBRARY_STATUSES = ("completed", "partial")
SORT_CREATED_DESC = "created_desc"
SORT_CREATED_ASC = "created_asc"
SUPPORTED_SORTS = frozenset({SORT_CREATED_DESC, SORT_CREATED_ASC})

_COLUMNS = (
    "job_id",
    "revision",
    "file_mtime",
    "status",
    "platform",
    "media_type",
    "title",
    "author",
    "source_url",
    "created_at",
    "output_dir",
    "total_items",
    "completed_items",
    "failed_items",
    "file_paths",
    "total_bytes",
)


def encode_cursor(created_at: str, job_id: str) -> str:
    raw = f"{created_at}\t{job_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> tuple[str, str] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        created_at, job_id = raw.split("\t", 1)
        return created_at, job_id
    except (ValueError, UnicodeDecodeError):
        return None


def _values(entry: IndexedJob) -> tuple[object, ...]:
    return (
        entry.job_id,
        entry.revision,
        entry.file_mtime,
        entry.status,
        entry.platform,
        entry.media_type,
        entry.title,
        entry.author,
        entry.source_url,
        entry.created_at,
        entry.output_dir,
        entry.total_items,
        entry.completed_items,
        entry.failed_items,
        json.dumps(list(entry.file_paths), ensure_ascii=False),
        entry.total_bytes,
    )


def _row_to_entry(row: sqlite3.Row) -> IndexedJob:
    try:
        paths = tuple(json.loads(row["file_paths"] or "[]"))
    except (TypeError, ValueError):
        paths = ()
    return IndexedJob(
        job_id=row["job_id"],
        revision=int(row["revision"] or 0),
        file_mtime=float(row["file_mtime"] or 0),
        status=row["status"],
        platform=row["platform"],
        media_type=row["media_type"],
        title=row["title"],
        author=row["author"],
        source_url=row["source_url"],
        created_at=row["created_at"],
        output_dir=row["output_dir"],
        total_items=int(row["total_items"] or 0),
        completed_items=int(row["completed_items"] or 0),
        failed_items=int(row["failed_items"] or 0),
        file_paths=paths,
        total_bytes=int(row["total_bytes"] or 0),
    )


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _has_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _has_existing_file(raw_paths: str) -> int:
    try:
        paths = json.loads(raw_paths or "[]")
        if not isinstance(paths, list):
            return 0
        return int(any(isinstance(path, str) and Path(path).is_file() for path in paths))
    except (OSError, TypeError, ValueError):
        return 0


class SQLiteIndexBackend:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        try:
            self._conn.row_factory = sqlite3.Row
            self._conn.create_function("shiying_has_existing_file", 1, _has_existing_file)
            ensure_schema(self._conn)
        except Exception:
            self._conn.close()
            raise

    def upsert(self, entry: IndexedJob) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT rowid, title, author, source_url FROM jobs_index WHERE job_id = ?",
                (entry.job_id,),
            ).fetchone()
            if row is None:
                placeholders = ", ".join("?" for _ in _COLUMNS)
                cursor = self._conn.execute(
                    f"INSERT INTO jobs_index({', '.join(_COLUMNS)}) VALUES({placeholders})",
                    _values(entry),
                )
                rowid = cursor.lastrowid
            else:
                rowid = int(row["rowid"])
                assignments = ", ".join(f"{name} = ?" for name in _COLUMNS)
                self._conn.execute(
                    f"UPDATE jobs_index SET {assignments} WHERE rowid = ?",
                    (*_values(entry), rowid),
                )
                self._conn.execute(
                    "INSERT INTO jobs_fts(jobs_fts, rowid, title, author, source_url) "
                    "VALUES('delete', ?, ?, ?, ?)",
                    (rowid, row["title"], row["author"], row["source_url"]),
                )
            self._conn.execute(
                "INSERT INTO jobs_fts(rowid, title, author, source_url) VALUES(?, ?, ?, ?)",
                (rowid, entry.title, entry.author, entry.source_url),
            )
            self._conn.commit()

    def delete(self, job_id: str) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT rowid, title, author, source_url FROM jobs_index WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return
            self._conn.execute(
                "INSERT INTO jobs_fts(jobs_fts, rowid, title, author, source_url) "
                "VALUES('delete', ?, ?, ?, ?)",
                (row["rowid"], row["title"], row["author"], row["source_url"]),
            )
            self._conn.execute("DELETE FROM jobs_index WHERE rowid = ?", (row["rowid"],))
            self._conn.commit()

    def fingerprints(self) -> dict[str, tuple[int, float]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT job_id, revision, file_mtime FROM jobs_index"
            ).fetchall()
        return {
            row["job_id"]: (int(row["revision"] or 0), float(row["file_mtime"] or 0))
            for row in rows
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
        if sort not in SUPPORTED_SORTS:
            raise ValueError(f"unsupported sort: {sort}")
        with self._lock:
            keyword = q.strip()
            if not keyword or _has_cjk(keyword):
                return self._query_like(keyword, platform, media_type, sort, cursor, limit, exists_only)
            try:
                result = self._query_fts(keyword, platform, media_type, sort, cursor, limit, exists_only)
            except sqlite3.OperationalError:
                return self._query_like(keyword, platform, media_type, sort, cursor, limit, exists_only)
            if result[2] == 0:
                return self._query_like(keyword, platform, media_type, sort, cursor, limit, exists_only)
            return result

    def _query_fts(
        self,
        q: str,
        platform: str | None,
        media_type: str | None,
        sort: str,
        cursor: str | None,
        limit: int,
        exists_only: bool,
    ) -> tuple[list[IndexedJob], str | None, int]:
        where = [
            "j.status IN (?, ?)",
            "j.rowid IN (SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH ?)",
        ]
        params: list[object] = [*LIBRARY_STATUSES, q]
        where, params = self._base_filters(where, params, platform, media_type, exists_only)
        return self._execute_page(where, params, sort, cursor, limit)

    def _query_like(
        self,
        q: str,
        platform: str | None,
        media_type: str | None,
        sort: str,
        cursor: str | None,
        limit: int,
        exists_only: bool,
    ) -> tuple[list[IndexedJob], str | None, int]:
        where = ["j.status IN (?, ?)"]
        params: list[object] = list(LIBRARY_STATUSES)
        if q:
            pattern = _like_pattern(q)
            terms = [
                "j.title LIKE ? ESCAPE '\\'",
                "j.author LIKE ? ESCAPE '\\'",
                "j.source_url LIKE ? ESCAPE '\\'",
            ]
            params.extend([pattern] * 3)
            # 前端本地搜索还匹配平台中文名（task-views.js）；下推到后端时补上
            # 平台键条件，否则「抖音」这类中文词在资料库里搜不到。
            platform_keys = platform_keys_for_query(q)
            if platform_keys:
                terms.append(f"j.platform IN ({','.join('?' for _ in platform_keys)})")
                params.extend(platform_keys)
            where.append("(" + " OR ".join(terms) + ")")
        where, params = self._base_filters(where, params, platform, media_type, exists_only)
        return self._execute_page(where, params, sort, cursor, limit)

    def _base_filters(
        self,
        where: list[str],
        params: list[object],
        platform: str | None,
        media_type: str | None,
        exists_only: bool,
    ) -> tuple[list[str], list[object]]:
        if platform:
            where.append("j.platform = ?")
            params.append(platform)
        if media_type:
            where.append("j.media_type = ?")
            params.append(media_type)
        if exists_only:
            where.append("shiying_has_existing_file(j.file_paths) = 1")
        return where, params

    def _execute_page(
        self,
        where: list[str],
        params: list[object],
        sort: str,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[IndexedJob], str | None, int]:
        count_sql = f"SELECT COUNT(*) FROM jobs_index j WHERE {' AND '.join(where)}"
        total = int(self._conn.execute(count_sql, params).fetchone()[0])

        page_where = list(where)
        page_params = list(params)
        if cursor:
            decoded = decode_cursor(cursor)
            if decoded is None:
                return [], None, total
            created_at, job_id = decoded
            if sort == SORT_CREATED_ASC:
                page_where.append(
                    "(j.created_at > ? OR (j.created_at = ? AND j.job_id > ?))"
                )
            else:
                page_where.append(
                    "(j.created_at < ? OR (j.created_at = ? AND j.job_id < ?))"
                )
            page_params.extend([created_at, created_at, job_id])

        direction = "ASC" if sort == SORT_CREATED_ASC else "DESC"
        sql = (
            f"SELECT j.* FROM jobs_index j WHERE {' AND '.join(page_where)} "
            f"ORDER BY j.created_at {direction}, j.job_id {direction} LIMIT ?"
        )
        rows = self._conn.execute(sql, [*page_params, limit + 1]).fetchall()
        has_more = len(rows) > limit
        page = [_row_to_entry(row) for row in rows[:limit]]
        next_cursor = (
            encode_cursor(page[-1].created_at, page[-1].job_id)
            if has_more and page
            else None
        )
        return page, next_cursor, total

    def rebuild(self, entries: Sequence[IndexedJob]) -> int:
        with self._lock:
            self._conn.execute("DELETE FROM jobs_fts")
            self._conn.execute("DELETE FROM jobs_index")
            placeholders = ", ".join("?" for _ in _COLUMNS)
            self._conn.executemany(
                f"INSERT INTO jobs_index({', '.join(_COLUMNS)}) VALUES({placeholders})",
                [_values(entry) for entry in entries],
            )
            self._conn.execute(
                "INSERT INTO jobs_fts(rowid, title, author, source_url) "
                "SELECT rowid, title, author, source_url FROM jobs_index"
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('rebuilt_at', ?)",
                (str(len(entries)),),
            )
            self._conn.commit()
        return len(entries)

    def close(self) -> None:
        with self._lock:
            self._conn.close()