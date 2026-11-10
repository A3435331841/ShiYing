from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.bilibili_opus import is_trusted_bilibili_asset_url
from app.build_info import calculate_build_id
from app.downloader import (
    DiscoveryResult,
    DownloadOutcome,
    DownloaderConfig,
    MediaDownloader,
)
from app.errors import DownloadCancelledError, MediaDownloadError
from app.models import (
    DownloadItem,
    DownloadJob,
    ItemStatus,
    JobStatus,
    MediaType,
    Platform,
    SourceKind,
)
from app.task_manager import DownloadManager, ItemNotRetryableError
from app.xiaohongshu import RemoteAsset


def wait_for(manager: DownloadManager, job_id: str, statuses: set[JobStatus], timeout: float = 10.0) -> DownloadJob:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job.status in statuses:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} stayed in {manager.get_job(job_id).status}")


class PausableEngine:
    """Discovery with two items; the first transfer blocks until cancelled."""

    def __init__(self) -> None:
        self.block_first = True
        self.pause_seen = threading.Event()
        self.download_calls: list[str] = []
        self._lock = threading.Lock()

    def discover(self, url, platform, kind, *, should_cancel):
        return DiscoveryResult(
            author="Test Author",
            items=[
                DownloadItem(
                    id="part-a",
                    media_id="media-a",
                    source_url="https://www.bilibili.com/video/BV1xx411c7mD",
                    title="Part A",
                    media_type=MediaType.VIDEO,
                ),
                DownloadItem(
                    id="part-b",
                    media_id="media-b",
                    source_url="https://www.bilibili.com/video/BV1xx411c7mE",
                    title="Part B",
                    media_type=MediaType.VIDEO,
                ),
            ],
        )

    def download_item(self, item, platform, output_dir, *, callback, should_cancel):
        with self._lock:
            self.download_calls.append(item.id)
        if self.block_first and item.id == "part-a":
            self.pause_seen.set()
            while not should_cancel():
                time.sleep(0.01)
            raise DownloadCancelledError("Task cancelled")
        path = Path(output_dir) / f"{item.id}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"media")
        return DownloadOutcome(
            output_paths=[str(path)],
            title=item.title,
            author="Test Author",
            media_type=MediaType.VIDEO,
        )


@pytest.fixture
def manager(tmp_path):
    instance = DownloadManager(
        state_dir=tmp_path / "state",
        default_output_root=tmp_path / "downloads",
        max_workers=1,
    )
    try:
        yield instance
    finally:
        instance.shutdown()


def test_pause_keeps_the_in_flight_item_resumable(monkeypatch, manager):
    engine = PausableEngine()
    monkeypatch.setattr(manager, "_engine_for_job", lambda job: engine)
    job = manager.create_job(
        "https://www.bilibili.com/video/BV1xx411c7mD", auto_start=True
    )

    assert engine.pause_seen.wait(5), "the first transfer never started"
    manager.pause_job(job.id)
    paused = wait_for(manager, job.id, {JobStatus.PAUSED})

    assert paused.active_item_id is None
    assert paused.items[0].status == ItemStatus.PAUSED
    assert paused.items[1].status == ItemStatus.PAUSED

    engine.block_first = False
    manager.resume_job(job.id)
    finished = wait_for(manager, job.id, {JobStatus.COMPLETED, JobStatus.PARTIAL})

    # The paused file must be transferred again instead of being skipped.
    assert engine.download_calls[:2] == ["part-a", "part-a"]
    assert "part-b" in engine.download_calls
    assert finished.status == JobStatus.COMPLETED
    assert finished.completed_items == 2


def test_resume_retries_items_cancelled_by_an_older_pause(monkeypatch, manager, tmp_path):
    engine = PausableEngine()
    engine.block_first = False
    monkeypatch.setattr(manager, "_engine_for_job", lambda job: engine)
    job = DownloadJob(
        id="legacy-paused",
        source_url="https://www.bilibili.com/video/BV1xx411c7mD",
        platform=Platform.BILIBILI,
        source_kind=SourceKind.ITEM,
        output_root=str(tmp_path / "downloads"),
        status=JobStatus.PAUSED,
        items=[
            DownloadItem(
                id="part-a",
                source_url="https://www.bilibili.com/video/BV1xx411c7mD",
                status=ItemStatus.CANCELLED,
                error="Cancelled by user",
                retryable=True,
            )
        ],
    )
    manager._jobs[job.id] = job
    manager.store.save(job)

    manager.resume_job(job.id)
    wait_for(manager, job.id, {JobStatus.COMPLETED, JobStatus.PARTIAL, JobStatus.FAILED})

    assert engine.download_calls == ["part-a"]


def test_selection_is_applied_when_the_job_is_still_empty(monkeypatch, manager):
    engine = PausableEngine()
    engine.block_first = False
    monkeypatch.setattr(manager, "_engine_for_job", lambda job: engine)

    job = manager.create_job(
        "https://www.bilibili.com/video/BV1xx411c7mD", auto_start=False
    )
    assert job.items == []
    manager.start_job(job.id, target_item_ids=["part-a"])
    finished = wait_for(manager, job.id, {JobStatus.COMPLETED, JobStatus.PARTIAL})

    assert engine.download_calls == ["part-a"]
    assert finished.status == JobStatus.COMPLETED
    # Only the selected item counts; the unselected item is not "部分完成".
    assert finished.selected_item_ids == ["part-a"]
    assert (finished.total_items, finished.completed_items) == (1, 1)


def test_starting_with_an_empty_selection_is_rejected(manager):
    job = manager.create_job(
        "https://www.bilibili.com/video/BV1xx411c7mD", auto_start=False
    )
    with pytest.raises(ItemNotRetryableError):
        manager.start_job(job.id, target_item_ids=[])
    assert manager.get_job(job.id).status == JobStatus.QUEUED


def test_wechat_staging_names_are_isolated_per_job(monkeypatch, tmp_path):
    recorded: list[Path] = []

    def fake_download(url, key, output_path, cancel_event, progress_callback):
        recorded.append(Path(output_path))
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")

    monkeypatch.setattr("app.wechat.load_yuanbao_cookie", lambda: "cookie")
    monkeypatch.setattr("app.wechat.profile_yuanbao", lambda url, cookie: ({}, {}))
    monkeypatch.setattr("app.wechat.work_identity", lambda feed, url: "work-1")
    monkeypatch.setattr("app.wechat.media_selection", lambda feed: ("https://example.invalid/v.mp4", 0))
    monkeypatch.setattr("app.wechat.download_stream_and_decrypt", fake_download)
    monkeypatch.setattr("app.wechat.verify_video", lambda path: True)

    downloader = MediaDownloader(DownloaderConfig(cookie_browser=None))
    for index, job_id in enumerate(("job-one", "job-two")):
        item = DownloadItem(
            id="item-1",
            media_id="work-1",
            source_url="https://channels.weixin.qq.com/x",
            title="Clip",
            media_type=MediaType.VIDEO,
            metadata={"_job_id": job_id},
        )
        downloader._download_wechat_item(
            item,
            tmp_path / f"out-{index}",
            callback=None,
            should_cancel=lambda: False,
        )

    assert len(recorded) == 2
    assert recorded[0].name != recorded[1].name
    assert "job-one" in recorded[0].name and "job-two" in recorded[1].name
    for path in recorded:
        assert not path.exists(), "staging files must not be left behind"


def test_wechat_reports_progress_without_content_length(monkeypatch, tmp_path):
    chunks = [b"a" * 4, b"b" * 4, b"c" * 4]

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield from chunks

        def close(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("app.wechat.requests.get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr("app.wechat._trusted_media_url", lambda value: value)

    seen: list[tuple[int, int | None]] = []

    def on_progress(downloaded, total):
        seen.append((downloaded, total))

    from app.wechat import download_stream_and_decrypt

    target = tmp_path / "video.mp4"
    download_stream_and_decrypt("https://example.invalid/v.mp4", 0, target, threading.Event(), on_progress)

    assert target.exists()
    assert [item[0] for item in seen] == [4, 8, 12]
    assert all(item[1] is None for item in seen)
    assert not target.with_suffix(".part").exists()


def test_wechat_unknown_size_can_be_cancelled_early(monkeypatch, tmp_path):
    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            for _ in range(50):
                yield b"x" * 1024

        def close(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("app.wechat.requests.get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr("app.wechat._trusted_media_url", lambda value: value)

    cancel = threading.Event()
    calls = {"count": 0}

    def on_progress(downloaded, total):
        calls["count"] += 1
        if calls["count"] == 2:
            cancel.set()

    from app.wechat import download_stream_and_decrypt

    target = tmp_path / "video.mp4"
    with pytest.raises(ValueError):
        download_stream_and_decrypt("https://example.invalid/v.mp4", 0, target, cancel, on_progress)

    assert calls["count"] == 2
    assert not target.exists()
    assert not target.with_suffix(".part").exists()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://i0.hdslb.com/bfs/new_dyn/a.jpg", True),
        ("https://archive.biliimg.com/a.png", True),
        ("https://upos-sz.bilivideo.com/a.mp4", True),
        ("http://i0.hdslb.com/a.jpg", False),
        ("https://i0.hdslb.com:8443/a.jpg", False),
        ("https://user@i0.hdslb.com/a.jpg", False),
        ("https://i0.hdslb.com.evil.test/a.jpg", False),
        ("https://hdslb.com.evil.test/a.jpg", False),
        ("https://127.0.0.1/a.jpg", False),
    ],
)
def test_bilibili_asset_url_whitelist(value, expected):
    assert is_trusted_bilibili_asset_url(value) is expected


def test_bilibili_untrusted_candidate_is_blocked_before_any_request():
    class FakeYdl:
        called = False

        def urlopen(self, request):
            FakeYdl.called = True
            raise AssertionError("untrusted URLs must never be requested")

    asset = RemoteAsset(candidates=["http://127.0.0.1:9/a.jpg"], index=1)
    downloader = MediaDownloader(DownloaderConfig(cookie_browser=None))
    with pytest.raises(MediaDownloadError) as excinfo:
        downloader._download_first_available_asset(
            FakeYdl(),
            [asset],
            Path("."),
            None,
            "Title",
            "media-1",
            "https://www.bilibili.com/opus/1",
            platform=Platform.BILIBILI,
            media_type=MediaType.IMAGE,
            callback=None,
            should_cancel=lambda: False,
        )
    assert "Untrusted Bilibili media URL" in str(excinfo.value)
    assert FakeYdl.called is False


def test_corrupt_config_is_backed_up_and_reported(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(main, "CONFIG_RECOVERY", None)

    loaded = main._load_config()

    assert loaded.download_dir
    assert not config_path.exists(), "the corrupt file must be moved aside"
    backups = list(tmp_path.glob("config.corrupt-*.json"))
    assert len(backups) == 1
    assert main.CONFIG_RECOVERY["config_recovered"] is True
    assert str(backups[0]) == main.CONFIG_RECOVERY["config_backup"]
    assert main.CONFIG_RECOVERY["message"]


def test_health_exposes_recovery_warnings(monkeypatch):
    monkeypatch.setattr(
        main,
        "CONFIG_RECOVERY",
        {"config_recovered": True, "message": "配置文件无法读取", "config_backup": None},
    )
    client = TestClient(main.app, base_url="http://localhost")
    payload = client.get("/api/health").json()

    assert payload["recovery"]["config_recovered"] is True
    assert payload["recovery"]["message"] == "配置文件无法读取"


def test_health_has_no_recovery_block_when_nothing_is_wrong(monkeypatch):
    monkeypatch.setattr(main, "CONFIG_RECOVERY", None)
    monkeypatch.setattr(main.manager, "load_warnings", [])
    client = TestClient(main.app, base_url="http://localhost")
    assert "recovery" not in client.get("/api/health").json()


def test_open_folder_rejects_legacy_wechat_paths_outside_the_recorded_root(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main,
        "get_config",
        lambda: SimpleNamespace(download_dir=str(tmp_path / "downloads")),
    )
    monkeypatch.setattr(
        "app.wechat.list_jobs",
        lambda: [
            {
                "id": "w1",
                "output_root": str(tmp_path / "root"),
                "path": str(tmp_path / "elsewhere" / "x.mp4"),
            }
        ],
    )
    client = TestClient(main.app, base_url="http://localhost")
    response = client.post(
        "/api/folder/open",
        json={"job_id": "w1", "wechat": True, "prepare_only": True},
    )
    assert response.status_code == 400
    assert "保存范围" in response.json()["detail"]


def test_build_id_covers_the_desktop_launcher(tmp_path):
    (tmp_path / "app" / "static").mkdir(parents=True)
    (tmp_path / "run.py").write_text("a", encoding="utf-8")
    (tmp_path / "launch_shiying.py").write_text("a", encoding="utf-8")
    first = calculate_build_id(tmp_path)
    (tmp_path / "launch_shiying.py").write_text("b", encoding="utf-8")
    second = calculate_build_id(tmp_path)
    assert first != second


def test_preview_helpers_live_inside_the_ui_closure():
    source = (Path(__file__).resolve().parents[1] / "app" / "static" / "home.js").read_text(encoding="utf-8")
    tail = source[source.index("  // ---- 预览选集"):]
    # Every preview helper must be declared before the single initialize() call
    # and before the IIFE closes.
    assert tail.index("async function openPreview") < tail.index("async function initialize")
    assert tail.index("async function initialize") < tail.rindex("initialize();")
    assert source.rstrip().endswith("})();")
    # The old defect: the IIFE closed before the preview helpers were declared.
    assert source.index("})();") == source.rindex("})();")


def test_runtime_api_uses_a_per_request_timeout():
    source = (Path(__file__).resolve().parents[1] / "app" / "static" / "home.js").read_text(encoding="utf-8")
    assert "PREVIEW_TIMEOUT_MS" in source
    assert "LOGIN_TIMEOUT_MS" in source
    assert "options.timeoutMs" in source
    assert "AbortSignal.timeout(20000)" not in source
