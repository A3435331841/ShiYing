from __future__ import annotations

import shutil
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app import main
from app.models import DownloadItem, DownloadJob, JobStatus, MediaType, Platform

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_index_html_has_optimized_settings_and_no_legacy_platform_symbols():
    html = (PROJECT_ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")

    # Keep the clean settings icon with its circle and gear path.
    assert 'symbol id="i-settings"' in html
    assert '<circle cx="12" cy="12" r="3"/>' in html
    assert "M19.4 15" in html
    assert "m9 3-1 3-3 1-2 4" not in html

    # Official local artwork replaces the old hand-drawn platform symbols.
    assert 'symbol id="p-' not in html
    assert 'href="#p-' not in html
    assert "▱" not in html


def test_home_css_and_js_have_cover_thumbnail_support():
    css = (PROJECT_ROOT / "app" / "static" / "home.css").read_text(encoding="utf-8")
    js = (PROJECT_ROOT / "app" / "static" / "home.js").read_text(encoding="utf-8")

    # CSS definitions
    assert ".task-thumb-wrap" in css
    assert ".task-thumb-img" in css
    assert ".thumb-platform-icon" in css
    assert ".platform-icon" in css
    assert "object-fit: contain;" in css

    # JS rendering
    assert "task-thumb-wrap" in js
    assert "/api/jobs/" in js and "/cover" in js
    assert "task-thumb-img" in js
    assert "thumb-platform-icon" in js


def test_cover_endpoint_returns_404_for_nonexistent_job():
    client = TestClient(main.app, base_url="http://127.0.0.1")
    resp = client.get("/api/jobs/nonexistent-job-id/cover")
    assert resp.status_code == 404


def test_cover_endpoint_serves_video_thumbnail(tmp_path, monkeypatch):
    # Check if ffmpeg exists in environment
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")

    client = TestClient(main.app, base_url="http://127.0.0.1")
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir(parents=True)

    # Create a real small test video with ffmpeg
    test_video = downloads_dir / "test.mp4"
    import subprocess
    cmd = [
        "ffmpeg", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(test_video), "-y"
    ]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        pytest.skip("ffmpeg testsrc generation failed")

    from app.models import SourceKind

    job = DownloadJob(
        id="test-job-with-video",
        source_url="https://www.bilibili.com/video/BV1test",
        source_kind=SourceKind.ITEM,
        platform=Platform.BILIBILI,
        title="Test Bilibili Video",
        status=JobStatus.COMPLETED,
        output_root=str(downloads_dir),
        output_path=downloads_dir,
        items=[
            DownloadItem(
                id="item-1",
                media_id="BV1test",
                source_url="https://www.bilibili.com/video/BV1test",
                title="Test Video",
                media_type=MediaType.VIDEO,
                output_paths=[str(test_video)],
            )
        ]
    )

    monkeypatch.setattr(main.manager, "get_job", lambda jid: job if jid == job.id else None)

    resp = client.get(f"/api/jobs/{job.id}/cover")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/jpeg")
    assert len(resp.content) > 1000
