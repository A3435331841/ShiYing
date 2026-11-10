import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, wechat
from app.models import DownloadItem, DownloadJob, ItemStatus, JobStatus, Platform, SourceKind
from app.storage import JobNotFoundError
from app.task_manager import DownloadManager, JobBusyError


def _job(tmp_path, status):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"user media")
    return DownloadJob(
        id="record", source_url="https://www.bilibili.com/video/BV1xx411c7mD",
        platform=Platform.BILIBILI, source_kind=SourceKind.ITEM, output_root=str(tmp_path),
        status=status, items=[DownloadItem(id="item", source_url="https://www.bilibili.com/video/BV1xx411c7mD",
                                          status=ItemStatus.COMPLETED, output_paths=[str(media)])],
    ), media


def test_delete_completed_record_preserves_download_and_survives_restart(tmp_path):
    manager = DownloadManager(state_dir=tmp_path / "state", default_output_root=tmp_path)
    job, media = _job(tmp_path, JobStatus.COMPLETED)
    manager._jobs[job.id] = job
    manager.store.save(job)
    try:
        manager.delete_job_record(job.id)
        assert media.read_bytes() == b"user media"
        with pytest.raises(JobNotFoundError):
            manager.get_job(job.id)
        assert not (tmp_path / "state" / "record.json").exists()
    finally:
        manager.shutdown()
    restored = DownloadManager(state_dir=tmp_path / "state", default_output_root=tmp_path)
    try:
        assert restored.list_jobs() == []
    finally:
        restored.shutdown()


def test_delete_refuses_active_and_does_not_remove_record_on_disk_failure(tmp_path, monkeypatch):
    manager = DownloadManager(state_dir=tmp_path / "state", default_output_root=tmp_path)
    job, media = _job(tmp_path, JobStatus.DOWNLOADING)
    manager._jobs[job.id] = job
    manager.store.save(job)
    try:
        with pytest.raises(JobBusyError):
            manager.delete_job_record(job.id)
        job.status = JobStatus.COMPLETED
        monkeypatch.setattr(manager.store, "delete", lambda _: (_ for _ in ()).throw(OSError("disk failure")))
        with pytest.raises(OSError):
            manager.delete_job_record(job.id)
        assert manager.get_job(job.id).id == job.id
        assert media.read_bytes() == b"user media"
    finally:
        manager.shutdown()


def test_wechat_delete_rejects_active_and_preserves_media(tmp_path, monkeypatch):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"user media")
    monkeypatch.setattr(wechat, "STATE", tmp_path / "jobs.json")
    monkeypatch.setattr(wechat, "JOBS", [{"id": "done", "status": "completed", "path": str(media)},
                                         {"id": "busy", "status": "downloading"}])
    monkeypatch.setattr(wechat, "CANCELLATIONS", {"busy": threading.Event()})
    client = TestClient(main.app, base_url="http://localhost")
    assert client.delete("/api/wechat/jobs/busy").status_code == 409
    assert client.delete("/api/wechat/jobs/done", headers={"origin": "https://other.example"}).status_code == 403
    assert client.delete("/api/wechat/jobs/done").json() == {"deleted": True}
    assert media.read_bytes() == b"user media"
    assert [job["id"] for job in wechat.list_jobs()] == ["busy"]


def test_thumbnail_cleanup_failure_does_not_fail_deletion(tmp_path, monkeypatch):
    """记录删除成功后，缩略图清理失败（如被杀软锁定）不应把删除报为失败——
    否则重试同一 job_id 会得到矛盾的 404。"""
    from fastapi.testclient import TestClient

    from app import main

    job = DownloadJob(
        id="thumb-stuck",
        source_url="https://www.bilibili.com/video/BV1xx411c7mD",
        platform=Platform.BILIBILI,
        source_kind=SourceKind.ITEM,
        output_root=str(tmp_path / "downloads"),
    )
    monkeypatch.setattr(
        main.manager, "delete_job_record", lambda job_id: job
    )

    def refusing_unlink(self, missing_ok=False):
        raise PermissionError("locked by antivirus")

    monkeypatch.setattr(Path, "unlink", refusing_unlink)
    client = TestClient(main.app, base_url="http://localhost")
    response = client.delete("/api/jobs/thumb-stuck")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}
