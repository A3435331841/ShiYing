import pytest
from app.downloader import MediaDownloader, DownloaderConfig
from app.models import Platform, SourceKind, DownloadItem, MediaType
from app.bilibili_opus import BilibiliOpus
from app.errors import DiscoveryError, MediaDownloadError


def test_discover_bilibili_opus(monkeypatch):
    def fake_parse(url):
        return BilibiliOpus(
            opus_id="1189486481077436417",
            title="测试B站图文动态",
            author="测试UP主",
            author_id="123456",
            text_content="这是正文内容",
            images=[]
        )

    monkeypatch.setattr("app.bilibili_opus.parse_bilibili_opus", fake_parse)
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    res = engine.discover("https://www.bilibili.com/opus/1189486481077436417", Platform.BILIBILI, SourceKind.ITEM)
    assert res.author == "测试UP主"
    assert len(res.items) == 1
    assert res.items[0].media_type == MediaType.IMAGE
    assert res.items[0].title == "测试B站图文动态"


def test_opus_text_is_saved_and_empty_or_changed_work_fails(monkeypatch, tmp_path):
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    item = DownloadItem(id="one", media_id="123", source_url="https://www.bilibili.com/opus/123")
    work = BilibiliOpus("123", "标题", "作者", "1", "正文")
    monkeypatch.setattr("app.bilibili_opus.parse_bilibili_opus", lambda url: work)
    def download():
        return engine._download_bilibili_opus(item, tmp_path, callback=None, should_cancel=lambda: False)
    result = download()
    assert len(result.output_paths) == 1
    from pathlib import Path
    assert Path(result.output_paths[0]).read_text(encoding="utf-8") == "正文"
    work.text_content = ""
    with pytest.raises(MediaDownloadError, match="未能提取"):
        download()
    work.opus_id = "456"
    with pytest.raises(MediaDownloadError, match="身份"):
        download()


def test_article_redirect_rejects_untrusted_target(monkeypatch):
    from app.bilibili_opus import parse_bilibili_opus
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["allow_redirects"] is False
        return type("Response", (), {"status_code": 302, "headers": {"Location": "http://127.0.0.1/opus/123"}})()
    monkeypatch.setattr("app.bilibili_opus.requests.get", get)
    with pytest.raises(DiscoveryError, match="不可信"):
        parse_bilibili_opus("https://www.bilibili.com/read/cv123")
    assert len(calls) == 1


def test_opus_rejects_mismatched_response_identity(monkeypatch):
    from app.bilibili_opus import parse_bilibili_opus
    class Response:
        status_code = 200
        def json(self):
            return {"code": 0, "data": {"item": {"id_str": "456", "modules": []}}}
    monkeypatch.setattr("app.bilibili_opus.requests.get", lambda *args, **kwargs: Response())
    with pytest.raises(DiscoveryError, match="身份不匹配"):
        parse_bilibili_opus("https://www.bilibili.com/opus/123")


def test_opus_api_rejects_unexpected_redirect(monkeypatch):
    from app.bilibili_opus import parse_bilibili_opus
    class RedirectResponse:
        status_code = 302
        headers = {"Location": "https://evil.com"}
    monkeypatch.setattr("app.bilibili_opus.requests.get", lambda *args, **kwargs: RedirectResponse())
    with pytest.raises(DiscoveryError, match="非预期跳转"):
        parse_bilibili_opus("https://www.bilibili.com/opus/123")

