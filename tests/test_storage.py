from __future__ import annotations

import json

import pytest

from app.models import DownloadItem, DownloadJob, Platform, SourceKind
from app.storage import JobNotFoundError, JsonJobStore


def make_job(job_id: str = "job-1") -> DownloadJob:
    return DownloadJob(
        id=job_id,
        source_url="https://www.youtube.com/@BlenderOfficial",
        platform=Platform.YOUTUBE,
        source_kind=SourceKind.PROFILE,
        output_root="downloads",
    )


def test_store_round_trip(tmp_path) -> None:
    store = JsonJobStore(tmp_path)
    job = make_job()

    store.save(job)

    assert store.get(job.id) == job
    assert store.load_all() == ([job], [])


def test_store_rejects_unsafe_job_ids(tmp_path) -> None:
    store = JsonJobStore(tmp_path)

    with pytest.raises(ValueError, match="Invalid job ID"):
        store.get("../outside")


def test_store_reports_missing_job(tmp_path) -> None:
    store = JsonJobStore(tmp_path)

    with pytest.raises(JobNotFoundError):
        store.get("missing")


def test_store_skips_corrupt_state_and_returns_warning(tmp_path) -> None:
    store = JsonJobStore(tmp_path)
    store.save(make_job("healthy"))
    (tmp_path / "broken.json").write_text("{not-json", encoding="utf-8")

    jobs, warnings = store.load_all()

    assert [job.id for job in jobs] == ["healthy"]
    assert len(warnings) == 1
    assert "broken.json" in warnings[0]


def test_save_omits_transient_media_urls_without_mutating_memory(tmp_path) -> None:
    store = JsonJobStore(tmp_path)
    job = make_job()
    signed_url = "https://cdn.example.com/media.mp4?token=secret&expires=123"
    job.items.append(
        DownloadItem(
            id="item-1",
            source_url="https://www.douyin.com/video/1234567890123456",
            metadata={
                "douyin_item_media": {
                    "media_id": "1234567890123456",
                    "video_uri": "v0300identity",
                    "candidates": [signed_url],
                    "direct_candidates": [
                        {
                            "width": 1080,
                            "height": 1920,
                            "urls": [signed_url],
                            "bit_rate": 2_000_000,
                        }
                    ],
                },
                "wechat_media_url": signed_url,
                "wechat_decode_key": "secret-key",
            },
        )
    )

    store.save(job)

    raw = (tmp_path / "job-1.json").read_text(encoding="utf-8")
    assert "secret" not in raw and "expires" not in raw
    persisted = store.get(job.id)
    cached = persisted.items[0].metadata["douyin_item_media"]
    assert cached["media_urls_persisted"] is False
    assert cached["direct_candidates"][0]["width"] == 1080
    assert "urls" not in cached["direct_candidates"][0]
    assert job.items[0].metadata["douyin_item_media"]["candidates"] == [signed_url]
    assert job.items[0].metadata["wechat_decode_key"] == "secret-key"


def test_save_replaces_existing_state_atomically(tmp_path) -> None:
    store = JsonJobStore(tmp_path)
    job = make_job()
    store.save(job)
    job.revision = 2

    store.save(job)

    payload = json.loads((tmp_path / "job-1.json").read_text(encoding="utf-8"))
    assert payload["revision"] == 2
    assert list(tmp_path.glob("*.tmp")) == []
