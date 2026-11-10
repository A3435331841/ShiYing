"""Selection scope must survive job creation and discovery (F02)."""
import time

from fastapi.testclient import TestClient


def _isolate(monkeypatch, tmp_path):
    from app import main
    from app.storage import JsonJobStore

    monkeypatch.setattr(main, "config", main.config.model_copy(update={"download_dir": str(tmp_path)}))
    monkeypatch.setattr(main.manager, "store", JsonJobStore(tmp_path / "state"))


def _fake_engine(monkeypatch, downloaded):
    from app.downloader import DiscoveryResult, DownloadOutcome, MediaDownloader
    from app.models import DownloadItem, MediaType

    def fake_discover(self, url, platform, kind, should_cancel=lambda: False):
        items = [
            DownloadItem(id="part-1", media_id="m1", source_url=url, title="p1", author="a", media_type=MediaType.VIDEO),
            DownloadItem(id="part-2", media_id="m2", source_url=url, title="p2", author="a", media_type=MediaType.VIDEO),
        ]
        return DiscoveryResult(author="a", items=items)

    def fake_download_item(self, item, platform, output_dir, *, callback=None, should_cancel=None):
        downloaded.append(item.id)
        target = output_dir / f"{item.id}.mp4"
        target.write_bytes(b"fake")
        return DownloadOutcome(output_paths=[str(target)], title=item.title, author=item.author, media_type=MediaType.VIDEO)

    monkeypatch.setattr(MediaDownloader, "discover", fake_discover)
    monkeypatch.setattr(MediaDownloader, "download_item", fake_download_item)


def _wait_terminal(client, job_id, timeout=15):
    deadline = time.time() + timeout
    while True:
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] in {"completed", "partial", "failed", "cancelled"}:
            return payload
        assert time.time() < deadline, "任务未在期限内结束"
        time.sleep(0.05)


def test_selection_only_downloads_selected_and_counts_scope(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _fake_engine(monkeypatch, downloaded := [])
    client = TestClient(__import__("app").main.app, base_url="http://localhost")

    created = client.post(
        "/api/jobs",
        json={"url": "https://www.bilibili.com/video/BV1GJ411x7h7", "auto_start": False},
    )
    assert created.status_code == 201, created.text
    job = created.json()
    started = client.post(f"/api/jobs/{job['id']}/start", json={"item_ids": ["part-1"]})
    assert started.status_code == 200, started.text

    payload = _wait_terminal(client, job["id"])
    assert payload["status"] == "completed"
    assert downloaded == ["part-1"], "只应下载用户选中的那一个项目"
    statuses = {item["id"]: item["status"] for item in payload["items"]}
    assert statuses["part-1"] == "completed"
    assert statuses["part-2"] == "skipped"
    # 未选择 ≠ 未完成：计数按所选范围，显示“所选 1/1 已完成”。
    assert payload["total_items"] == 1
    assert payload["completed_items"] == 1


def test_selection_not_matching_discovery_fails_without_expanding(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _fake_engine(monkeypatch, downloaded := [])
    client = TestClient(__import__("app").main.app, base_url="http://localhost")

    created = client.post(
        "/api/jobs",
        json={"url": "https://www.bilibili.com/video/BV1GJ411x7h7", "auto_start": False},
    )
    assert created.status_code == 201, created.text
    job = created.json()
    started = client.post(f"/api/jobs/{job['id']}/start", json={"item_ids": ["missing-id"]})
    assert started.status_code == 200, started.text

    payload = _wait_terminal(client, job["id"])
    assert payload["status"] == "failed"
    assert "所选内容与本次解析结果不一致" in (payload["error"] or "")
    assert downloaded == [], "选集完全落空时不得扩大下载范围"


def test_empty_item_ids_rejected_with_422_without_mutating_items(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _fake_engine(monkeypatch, downloaded := [])
    client = TestClient(__import__("app").main.app, base_url="http://localhost")

    created = client.post(
        "/api/jobs",
        json={"url": "https://www.bilibili.com/video/BV1GJ411x7h7", "auto_start": False},
    )
    assert created.status_code == 201
    job = created.json()

    # Empty item_ids list must be rejected at input validation (422), not mutating items to SKIPPED
    started = client.post(f"/api/jobs/{job['id']}/start", json={"item_ids": []})
    assert started.status_code == 422

    # Verify items are not mutated to skipped
    current_job = client.get(f"/api/jobs/{job['id']}").json()
    assert all(item["status"] != "skipped" for item in current_job["items"])


def test_start_without_item_ids_preserves_existing_selection_scope(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _fake_engine(monkeypatch, downloaded := [])
    client = TestClient(__import__("app").main.app, base_url="http://localhost")

    created = client.post(
        "/api/jobs",
        json={"url": "https://www.bilibili.com/video/BV1GJ411x7h7", "auto_start": False, "item_ids": ["part-1"]},
    )
    assert created.status_code == 201
    job_id = created.json()["id"]

    # Start without specifying item_ids; it should preserve the previously selected ["part-1"]
    started = client.post(f"/api/jobs/{job_id}/start", json={})
    assert started.status_code == 200

    payload = _wait_terminal(client, job_id)
    assert payload["status"] == "completed"
    assert payload["selected_item_ids"] == ["part-1"]
    assert downloaded == ["part-1"]


def test_start_on_paused_job_resumes_paused_items(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _fake_engine(monkeypatch, downloaded := [])
    from app import main
    from app.models import DownloadItem, ItemStatus, JobStatus, MediaType

    job = main.manager.create_job("https://www.bilibili.com/video/BV1GJ411x7h7", auto_start=False)
    # Manually populate items with one PAUSED item and mark job PAUSED
    with main.manager._lock:
        live_job = main.manager._require_job(job.id)
        live_job.items = [
            DownloadItem(id="p1", media_id="m1", source_url=job.source_url, title="t1", author="a", media_type=MediaType.VIDEO, status=ItemStatus.PAUSED),
            DownloadItem(id="p2", media_id="m2", source_url=job.source_url, title="t2", author="a", media_type=MediaType.VIDEO, status=ItemStatus.COMPLETED),
        ]
        live_job.status = JobStatus.PAUSED
        main.manager._commit_locked(live_job)

    client = TestClient(main.app, base_url="http://localhost")
    # Calling start without target_item_ids on a paused job should resume paused item p1
    res = client.post(f"/api/jobs/{job.id}/start", json={})
    assert res.status_code == 200

    payload = _wait_terminal(client, job.id)
    assert payload["status"] == "completed"
    assert downloaded == ["p1"]

