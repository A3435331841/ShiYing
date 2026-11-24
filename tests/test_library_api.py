from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import main as main_module
from app.index.backend import InMemoryIndexBackend
from app.index.indexer import JobIndexer
from app.models import (
    DownloadItem,
    DownloadJob,
    ItemStatus,
    JobStatus,
    Platform,
    SourceKind,
)


def _job(tmp_path: Path, job_id: str, status: JobStatus, *, with_file: bool) -> DownloadJob:
    media = tmp_path / f"{job_id}.mp4"
    if with_file:
        media.write_bytes(b"data")
    return DownloadJob(
        id=job_id,
        source_url=f"https://www.bilibili.com/video/{job_id}",
        platform=Platform.BILIBILI,
        source_kind=SourceKind.ITEM,
        output_root=str(tmp_path),
        status=status,
        items=[
            DownloadItem(
                id=f"{job_id}-item",
                source_url=f"https://www.bilibili.com/video/{job_id}",
                title=f"作品-{job_id}",
                media_type="video",
                status=ItemStatus.COMPLETED,
                output_paths=[str(media)] if with_file else [],
            )
        ],
        total_items=1,
        completed_items=1,
    )


def _indexer(tmp_path: Path, enabled: bool = True) -> JobIndexer:
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    return JobIndexer(
        state,
        tmp_path / "jobs.sqlite",
        enabled=enabled,
        debounce_seconds=0,
        backend=InMemoryIndexBackend(),
    )


def test_library_view_filters_dead_files_and_keeps_v1(monkeypatch, tmp_path: Path) -> None:
    jobs = [
        _job(tmp_path, "job-live", JobStatus.COMPLETED, with_file=True),
        _job(tmp_path, "job-dead", JobStatus.PARTIAL, with_file=False),
        _job(tmp_path, "job-active", JobStatus.DOWNLOADING, with_file=True),
    ]
    indexer = _indexer(tmp_path)
    for job in jobs:
        (tmp_path / "state" / f"{job.id}.json").write_text(
            job.model_dump_json(), encoding="utf-8"
        )
    monkeypatch.setattr(main_module, "indexer", indexer)
    client = TestClient(main_module.app, base_url="http://localhost")

    response = client.get("/api/jobs?view=library")
    assert response.status_code == 200
    page = response.json()
    assert page["index_status"] == "ready"
    assert [item["id"] for item in page["items"]] == ["job-live"]
    assert page["total_estimate"] == 1

    legacy = client.get("/api/jobs")
    assert legacy.status_code == 200
    assert isinstance(legacy.json(), list)


def test_library_page_filters_dead_files_before_small_pages(monkeypatch, tmp_path: Path) -> None:
    jobs = [
        _job(tmp_path, "job-dead-new", JobStatus.PARTIAL, with_file=False),
        _job(tmp_path, "job-live", JobStatus.COMPLETED, with_file=True),
        _job(tmp_path, "job-dead-old", JobStatus.PARTIAL, with_file=False),
    ]
    for job in jobs:
        job.created_at = job.created_at.replace(year=2026, month=11, day=24)
    indexer = _indexer(tmp_path)
    for job in jobs:
        (tmp_path / "state" / f"{job.id}.json").write_text(job.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(main_module, "indexer", indexer)
    client = TestClient(main_module.app, base_url="http://localhost")
    page = client.get("/api/jobs?view=library&limit=1&sort=created_asc").json()
    assert page["total_estimate"] == 1
    assert [item["id"] for item in page["items"]] == ["job-live"]
    assert page["next_cursor"] is None

    monkeypatch.setattr(main_module, "indexer", _indexer(tmp_path, enabled=False))
    monkeypatch.setattr(main_module.manager, "list_jobs", lambda: jobs)
    page = client.get("/api/jobs?view=library&limit=1&sort=created_asc").json()
    assert page["index_status"] == "fallback"
    assert page["total_estimate"] == 1
    assert [item["id"] for item in page["items"]] == ["job-live"]
    assert page["next_cursor"] is None


def test_library_view_rejects_invalid_limit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(main_module, "indexer", _indexer(tmp_path))
    client = TestClient(main_module.app, base_url="http://localhost")
    response = client.get("/api/jobs?view=library&limit=101")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_input"


def test_library_view_falls_back_when_disabled(monkeypatch, tmp_path: Path) -> None:
    job = _job(tmp_path, "job-fallback", JobStatus.COMPLETED, with_file=True)
    monkeypatch.setattr(main_module, "indexer", _indexer(tmp_path, enabled=False))
    monkeypatch.setattr(main_module.manager, "list_jobs", lambda: [job])
    client = TestClient(main_module.app, base_url="http://localhost")
    page = client.get("/api/jobs?view=library").json()
    assert page["index_status"] == "fallback"
    assert [item["id"] for item in page["items"]] == ["job-fallback"]