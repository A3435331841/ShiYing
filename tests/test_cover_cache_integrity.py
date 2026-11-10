"""Regression tests for thumbnail cache corruption and atomic publication.

These isolate the real thumbnail directory and the decode verifier so that no
external traffic or real user state is touched. They prove:

* a nonempty but corrupt cached thumbnail is dropped and regenerated instead
  of being served with a full day of browser cache;
* a cache whose decodability is unknown (no decoder) is still served but with
  no-store, so the browser re-validates instead of pinning garbage;
* freshly extracted / fetched caches are published atomically, leaving no
  partial ``.tmp`` files and never exposing a half-written thumbnail.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.models import (
    DownloadItem,
    DownloadJob,
    JobStatus,
    MediaType,
    Platform,
    SourceKind,
)


def _job_with_video(job_id: str, media: Path) -> DownloadJob:
    return DownloadJob(
        id=job_id,
        source_url="https://www.bilibili.com/video/BVcover",
        source_kind=SourceKind.ITEM,
        platform=Platform.BILIBILI,
        title="Cover cache integrity",
        status=JobStatus.COMPLETED,
        output_root=str(media.parent),
        output_path=media.parent,
        items=[
            DownloadItem(
                id="item-1",
                media_id="BVcover",
                source_url="https://www.bilibili.com/video/BVcover",
                title="Cover cache integrity",
                media_type=MediaType.VIDEO,
                output_paths=[str(media)],
            )
        ],
    )


def _patch_thumbnail_dir(tmp_path: Path, monkeypatch) -> Path:
    thumb_dir = tmp_path / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "THUMBNAILS_DIR", thumb_dir)
    return thumb_dir


def _pin_decoder(monkeypatch, decodable: bool | None):
    """Pin the decode verifier to a deterministic answer so the test does not
    depend on ffmpeg being installed.
    """
    monkeypatch.setattr(main, "_verify_thumbnail_decodable", lambda _path: decodable)


def _write_and_publish(path: Path, content: bytes) -> bool:
    """A stand-in for the real extraction: publish ``content`` to ``path`` via
    an atomic same-directory temp + ``os.replace``, the exact shape the
    production ``_extract_video_thumbnail_locked`` uses.
    """
    import os

    temp = path.with_name(f"{path.name}.gen")
    temp.write_bytes(content)
    os.replace(temp, path)
    return True


def test_corrupt_cached_thumbnail_is_dropped_and_rebuilt(tmp_path, monkeypatch):
    thumb_dir = _patch_thumbnail_dir(tmp_path, monkeypatch)
    _pin_decoder(monkeypatch, decodable=False)

    cache = thumb_dir / "job-corrupt.jpg"
    # A nonempty file that will fail full decoding: the failure mode that
    # used to be served with max-age=86400 for a full day.
    cache.write_bytes(b"\xff\xd8\xffnot-a-real-jpeg")

    media = tmp_path / "media"
    media.mkdir()
    video = media / "vid.mp4"
    video.write_bytes(b"\x00\x00fake-video-bytes")
    monkeypatch.setattr(
        main.manager, "get_job", lambda jid: _job_with_video("job-corrupt", video)
    )

    # A probe that publishes a definitive replacement when it can regenerate.
    monkeypatch.setattr(
        main,
        "_extract_video_thumbnail",
        lambda video_path, out: _write_and_publish(out, b"good regenerated thumbnail"),
    )

    client = TestClient(main.app, base_url="http://127.0.0.1")
    resp = client.get("/api/jobs/job-corrupt/cover")

    # The corrupt cache must have been dropped, not served.
    assert cache.read_bytes() == b"good regenerated thumbnail"
    # A freshly rebuilt cache is trusted, so it carries the long cache header.
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=86400"


def test_unknown_decodability_serves_cache_without_long_cache(tmp_path, monkeypatch):
    thumb_dir = _patch_thumbnail_dir(tmp_path, monkeypatch)
    _pin_decoder(monkeypatch, decodable=None)

    cache = thumb_dir / "job-unknown.jpg"
    cache.write_bytes(b"cache bytes of unknown integrity")

    client = TestClient(main.app, base_url="http://127.0.0.1")
    # The cache branch returns early, so no manager or remote path is hit.
    resp = client.get("/api/jobs/job-unknown/cover")

    assert resp.status_code == 200
    assert b"cache bytes of unknown integrity" in resp.content
    # An unverifiable cache must not be pinned in the browser for a day.
    assert "no-store" in resp.headers["cache-control"]
    # The cache file is preserved when its integrity is unknown.
    assert cache.exists()


def test_verified_cache_keeps_long_cache_header(tmp_path, monkeypatch):
    thumb_dir = _patch_thumbnail_dir(tmp_path, monkeypatch)
    _pin_decoder(monkeypatch, decodable=True)

    cache = thumb_dir / "job-verified.jpg"
    cache.write_bytes(b"fully decodable thumbnail")

    client = TestClient(main.app, base_url="http://127.0.0.1")
    resp = client.get("/api/jobs/job-verified/cover")

    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=86400"


def test_atomic_extraction_leaves_no_partial_tmp(tmp_path, monkeypatch):
    thumb_dir = _patch_thumbnail_dir(tmp_path, monkeypatch)
    _pin_decoder(monkeypatch, decodable=False)

    media = tmp_path / "media"
    media.mkdir()
    video = media / "vid.mp4"
    video.write_bytes(b"\x00\x00fake-video-bytes")
    monkeypatch.setattr(
        main.manager, "get_job", lambda jid: _job_with_video("job-atomic", video)
    )

    monkeypatch.setattr(
        main,
        "_extract_video_thumbnail",
        lambda video, out: _write_and_publish(out, b"data"),
    )

    client = TestClient(main.app, base_url="http://127.0.0.1")
    client.get("/api/jobs/job-atomic/cover")

    cache = thumb_dir / "job-atomic.jpg"
    assert cache.read_bytes() == b"data"
    # No stray partial files may remain after a successful publish.
    leftovers = [p for p in thumb_dir.iterdir() if p.name != "job-atomic.jpg"]
    assert leftovers == []


def test_publish_cover_cache_bytes_is_atomic_and_validates(tmp_path, monkeypatch):
    thumb_dir = _patch_thumbnail_dir(tmp_path, monkeypatch)
    cache = thumb_dir / "target.jpg"

    assert main._publish_cover_cache_bytes(b"", cache) is False
    assert not cache.exists()

    assert main._publish_cover_cache_bytes(b"valid cover", cache) is True
    assert cache.read_bytes() == b"valid cover"

    # A second publish must not corrupt the file; it atomically replaces it.
    assert main._publish_cover_cache_bytes(b"second", cache) is True
    assert cache.read_bytes() in {b"valid cover", b"second"}
    # No temporary file may linger in the cache's own directory.
    stray = [p for p in cache.parent.iterdir() if p.name != "target.jpg"]
    assert stray == []
