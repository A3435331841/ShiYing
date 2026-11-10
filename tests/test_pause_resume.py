"""Pause during an active download must not skip the in-flight item (F03)."""
import threading
import time

from fastapi.testclient import TestClient


def _isolate(monkeypatch, tmp_path):
    from app import main
    from app.storage import JsonJobStore

    monkeypatch.setattr(main, "config", main.config.model_copy(update={"download_dir": str(tmp_path)}))
    monkeypatch.setattr(main.manager, "store", JsonJobStore(tmp_path / "state"))


def _engine_pausing_midway(monkeypatch, downloaded):
    """part-1 的首次下载会等到取消信号后抛 DownloadCancelledError；再次调用则成功。"""
    from app.downloader import DiscoveryResult, DownloadOutcome, MediaDownloader
    from app.errors import DownloadCancelledError
    from app.models import DownloadItem, MediaType

    def fake_discover(self, url, platform, kind, should_cancel=lambda: False):
        items = [
            DownloadItem(id="part-1", media_id="m1", source_url=url, title="p1", author="a", media_type=MediaType.VIDEO),
            DownloadItem(id="part-2", media_id="m2", source_url=url, title="p2", author="a", media_type=MediaType.VIDEO),
        ]
        return DiscoveryResult(author="a", items=items)

    calls = {"part-1": 0}

    def fake_download_item(self, item, platform, output_dir, *, callback=None, should_cancel=None):
        if item.id == "part-1":
            calls["part-1"] += 1
            if calls["part-1"] == 1:
                while not (should_cancel and should_cancel()):
                    time.sleep(0.01)
                raise DownloadCancelledError("Task cancelled")
        downloaded.append(item.id)
        target = output_dir / f"{item.id}.mp4"
        target.write_bytes(b"fake")
        return DownloadOutcome(output_paths=[str(target)], title=item.title, author=item.author, media_type=MediaType.VIDEO)

    monkeypatch.setattr(MediaDownloader, "discover", fake_discover)
    monkeypatch.setattr(MediaDownloader, "download_item", fake_download_item)


def _wait_status(client, job_id, statuses, timeout=15):
    deadline = time.time() + timeout
    while True:
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] in statuses:
            return payload
        assert time.time() < deadline, f"任务未在期限内到达 {statuses}，当前 {payload['status']}"
        time.sleep(0.05)


def test_pause_then_resume_completes_current_item(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _engine_pausing_midway(monkeypatch, downloaded := [])
    from app import main

    client = TestClient(main.app, base_url="http://localhost")
    created = client.post(
        "/api/jobs",
        json={"url": "https://www.bilibili.com/video/BV1GJ411x7h7", "auto_start": False},
    )
    assert created.status_code == 201, created.text
    job = created.json()
    started = client.post(f"/api/jobs/{job['id']}/start", json={})
    assert started.status_code == 200, started.text

    # 等当前项进入下载，再请求暂停。
    deadline = time.time() + 10
    while True:
        snapshot = client.get(f"/api/jobs/{job['id']}").json()
        if snapshot.get("active_item_id") == "part-1":
            break
        assert time.time() < deadline, "part-1 未开始下载"
        time.sleep(0.05)
    paused = client.post(f"/api/jobs/{job['id']}/pause")
    assert paused.status_code == 200, paused.text
    payload = _wait_status(client, job["id"], {"paused"})
    statuses = {item["id"]: item["status"] for item in payload["items"]}
    assert statuses["part-1"] == "paused", "暂停时当前项必须保持可恢复，而不是 cancelled"

    # 继续后当前项重新下载并完成，任务整体完成而不是 partial。
    resumed = client.post(f"/api/jobs/{job['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    payload = _wait_status(client, job["id"], {"completed", "partial", "failed", "cancelled"})
    assert payload["status"] == "completed"
    assert downloaded == ["part-1", "part-2"], "继续后必须重新下载被暂停的当前项"
