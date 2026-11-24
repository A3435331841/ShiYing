from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from app.index.backend import InMemoryIndexBackend, IndexedJob
from app.index.indexer import JobIndexer, build_entry, filter_entries
from app.index.sqlite_backend import SQLiteIndexBackend


def _write_job(state_dir: Path, job_id: str, **overrides) -> Path:
    file_paths = overrides.pop("paths", ())
    title = overrides.pop("title", f"标题-{job_id}")
    payload = {
        "id": job_id,
        "source_url": f"https://example.com/{job_id}",
        "platform": "bilibili",
        "source_kind": "item",
        "output_root": str(state_dir),
        "status": "completed",
        "revision": 1,
        "created_at": "2026-11-23T10:00:00+00:00",
        "total_items": 1,
        "completed_items": 1,
        "failed_items": 0,
        "items": [
            {
                "id": "i1",
                "source_url": f"https://example.com/{job_id}/1",
                "title": title,
                "author": "作者",
                "media_type": "video",
                "output_paths": [str(path) for path in file_paths],
            }
        ],
    }
    payload.update(overrides)
    path = state_dir / f"{job_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _entry(job_id: str = "job-1", **overrides) -> IndexedJob:
    values = {
        "job_id": job_id,
        "revision": 1,
        "file_mtime": 1.0,
        "status": "completed",
        "platform": "bilibili",
        "media_type": "video",
        "title": "旅行视频",
        "author": "作者",
        "source_url": "https://example.com/1",
        "created_at": "2026-11-23T10:00:00+00:00",
        "total_items": 1,
        "completed_items": 1,
    }
    values.update(overrides)
    return IndexedJob(**values)


def test_sqlite_backend_schema_query_delete(tmp_path: Path) -> None:
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x" * 10)
    backend = SQLiteIndexBackend(tmp_path / "jobs.sqlite")
    backend.upsert(_entry(file_paths=(str(media),), total_bytes=10))
    items, cursor, total = backend.query(q="旅行")
    assert total == 1
    assert cursor is None
    assert items[0].job_id == "job-1"
    backend.delete("job-1")
    assert backend.query()[0] == []
    backend.close()


def test_schema_version_mismatch_rebuilds(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite"
    backend = SQLiteIndexBackend(path)
    backend.upsert(_entry())
    backend.close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    reopened = SQLiteIndexBackend(path)
    assert reopened.fingerprints() == {}
    reopened.close()


def test_indexer_incremental_and_rebuild(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    _write_job(state, "job-1", paths=[media])
    indexer = JobIndexer(state, tmp_path / "jobs.sqlite")
    assert indexer.refresh(force=True) == "ready"
    items, _, total = indexer.query()
    assert total == 1
    assert items[0].title == "标题-job-1"
    time.sleep(0.02)
    _write_job(state, "job-1", paths=[media], revision=2, title="新标题")
    assert indexer.refresh(force=True) == "ready"
    updated = indexer.query(q="新标题")[0]
    assert updated and updated[0].revision == 2
    assert indexer.rebuild() == 1
    indexer.close()


def test_corrupt_index_falls_back_then_recovers(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    _write_job(state, "job-1", paths=[media])
    db_path = tmp_path / "jobs.sqlite"
    db_path.write_bytes(b"not a sqlite database")
    indexer = JobIndexer(state, db_path)
    assert indexer.status() == "fallback"
    assert indexer.refresh() == "fallback"
    deadline = time.time() + 5
    while indexer.status() != "ready" and time.time() < deadline:
        time.sleep(0.05)
    assert indexer.status() == "ready"
    assert indexer.query()[2] == 1
    indexer.close()


def test_disabled_index_reports_fallback(tmp_path: Path) -> None:
    indexer = JobIndexer(
        tmp_path / "state",
        tmp_path / "jobs.sqlite",
        enabled=False,
        backend=InMemoryIndexBackend(),
    )
    assert indexer.refresh() == "fallback"


def test_filter_entries_pagination() -> None:
    entries = [
        IndexedJob(
            job_id=f"job-{index}",
            revision=1,
            file_mtime=0.0,
            status="completed",
            platform="bilibili",
            media_type="video",
            title=f"t{index}",
            created_at=f"2026-11-23T10:00:0{index}+00:00",
        )
        for index in range(3)
    ]
    page, cursor, total = filter_entries(entries, limit=2)
    assert total == 3
    assert len(page) == 2
    assert cursor
    second, second_cursor, _ = filter_entries(entries, limit=2, cursor=cursor)
    assert [entry.job_id for entry in second] == ["job-0"]
    assert second_cursor is None


def test_build_entry_skips_broken_json(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert build_entry(broken) is None


def test_indexer_detects_revision_change_with_same_mtime(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    path = _write_job(state, "job-1", paths=[media])
    indexer = JobIndexer(state, tmp_path / "jobs.sqlite")
    try:
        assert indexer.refresh(force=True) == "ready"
        original_stat = path.stat()
        _write_job(state, "job-1", paths=[media], revision=2, title="新标题")
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        assert indexer.refresh(force=True) == "ready"
        page, _, total = indexer.query(q="新标题")
        assert total == 1 and page[0].revision == 2
    finally:
        indexer.close()


def test_indexer_deletes_and_recovers_corrupt_rows(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    path = _write_job(state, "job-1", paths=[media])
    indexer = JobIndexer(state, tmp_path / "jobs.sqlite")
    try:
        assert indexer.refresh(force=True) == "ready"
        original_stat = path.stat()
        path.write_text("{", encoding="utf-8")
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        assert indexer.refresh(force=True) == "ready"
        assert indexer.query()[2] == 0
        _write_job(state, "job-1", paths=[media], title="恢复")
        assert indexer.refresh(force=True) == "ready"
        assert indexer.query(q="恢复")[2] == 1
    finally:
        indexer.close()


def test_exists_only_filter_precedes_pagination(tmp_path: Path) -> None:
    dead = tmp_path / "missing.mp4"
    entries = []
    for index in range(5):
        media = tmp_path / f"live-{index}.mp4"
        if index % 2:
            media.write_bytes(b"x")
            paths = (str(media),)
        else:
            paths = (str(dead),)
        entries.append(
            _entry(
                f"job-{index}",
                created_at=f"2026-11-23T10:00:0{index}+00:00",
                file_paths=paths,
            )
        )

    for backend in (InMemoryIndexBackend(), SQLiteIndexBackend(tmp_path / "index.sqlite")):
        try:
            backend.rebuild(entries)
            assert backend.query(exists_only=True)[2] == 2
            page, cursor, total = backend.query(exists_only=True, limit=1)
            assert total == 2 and len(page) == 1 and cursor
            second, cursor, total = backend.query(exists_only=True, limit=1, cursor=cursor)
            assert total == 2 and len(second) == 1 and cursor is None
            assert {entry.job_id for entry in page + second} == {entry.job_id for entry in entries if entry.file_paths[0] != str(dead)}
            assert backend.query(limit=100)[2] == 5
        finally:
            backend.close()


def test_library_query_matches_chinese_platform_label(tmp_path: Path) -> None:
    """资料库把搜索词下推后端，中文平台名必须与前端本地匹配口径一致。"""
    entry = _entry(
        "j1", platform="douyin", title="猫咪日常", source_url="https://www.douyin.com/video/123"
    )
    backend = SQLiteIndexBackend(tmp_path / "index.sqlite")
    try:
        backend.rebuild([entry])
        page, _, total = backend.query(q="抖音")
        assert total == 1
        assert [item.job_id for item in page] == ["j1"]
    finally:
        backend.close()

    memory = InMemoryIndexBackend()
    memory.rebuild([entry])
    assert [item.job_id for item in memory.query(q="抖音")[0]] == ["j1"]


def test_mixed_video_image_is_indexed_as_video_for_library_filter(tmp_path: Path) -> None:
    """前端把含视频的任务归为 video；索引必须同口径，否则资料库筛选漏项。"""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    path = _write_job(
        state_dir,
        "mixed-vi",
        items=[
            {"id": "i1", "source_url": "https://example.com/1", "media_type": "video", "output_paths": []},
            {"id": "i2", "source_url": "https://example.com/2", "media_type": "image", "output_paths": []},
        ],
    )
    entry = build_entry(path)
    assert entry is not None and entry.media_type == "video"

    backend = SQLiteIndexBackend(tmp_path / "index.sqlite")
    try:
        backend.rebuild([entry])
        page, _, total = backend.query(media_type="video")
        assert total == 1
        assert [item.job_id for item in page] == ["mixed-vi"]
    finally:
        backend.close()