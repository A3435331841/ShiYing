import pytest
from app.downloader import MediaDownloader, DownloaderConfig
from app.models import Platform, SourceKind, DownloadItem, MediaType


def test_discover_wechat_requires_cookie(monkeypatch, tmp_path):
    monkeypatch.setattr("app.wechat.load_secret", lambda path: "")
    monkeypatch.setattr("app.wechat._chrome_yuanbao_cookie", lambda: "")
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    with pytest.raises(Exception, match="腾讯元宝登录态"):
        engine.discover("https://weixin.qq.com/sph/A7WJC6T1J8", Platform.WECHAT, SourceKind.ITEM)


def test_discover_wechat_success(monkeypatch, tmp_path):
    monkeypatch.setattr("app.wechat.load_secret", lambda path: "mock-cookie")
    monkeypatch.setattr("app.wechat.profile_yuanbao", lambda url, cookie: (
        {"objectId": "12345", "description": "测试视频", "videoInfo": {"url": "https://finder.video.qq.com/video", "decodeKey": "999"}},
        {"nickname": "测试UP主"}
    ))
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    res = engine.discover("https://weixin.qq.com/sph/A7WJC6T1J8", Platform.WECHAT, SourceKind.ITEM)
    assert res.author == "测试UP主"
    assert len(res.items) == 1
    assert res.items[0].media_id == "12345"
    assert res.items[0].title == "测试视频"
    assert "wechat_decode_key" not in res.items[0].metadata
    assert "wechat_media_url" not in res.items[0].metadata


def test_download_rejects_changed_wechat_identity(monkeypatch, tmp_path):
    from app.errors import MediaDownloadError
    monkeypatch.setattr("app.wechat.load_secret", lambda path: "cookie")
    monkeypatch.setattr("app.wechat.profile_yuanbao", lambda *args: ({"objectId": "other"}, {}))
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    item = DownloadItem(id="one", media_id="expected", source_url="https://weixin.qq.com/sph/example")
    with pytest.raises(MediaDownloadError, match="身份发生变化"):
        engine._download_wechat_item(item, tmp_path, callback=None, should_cancel=lambda: False)


def test_old_wechat_media_secrets_are_not_public():
    from app.main import _public_job
    from app.models import DownloadJob
    item = DownloadItem(id="one", source_url="https://weixin.qq.com/sph/example", metadata={
        "wechat_media_url": "https://finder.video.qq.com/video?token=secret",
        "wechat_decode_key": 123,
    })
    job = DownloadJob(id="job", source_url=item.source_url, platform=Platform.WECHAT,
                      source_kind=SourceKind.ITEM, output_root=".", items=[item])
    assert _public_job(job).items[0].metadata == {}
    assert item.metadata["wechat_decode_key"] == 123
