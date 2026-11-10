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
