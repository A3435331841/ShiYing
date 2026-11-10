from app.downloader import DOUYIN_LIVE_PHOTO_STATIC_FALLBACK_WARNING
from app.models import DownloadItem, DownloadJob, ItemStatus, JobStatus, Platform, SourceKind
from app.task_manager import DownloadManager


def test_missing_live_motion_is_partial(tmp_path):
    manager = DownloadManager(state_dir=tmp_path / "state", default_output_root=tmp_path)
    job = DownloadJob(
        id="live",
        source_url="https://www.douyin.com/video/123",
        platform=Platform.DOUYIN,
        source_kind=SourceKind.ITEM,
        output_root=str(tmp_path),
        warning=DOUYIN_LIVE_PHOTO_STATIC_FALLBACK_WARNING,
        items=[DownloadItem(id="item", source_url="https://www.douyin.com/video/123",
                            status=ItemStatus.COMPLETED)],
    )
    manager._jobs[job.id] = job
    try:
        manager._finish_job(job.id)
        assert manager.get_job(job.id).status == JobStatus.PARTIAL
        assert manager.get_job(job.id).error == DOUYIN_LIVE_PHOTO_STATIC_FALLBACK_WARNING
    finally:
        manager.shutdown()
