from fastapi.testclient import TestClient

from app import main
from app.downloader import DiscoveryResult
from app.models import DownloadItem, ItemStatus, MediaType


def test_preview_endpoint_returns_structured_items(monkeypatch, app_manager):
    def fake_discover(*args, **kwargs):
        return DiscoveryResult(
            author="TestAuthor",
            items=[
                DownloadItem(
                    id="item-1",
                    media_id="111",
                    source_url="https://example.com/1",
                    title="Part 1",
                    author="TestAuthor",
                    media_type=MediaType.VIDEO,
                    playlist_index=1,
                ),
                DownloadItem(
                    id="item-2",
                    media_id="222",
                    source_url="https://example.com/2",
                    title="Part 2",
                    author="TestAuthor",
                    media_type=MediaType.VIDEO,
                    playlist_index=2,
                ),
            ],
            warning="Some warning",
        )

    monkeypatch.setattr("app.main.MediaDownloader.discover", fake_discover)
    client = TestClient(main.app, base_url="http://localhost")
    res = client.post("/api/preview", json={"url": "https://www.bilibili.com/video/BV1xx411c7mD"})
    assert res.status_code == 200
    data = res.json()
    assert data["author"] == "TestAuthor"
    assert data["total_items"] == 2
    assert data["items"][0]["title"] == "Part 1"
    assert data["warning"] == "Some warning"


def test_start_job_with_selected_items(app_manager, monkeypatch):
    def fake_discover(*args, **kwargs):
        return DiscoveryResult(
            author="TestAuthor",
            items=[
                DownloadItem(id="i1", media_id="1", source_url="https://example.com/1", title="1"),
                DownloadItem(id="i2", media_id="2", source_url="https://example.com/2", title="2"),
            ],
        )

    monkeypatch.setattr("app.main.MediaDownloader.discover", fake_discover)

    def fake_download_item(self, item, platform, output_dir, *, callback=None, should_cancel=None):
        target = output_dir / f"{item.id}.mp4"
        target.write_bytes(b"fake")
        from app.downloader import DownloadOutcome

        return DownloadOutcome(
            output_paths=[str(target)],
            title=item.title,
            author=item.author,
            media_type=MediaType.VIDEO,
        )

    monkeypatch.setattr("app.main.MediaDownloader.download_item", fake_download_item)
    client = TestClient(main.app, base_url="http://localhost")
    created = client.post(
        "/api/jobs",
        json={"url": "https://www.bilibili.com/video/BV1xx411c7mD", "auto_start": False},
    ).json()
    job_id = created["id"]
    job = app_manager.get_job(job_id)
    job.items = [
        DownloadItem(id="i1", media_id="1", source_url="https://example.com/1", title="1", status=ItemStatus.QUEUED),
        DownloadItem(id="i2", media_id="2", source_url="https://example.com/2", title="2", status=ItemStatus.QUEUED),
    ]
    app_manager._jobs[job_id] = job
    app_manager.store.save(job)

    started = client.post(f"/api/jobs/{job_id}/start", json={"item_ids": ["i1"]}).json()
    assert started["items"][0]["status"] in {"queued", "downloading"}
    assert started["items"][1]["status"] == "skipped"
