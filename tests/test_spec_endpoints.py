from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import main as main_module
from app.models import DownloadItem, DownloadJob, ItemStatus, JobStatus, Platform, SourceKind


def _job(tmp_path: Path) -> DownloadJob:
    return DownloadJob(
        id="job-export",
        source_url="https://www.bilibili.com/video/BV1xx411c7mD?sign=secret&xsec_token=t",
        platform=Platform.BILIBILI,
        source_kind=SourceKind.ITEM,
        output_root=str(tmp_path),
        status=JobStatus.COMPLETED,
        items=[
            DownloadItem(
                id="item-1",
                source_url="https://www.bilibili.com/video/BV1xx411c7mD?sign=secret",
                title="作品",
                media_type="video",
                status=ItemStatus.COMPLETED,
            )
        ],
        total_items=1,
        completed_items=1,
    )


def test_platforms_endpoint_exposes_states(monkeypatch) -> None:
    client = TestClient(main_module.app, base_url="http://localhost")
    response = client.get("/api/platforms")
    assert response.status_code == 200
    entries = response.json()
    by_key = {(entry["platform"], entry["channel"]): entry for entry in entries}
    assert by_key[("bilibili", "default")]["download"] == "supported"
    assert by_key[("kuaishou", "default")]["download"] == "experimental"
    assert by_key[("wechat", "client_capture")]["download"] == "unavailable"
    assert "元宝" in by_key[("wechat", "client_capture")]["reason"]


def test_environment_includes_capability_impact(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "check_environment",
        lambda _: {"checks": [{"id": "ffmpeg", "ok": False}]},
    )
    client = TestClient(main_module.app, base_url="http://localhost")
    payload = client.get("/api/environment").json()
    capabilities = {item["capability"] for item in payload["capabilities_impact"]}
    assert {"merging", "frame_extract"} <= capabilities


def test_diagnostic_export_strips_sensitive_fields(monkeypatch, tmp_path: Path) -> None:
    job = _job(tmp_path)
    monkeypatch.setattr(main_module.manager, "get_job", lambda _job_id: job)
    client = TestClient(main_module.app, base_url="http://localhost")
    response = client.get("/api/jobs/job-export/export")
    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in ("cookie", "authorization", "temp_url", "sign="):
        assert forbidden not in serialized
    assert payload["job"]["source_url"] == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert payload["items"][0]["file_facts"] == []


def test_diagnostic_export_unknown_job_returns_404(monkeypatch) -> None:
    from app.storage import JobNotFoundError

    def missing(_job_id: str):
        raise JobNotFoundError("nope")

    monkeypatch.setattr(main_module.manager, "get_job", missing)
    client = TestClient(main_module.app, base_url="http://localhost")
    assert client.get("/api/jobs/nope/export").status_code == 404


def test_events_snapshot_follows_since(monkeypatch) -> None:
    job = _job(Path("."))
    monkeypatch.setattr(main_module.manager, "list_jobs", lambda: [job])

    async def collect() -> tuple[str, str]:
        response = await main_module.events(since=0)
        iterator = response.body_iterator
        first = await iterator.__anext__()
        second = await iterator.__anext__()
        await iterator.aclose()
        return str(first), str(second)

    first, second = asyncio.run(collect())
    assert ": connected" in first
    assert second.startswith("event: snapshot")
    data = json.loads(second.split("data: ", 1)[1].strip())
    assert data["jobs"][0]["id"] == "job-export"
    assert data["max_revision"] >= 0


def test_events_since_filters_by_per_job_revision(monkeypatch) -> None:
    """重连后的增量过滤必须按 job_id + revision，而非全局最大 revision 标量。"""
    high = _job(Path("."))
    high.id = "high-revision"
    high.revision = 9686
    low = _job(Path("."))
    low.id = "low-revision"
    low.revision = 5
    monkeypatch.setattr(main_module.manager, "list_jobs", lambda: [high, low])

    async def collect() -> list[str]:
        response = await main_module.events(since=9686)
        iterator = response.body_iterator
        frames = [str(await iterator.__anext__()) for _ in range(2)]
        listeners = list(main_module.manager._listeners)
        low.revision = 6
        for listener in listeners:
            listener(None, low)
        try:
            frames.append(str(await asyncio.wait_for(iterator.__anext__(), 2)))
        except (asyncio.TimeoutError, StopAsyncIteration):
            frames.append("")
        high.revision = 9687
        for listener in listeners:
            listener(None, high)
        try:
            frames.append(str(await asyncio.wait_for(iterator.__anext__(), 2)))
        except (asyncio.TimeoutError, StopAsyncIteration):
            frames.append("")
        await iterator.aclose()
        return frames

    frames = asyncio.run(collect())
    assert "connected" in frames[0]
    assert frames[1].startswith("event: snapshot")
    low_frame = json.loads(frames[2].split("data: ", 1)[1].strip())
    assert low_frame["id"] == "low-revision"
    assert low_frame["revision"] == 6
    high_frame = json.loads(frames[3].split("data: ", 1)[1].strip())
    assert high_frame["id"] == "high-revision"
    assert high_frame["revision"] == 9687


def test_create_job_rejects_unconfirmed_experimental(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "_capabilities",
        lambda: main_module.load_capabilities(
            main_module.PROJECT_ROOT / "data" / "capabilities.json",
            root=main_module.PROJECT_ROOT,
        ),
    )
    called = []
    monkeypatch.setattr(
        main_module.manager,
        "create_job",
        lambda url, **options: called.append((url, options)) or _job(Path(".")),
    )
    client = TestClient(main_module.app, base_url="http://localhost")
    response = client.post(
        "/api/jobs",
        json={"url": "https://v.kuaishou.com/abc123", "auto_start": False},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "experimental_confirmation_required"
    assert called == []
    confirmed = client.post(
        "/api/jobs",
        json={
            "url": "https://v.kuaishou.com/abc123",
            "auto_start": False,
            "confirmed_experimental": True,
        },
    )
    assert confirmed.status_code == 201
    assert called


def test_create_job_global_cookie_switch_applies_to_all_platforms(monkeypatch) -> None:
    """用户显式开启的 Chrome 登录态不能被平台 requires_login 静默否决。"""
    from types import SimpleNamespace

    captured: list[str | None] = []

    class _Config:
        download_dir = "."
        use_chrome_cookies = True
        chrome_profile = None

    monkeypatch.setattr(main_module, "get_config", lambda: _Config())
    monkeypatch.setattr(main_module.manager, "list_jobs", lambda: [])
    monkeypatch.setattr(main_module, "_public_job", lambda job: job)

    def _create_job(url, **options):
        captured.append(options.get("cookie_browser"))
        return _job(Path("."))

    monkeypatch.setattr(main_module.manager, "create_job", _create_job)

    # bilibili 与 douyin 的 requires_login 均为 false，但抖音图文等个别作品仍需登录。
    request = SimpleNamespace(
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        confirmed_experimental=False,
        auto_start=False,
        item_ids=None,
    )
    main_module.create_job(request)
    request.url = "https://v.douyin.com/abc123"
    main_module.create_job(request)
    assert captured == ["chrome", "chrome"]