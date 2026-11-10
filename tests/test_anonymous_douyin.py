import pytest
from app.downloader import DownloaderConfig, MediaDownloader
from app.errors import TemporaryAccessError


def test_public_discovery_never_accepts_a_mismatched_work(monkeypatch):
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None, anonymous_douyin=True))
    calls = []
    def fake_fetch(identifier, **kwargs):
        calls.append(kwargs)
        return {'aweme_id': '1234567890123456780', 'author': {'sec_uid': 'other-author'}}
    monkeypatch.setattr('app.downloader.fetch_signed_aweme_detail', fake_fetch)
    with pytest.raises(TemporaryAccessError):
        engine._discover_douyin_item('https://www.douyin.com/video/1234567890123456789', lambda: False)
    assert calls[0]['use_browser_cookies'] is False


def test_public_discovery_survives_missing_create_time(monkeypatch):
    """create_time 是可选元数据；缺失时上传时间应为 None 而非 KeyError 崩溃。"""

    engine = MediaDownloader(DownloaderConfig(cookie_browser=None, anonymous_douyin=True))
    monkeypatch.setattr(
        "app.engine.douyin_media.fetch_signed_aweme_detail",
        lambda identifier, **kwargs: {"aweme_id": identifier},
    )
    monkeypatch.setattr(
        "app.engine.douyin_media.verified_aweme_metadata",
        lambda detail, expected: {
            "aweme_id": expected,
            "author": "作者",
            "title": "标题",
            "media_kind": "video",
            "owner_id": "owner-1",
            "sec_uid": "sec-1",
            "video_uri": "verified-uri",
        },
    )
    result = engine._discover_douyin_item(
        "https://www.douyin.com/video/1234567890123456789", lambda: False
    )
    assert len(result.items) == 1
    assert result.items[0].upload_date is None
    assert result.items[0].media_type.value == "video"
