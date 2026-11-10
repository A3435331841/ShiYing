from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main
from app.models import DownloadJob, JobStatus, Platform, SourceKind


def _job(tmp_path: Path, **overrides) -> DownloadJob:
    data = dict(
        id="job-1",
        source_url="https://www.bilibili.com/video/BV1xx411c7mD",
        platform=Platform.BILIBILI,
        source_kind=SourceKind.ITEM,
        output_root=str(tmp_path / "downloads"),
    )
    data.update(overrides)
    return DownloadJob(**data)


def test_existing_job_lookup_matches_canonical_source(app_manager):
    job = _job(Path("."))
    app_manager._jobs[job.id] = job
    client = TestClient(main.app, base_url="http://localhost")

    found = client.get(
        "/api/jobs/exists",
        params={"url": "https://www.bilibili.com/video/BV1xx411c7mD?p=1"},
    ).json()
    assert found["exists"] is True
    assert found["job"]["id"] == "job-1"

    missing = client.get(
        "/api/jobs/exists",
        params={"url": "https://www.bilibili.com/video/BV1xx411c7mE"},
    ).json()
    assert missing == {"exists": False, "job": None}


def test_preview_rejects_profiles_with_actionable_message(app_manager):
    client = TestClient(main.app, base_url="http://localhost")
    response = client.post(
        "/api/preview",
        json={"url": "https://www.xiaohongshu.com/user/profile/5c99d4b30000000011015e6d"},
    )
    assert response.status_code == 422
    assert "解析并下载" in response.json()["detail"]


def test_cache_endpoints_report_and_clear(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    (cache / "job-a").mkdir(parents=True)
    (cache / "job-a" / "part.bin").write_bytes(b"x" * 2048)
    (cache / "stray.bin").write_bytes(b"y" * 1024)
    monkeypatch.setattr(main, "CACHE_DIR", cache)
    monkeypatch.setattr(main.manager, "list_jobs", lambda: [])
    client = TestClient(main.app, base_url="http://localhost")

    status = client.get("/api/cache").json()
    assert status["can_clear"] is True
    assert status["bytes"] == 3072
    assert status["files"] == 2

    cleared = client.post("/api/cache/clear").json()
    assert cleared["freed_bytes"] == 3072
    assert list(cache.iterdir()) == []


def test_cache_clear_refuses_while_a_job_is_downloading(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        main.manager,
        "list_jobs",
        lambda: [_job(tmp_path, status=JobStatus.DOWNLOADING)],
    )
    client = TestClient(main.app, base_url="http://localhost")

    assert client.get("/api/cache").json()["can_clear"] is False
    response = client.post("/api/cache/clear")
    assert response.status_code == 409
    assert "先完成或取消" in response.json()["detail"]


def test_cache_clear_warns_about_paused_jobs_but_allows_it(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "resume.bin").write_bytes(b"z" * 64)
    monkeypatch.setattr(main, "CACHE_DIR", cache)
    monkeypatch.setattr(
        main.manager,
        "list_jobs",
        lambda: [_job(tmp_path, status=JobStatus.PAUSED)],
    )
    client = TestClient(main.app, base_url="http://localhost")

    status = client.get("/api/cache").json()
    assert status["can_clear"] is True
    assert status["paused_jobs"] == 1
    assert client.post("/api/cache/clear").json()["freed_bytes"] == 64


def test_legacy_wechat_creation_is_retired(app_manager):
    client = TestClient(main.app, base_url="http://localhost")
    response = client.post(
        "/api/wechat/jobs",
        json={"url": "https://weixin.qq.com/sph/example"},
        headers={"origin": "http://localhost"},
    )
    assert response.status_code == 410
    assert "首页" in response.json()["detail"]


def test_existing_job_lookup_normalizes_host_and_youtu_be(app_manager):
    """www/非 www 与 youtu.be 短链必须与规范形态判为同一作品，
    否则重复检测静默放行第二个任务。"""
    job = _job(
        Path("."),
        source_url="https://xiaohongshu.com/explore/6411cf99000000001300b6d9",
        platform=Platform.XIAOHONGSHU,
    )
    app_manager._jobs[job.id] = job
    client = TestClient(main.app, base_url="http://localhost")

    found = client.get(
        "/api/jobs/exists",
        params={"url": "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9"},
    ).json()
    assert found["exists"] is True

    yt_job = _job(
        Path("."),
        source_url="https://www.youtube.com/watch?v=abcdefghijk",
        platform=Platform.YOUTUBE,
    )
    app_manager._jobs[yt_job.id] = yt_job
    short = client.get(
        "/api/jobs/exists",
        params={"url": "https://youtu.be/abcdefghijk"},
    ).json()
    assert short["exists"] is True
