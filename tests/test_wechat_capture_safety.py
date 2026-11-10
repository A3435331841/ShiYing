import os
import threading

import pytest

from app import wechat
from app.wechat_secret import PREFIX, load_secret, save_secret


class _FakeCookie:
    def __init__(self, domain, name, value, expires=0):
        self.domain = domain
        self.name = name
        self.value = value
        self.expires = expires


def test_chrome_fallback_keeps_only_current_yuanbao_cookies(monkeypatch):
    import time
    jar = [
        _FakeCookie(".yuanbao.tencent.com", "a", "1"),
        _FakeCookie("yuanbao.tencent.com", "b", "2", expires=int(time.time()) + 3600),
        _FakeCookie(".yuanbao.tencent.com", "old", "3", expires=1),
        _FakeCookie(".example.com", "other", "4"),
    ]
    monkeypatch.setattr(wechat, "extract_cookies_from_browser", lambda *a, **k: jar)
    assert wechat._chrome_yuanbao_cookie() == "a=1; b=2"


def test_chrome_read_failure_gives_clear_error_without_details(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret-path-and-value")
    monkeypatch.setattr(wechat, "extract_cookies_from_browser", boom)
    with pytest.raises(wechat.WeChatUserError) as info:
        wechat._chrome_yuanbao_cookie()
    assert "secret-path-and-value" not in str(info.value)


def test_no_cookie_does_not_start_unverified_proxy(monkeypatch, tmp_path):
    monkeypatch.setattr(wechat, "SECRET", tmp_path / "missing")
    monkeypatch.setattr(wechat, "_chrome_yuanbao_cookie", lambda: "")
    monkeypatch.setattr(wechat, "STATE", tmp_path / "jobs.json")
    job = {"id": "test", "url": "https://weixin.qq.com/sph/example"}
    monkeypatch.setattr(wechat, "JOBS", [job])
    monkeypatch.setattr(wechat, "CANCELLATIONS", {"test": threading.Event()})
    wechat.download(job, str(tmp_path))
    assert job["status"] == "failed"
    assert "暂不可用" in job["message"]


def test_share_code_accepts_only_exact_weixin_sph_links():
    assert wechat._share_code("https://weixin.qq.com/sph/target") == "target"
    assert wechat._share_code("target") == "target"
    assert wechat._share_code("https://evil@weixin.qq.com/sph/target") is None
    assert wechat._share_code("https://weixin.qq.com:444/sph/target") is None


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_credential_protection_and_legacy_migration(tmp_path):
    path = tmp_path / "cookie.txt"
    path.write_text("session=private", encoding="utf-8")
    assert load_secret(path) == "session=private"
    assert path.read_bytes().startswith(PREFIX)
    assert b"session=private" not in path.read_bytes()
    save_secret(path, "session=new")
    assert load_secret(path) == "session=new"


def test_unverified_video_is_not_published(monkeypatch, tmp_path):
    job = {"id": "unverified", "url": "https://weixin.qq.com/sph/example"}
    monkeypatch.setattr(wechat, "STATE", tmp_path / "jobs.json")
    monkeypatch.setattr(wechat, "JOBS", [job])
    monkeypatch.setattr(wechat, "CANCELLATIONS", {"unverified": threading.Event()})
    monkeypatch.setattr(wechat, "load_secret", lambda path: "cookie")
    monkeypatch.setattr(wechat, "profile_yuanbao", lambda *args: (
        {"videoInfo": {"url": "https://finder.video.qq.com/video", "decodeKey": 1}, "id": "one"},
        {"nickname": "author"},
    ))
    monkeypatch.setattr(wechat, "download_stream_and_decrypt",
                        lambda url, key, path, cancel, progress: path.write_bytes(b"bad"))
    monkeypatch.setattr(wechat.subprocess, "run",
                        lambda *args, **kwargs: type("Result", (), {"returncode": 1, "stdout": b"{}"})())
    wechat.download(job, str(tmp_path))
    assert job["status"] == "failed"
    assert not list(tmp_path.rglob("*.mp4"))
    assert list(tmp_path.rglob("*.unverified"))


def test_video_without_duration_is_not_verified(monkeypatch, tmp_path):
    monkeypatch.setattr(wechat.subprocess, "run", lambda *args, **kwargs: type(
        "Result", (), {"returncode": 0, "stdout": b'{"streams":[{"codec_type":"video","width":1280,"height":720}]}'}
    )())
    assert not wechat.verify_video(tmp_path / "incomplete.mp4")


def test_verified_video_is_published_without_overwriting(monkeypatch, tmp_path):
    job = {"id": "good", "url": "https://weixin.qq.com/sph/example"}
    monkeypatch.setattr(wechat, "STATE", tmp_path / "jobs.json")
    monkeypatch.setattr(wechat, "JOBS", [job])
    monkeypatch.setattr(wechat, "CANCELLATIONS", {"good": threading.Event()})
    monkeypatch.setattr(wechat, "load_secret", lambda path: "cookie")
    monkeypatch.setattr(wechat, "profile_yuanbao", lambda *args: (
        {"videoInfo": {"url": "https://finder.video.qq.com/video", "decodeKey": 1}, "id": "one"},
        {"nickname": "author"},
    ))
    monkeypatch.setattr(wechat, "download_stream_and_decrypt",
                        lambda url, key, path, cancel, progress: path.write_bytes(b"verified"))
    monkeypatch.setattr(wechat.subprocess, "run",
                        lambda *args, **kwargs: type("Result", (), {
                            "returncode": 0, "stdout": b'{"streams":[{"codec_type":"video","width":1280,"height":720}],"format":{"duration":"10"}}'
                        })())
    wechat.download(job, str(tmp_path))
    assert job["status"] == "completed"
    result = next(tmp_path.rglob("*.mp4"))
    assert result.read_bytes() == b"verified"
    assert not list(tmp_path.rglob("*.checking.mp4"))
    job["id"] = "duplicate"
    wechat.CANCELLATIONS["duplicate"] = threading.Event()
    wechat.download(job, str(tmp_path))
    assert job["status"] == "failed"
    assert result.read_bytes() == b"verified"


def test_wechat_error_does_not_expose_exception_details(monkeypatch, tmp_path):
    job = {"id": "secret", "url": "https://weixin.qq.com/sph/example"}
    monkeypatch.setattr(wechat, "STATE", tmp_path / "jobs.json")
    monkeypatch.setattr(wechat, "JOBS", [job])
    monkeypatch.setattr(wechat, "CANCELLATIONS", {"secret": threading.Event()})
    monkeypatch.setattr(wechat, "load_secret", lambda path: "sensitive-cookie")
    def fail(*args):
        raise ValueError("sensitive-cookie https://example.com/?token=private")
    monkeypatch.setattr(wechat, "profile_yuanbao", fail)
    wechat.download(job, str(tmp_path))
    assert job["status"] == "failed"
    assert "sensitive-cookie" not in job["message"]
    assert "private" not in (tmp_path / "jobs.json").read_text("utf-8")


def test_media_redirect_outside_trusted_host_is_not_requested(monkeypatch, tmp_path):
    calls = []
    class Redirect:
        status_code = 302
        headers = {"Location": "http://127.0.0.1/private"}
        def close(self):
            pass
    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["allow_redirects"] is False
        return Redirect()
    monkeypatch.setattr(wechat.requests, "get", get)
    with pytest.raises(wechat.WeChatUserError):
        wechat.download_stream_and_decrypt(
            "https://finder.video.qq.com/video", 0, tmp_path / "video.mp4",
            threading.Event(),
        )
    assert calls == ["https://finder.video.qq.com/video"]


def test_yuanbao_redirect_does_not_forward_cookie(monkeypatch):
    calls = []
    class Response:
        status_code = 302
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            calls.append((url, kwargs["allow_redirects"]))
            return Response()
    monkeypatch.setattr(wechat.requests, "Session", Session)
    with pytest.raises(wechat.WeChatUserError):
        wechat.profile_yuanbao("https://weixin.qq.com/sph/example", "secret")
    assert calls == [("https://yuanbao.tencent.com/api/weixin/get_parse_result", False)]


@pytest.mark.parametrize("field", ["h264VideoInfo", "h265VideoInfo", "videoInfo"])
def test_yuanbao_nested_media_and_feed_key(field):
    url = "https://finder.video.qq.com/video"
    assert wechat.media_selection({field: {"videoUrl": url}, "decodeKey": "123"}) == (url, 123)


def test_yuanbao_rejects_mismatched_or_invalid_media_keys():
    url = "https://finder.video.qq.com/video"
    for value in (
        {"h264VideoInfo": {"videoUrl": url, "decodeKey": 2}, "decodeKey": 3},
        {"h264VideoInfo": {"videoUrl": url}, "decodeKey": -1},
        {"h264VideoInfo": {"videoUrl": url}, "decodeKey": 1.2},
        {"h264VideoInfo": {"videoUrl": "https://evil.example/media"}},
    ):
        with pytest.raises(wechat.WeChatUserError):
            wechat.media_selection(value)


def _fake_yuanbao_session(monkeypatch, feed_info):
    class Response:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
        def raise_for_status(self):
            pass
        def json(self):
            return self._payload
    responses = [
        Response({"data": {"playable_url": "https://channels.weixin.qq.com/x?token=t&eid=e"}}),
        Response({"data": {"feedInfo": feed_info, "authorInfo": {}}}),
    ]
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            return responses.pop(0)
    monkeypatch.setattr(wechat.requests, "Session", Session)


def test_feed_with_different_share_code_is_rejected(monkeypatch):
    _fake_yuanbao_session(monkeypatch, {"shortUri": "OTHER", "objectId": "1"})
    with pytest.raises(wechat.WeChatUserError, match="不一致"):
        wechat.profile_yuanbao("https://weixin.qq.com/sph/ABC", "cookie")


def test_feed_with_matching_share_code_is_accepted(monkeypatch):
    _fake_yuanbao_session(monkeypatch, {"shortUri": "ABC", "objectId": "1"})
    feed, _ = wechat.profile_yuanbao("https://weixin.qq.com/sph/ABC", "cookie")
    assert feed["objectId"] == "1"


def test_missing_work_id_does_not_fall_back_to_job_id(monkeypatch, tmp_path):
    job = {"id": "noid", "url": "https://weixin.qq.com/sph/example"}
    monkeypatch.setattr(wechat, "STATE", tmp_path / "jobs.json")
    monkeypatch.setattr(wechat, "JOBS", [job])
    monkeypatch.setattr(wechat, "CANCELLATIONS", {"noid": threading.Event()})
    monkeypatch.setattr(wechat, "load_secret", lambda path: "cookie")
    monkeypatch.setattr(wechat, "profile_yuanbao", lambda *args: (
        {"videoInfo": {"url": "https://finder.video.qq.com/video", "decodeKey": 1}},
        {"nickname": "author"},
    ))
    monkeypatch.setattr(wechat, "download_stream_and_decrypt",
                        lambda url, key, path, cancel, progress: pytest.fail("download started without work id"))
    wechat.download(job, str(tmp_path))
    assert job["status"] == "failed"
    assert "作品 ID" in job["message"]
    assert not list(tmp_path.rglob("*.mp4"))


def test_failed_transfer_removes_part_file(monkeypatch, tmp_path):
    class Partial:
        status_code = 200
        headers = {"content-length": "10"}
        def raise_for_status(self):
            pass
        def iter_content(self, chunk_size):
            yield b"12345"
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def close(self):
            pass
    monkeypatch.setattr(wechat.requests, "get", lambda *args, **kwargs: Partial())
    with pytest.raises(wechat.WeChatUserError):
        wechat.download_stream_and_decrypt(
            "https://finder.video.qq.com/video", 0, tmp_path / "video.mp4", threading.Event(),
        )
    assert not list(tmp_path.glob("*.part"))
    assert not (tmp_path / "video.mp4").exists()


def test_cancel_leaves_finished_job_state_unchanged(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    job = {"id": "done1", "url": "https://weixin.qq.com/sph/A2uumghDtJ", "status": "completed", "message": "ok"}
    monkeypatch.setattr(wechat, "JOBS", [job])
    monkeypatch.setattr(wechat, "CANCELLATIONS", {})
    client = TestClient(main.app, base_url="http://localhost")
    r = client.post("/api/wechat/jobs/done1/cancel", headers={"origin": "http://localhost"})
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    assert job["status"] == "completed"


def test_unreadable_saved_cookie_gives_user_error_without_chaining(monkeypatch, tmp_path):
    secret = tmp_path / "yuanbao-cookie.txt"
    secret.write_text("x", encoding="utf-8")
    monkeypatch.setattr(wechat, "SECRET", secret)

    def boom(_path):
        raise RuntimeError("secret-detail-should-not-leak")

    monkeypatch.setattr(wechat, "load_secret", boom)
    with pytest.raises(wechat.WeChatUserError) as info:
        wechat.load_yuanbao_cookie()
    assert "secret-detail" not in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__ is True


def test_preview_then_selected_start_for_wechat(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr("app.wechat.load_secret", lambda path: "cookie")
    monkeypatch.setattr("app.wechat.profile_yuanbao", lambda url, cookie: (
        {"objectId": "work-1", "description": "title", "videoInfo": {"url": "https://finder.video.qq.com/video", "decodeKey": "7"}},
        {"nickname": "author"},
    ))
    client = TestClient(main.app, base_url="http://localhost")
    preview = client.post("/api/preview", json={"url": "https://weixin.qq.com/sph/A2uumghDtJ"})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["platform"] == "wechat"
    assert body["total_items"] == 1
    assert body["items"][0]["title"] == "title"
    assert body["items"][0]["author"] == "author"

    # 拦截真实下载与校验，任务在后台线程中完成，避免测试触网。
    monkeypatch.setattr(wechat, "download_stream_and_decrypt",
                        lambda url, key, path, cancel_event, progress_callback=None: open(path, "wb").close())
    monkeypatch.setattr(wechat, "verify_video", lambda path: True)
    monkeypatch.setattr(main, "config",
                        main.config.model_copy(update={"download_dir": str(tmp_path)}))
    # 任务状态同样隔离到临时目录，绝不写进真实的 data/state。
    from app.storage import JsonJobStore
    monkeypatch.setattr(main.manager, "store", JsonJobStore(tmp_path / "state"))

    created = client.post(
        "/api/jobs",
        json={"url": "https://weixin.qq.com/sph/A2uumghDtJ", "auto_start": False},
    )
    assert created.status_code == 201, created.text
    job = created.json()

    item_id = body["items"][0]["id"]
    started = client.post(f"/api/jobs/{job['id']}/start", json={"item_ids": [item_id]})
    assert started.status_code == 200, started.text

    import time
    deadline = time.time() + 15
    while True:
        current = client.get(f"/api/jobs/{job['id']}")
        assert current.status_code == 200, current.text
        payload = current.json()
        if payload["status"] in {"completed", "partial", "failed", "cancelled"}:
            break
        assert time.time() < deadline, "任务未在期限内结束"
        time.sleep(0.05)
    assert payload["status"] == "completed"
    assert [i["id"] for i in payload["items"] if i["status"] == "completed"] == [item_id]


def test_wechat_identity_prefers_object_id_and_falls_back_to_share_code():
    assert wechat.work_identity({"objectId": "w1"}, "https://weixin.qq.com/sph/A2uumghDtJ") == "w1"
    assert wechat.work_identity({"id": "w2"}, "https://weixin.qq.com/sph/A2uumghDtJ") == "w2"
    assert wechat.work_identity({"description": "无 ID 的返回"}, "https://weixin.qq.com/sph/A2uumghDtJ") == "sph:A2uumghDtJ"
    assert wechat.work_identity({}, "https://example.com/video?id=1") == ""


def test_browser_cookie_header_requires_login_pair_and_prefers_own_domain():
    cookies = [
        {'name': 'shared', 'value': 'parent', 'domain': '.tencent.com', 'expires': 9999999999},
        {'name': 'hy_user', 'value': 'u', 'domain': '.tencent.com', 'expires': 9999999999},
        {'name': 'hy_token', 'value': 't', 'domain': '.tencent.com', 'expires': 9999999999},
        {'name': 'shared', 'value': 'own', 'domain': 'yuanbao.tencent.com', 'expires': 9999999999},
        {'name': 'foreign', 'value': 'x', 'domain': '.qq.com', 'expires': 9999999999},
    ]
    header = wechat._browser_cookie_header(cookies)
    assert 'hy_user=u' in header
    assert 'hy_token=t' in header
    assert 'shared=own' in header
    assert 'foreign' not in header


def test_browser_cookie_header_rejects_missing_expired_or_newline_cookies():
    assert wechat._browser_cookie_header([]) == ''
    assert wechat._browser_cookie_header([{'name': 'a', 'value': 'b', 'domain': '.tencent.com', 'expires': 9999999999}]) == ''
    expired = [{'name': 'hy_user', 'value': 'u', 'domain': '.tencent.com', 'expires': 1},
               {'name': 'hy_token', 'value': 't', 'domain': '.tencent.com', 'expires': 9999999999}]
    assert wechat._browser_cookie_header(expired) == ''
    bad = [{'name': 'hy_user', 'value': 'a\nb', 'domain': '.tencent.com', 'expires': 9999999999},
           {'name': 'hy_token', 'value': 't', 'domain': '.tencent.com', 'expires': 9999999999}]
    assert wechat._browser_cookie_header(bad) == ''


def test_login_route_saves_and_does_not_expose_secret(monkeypatch):
    monkeypatch.setattr(wechat, 'login_with_browser', lambda timeout=300: {'saved': True})
    from fastapi.testclient import TestClient
    from app import main
    client = TestClient(main.app, base_url='http://localhost')
    r = client.post('/api/wechat/login', headers={'origin': 'http://localhost'})
    assert r.status_code == 200
    assert r.json() == {'saved': True}


def test_login_route_maps_user_error_without_details(monkeypatch):
    monkeypatch.setattr(wechat, 'login_with_browser', lambda timeout=300: (_ for _ in ()).throw(
        wechat.WeChatUserError('扫码登录超时，未检测到元宝登录态，请重试。')))
    from fastapi.testclient import TestClient
    from app import main
    client = TestClient(main.app, base_url='http://localhost')
    r = client.post('/api/wechat/login', headers={'origin': 'http://localhost'})
    assert r.status_code == 400
    assert '超时' in r.json()['detail']


def test_concurrent_tasks_get_distinct_staging_paths(monkeypatch, tmp_path):
    """同一作品的两个任务不得共用暂存文件（F05）。"""
    from app.downloader import DownloaderConfig, MediaDownloader

    monkeypatch.setattr(wechat, "load_yuanbao_cookie", lambda: "cookie")
    monkeypatch.setattr(wechat, "profile_yuanbao", lambda url, cookie: (
        {"objectId": "work-1", "description": "title", "videoInfo": {"url": "https://finder.video.qq.com/video", "decodeKey": "7"}},
        {"nickname": "author"},
    ))
    monkeypatch.setattr(wechat, "verify_video", lambda path: True)
    staging_paths = []

    def fake_stream(url, key, path, cancel_event, progress_callback=None):
        staging_paths.append(str(path))
        __import__("pathlib").Path(path).write_bytes(b"fake")

    monkeypatch.setattr(wechat, "download_stream_and_decrypt", fake_stream)
    engine = MediaDownloader(config=DownloaderConfig())
    from app.models import DownloadItem, MediaType

    def run(job_id: str, title: str):
        item = DownloadItem(
            id="item-1", media_id="work-1", source_url="https://weixin.qq.com/sph/A2uumghDtJ",
            title=title, author="author", media_type=MediaType.VIDEO,
            metadata={"_job_id": job_id},
        )
        return engine._download_wechat_item(item, tmp_path, callback=None, should_cancel=lambda: False)

    run("job-aaa", "任务一")
    run("job-bbb", "任务二")
    assert len(staging_paths) == 2
    assert staging_paths[0] != staging_paths[1], "不同任务的暂存路径必须互不相同"


def test_login_route_rejects_second_concurrent_login(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(wechat, 'login_with_browser', lambda timeout=300: (_ for _ in ()).throw(
        AssertionError('并发时应直接拒绝，不应再次启动登录窗口')))
    monkeypatch.setattr(wechat, '_LOGIN_LOCK', __import__('threading').Lock())
    with wechat._LOGIN_LOCK:
        client = TestClient(main.app, base_url='http://localhost')
        r = client.post('/api/wechat/login', headers={'origin': 'http://localhost'})
    assert r.status_code == 409
    assert '已有一个扫码登录窗口' in r.json()['detail']


def test_unknown_total_stream_reports_bytes_and_cancels_early(monkeypatch, tmp_path):
    """无 Content-Length 的流也必须逐块上报进度并响应取消（F06）。"""
    class FakeResponse:
        status_code = 200
        headers = {}
        def raise_for_status(self): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def iter_content(self, chunk_size):
            for i in range(5):
                reads.append(i + 1)
                yield b"x" * 65536
        def close(self): pass

    reads = []
    progress = []
    monkeypatch.setattr(wechat.requests, "get", lambda *a, **k: FakeResponse())

    def cancel_after_first_chunk(downloaded, total):
        progress.append((downloaded, total))
        if downloaded >= 65536:
            cancel_event.set()

    cancel_event = threading.Event()
    out = tmp_path / "video.mp4"
    with pytest.raises(ValueError, match="已取消"):
        wechat.download_stream_and_decrypt(
            "https://finder.video.qq.com/video", 7, out, cancel_event, cancel_after_first_chunk,
        )
    assert len(reads) < 5, "取消后不得继续读完整个流"
    assert progress, "未知总大小时也必须上报已读字节"
    assert all(total is None for _, total in progress)
    assert not list(tmp_path.rglob("*.part")), "取消后不得留下 .part 临时文件"


def test_trusted_media_url_rejects_injection_and_oversize():
    ok = "https://finder.video.qq.com/video?token=1"
    assert wechat._trusted_media_url(ok) == ok
    bad_urls = [
        "http://finder.video.qq.com/video",
        "ftp://finder.video.qq.com/video",
        "https://finder.video.qq.com.evil.com/video",
        "https://user:pw@finder.video.qq.com/video",
        "https://finder.video.qq.com:8443/video",
        "https://finder.video.qq.com/video\nEvil: 1",
        "https://finder.video.qq.com/\tvideo",
        "https://finder.video.qq.com/" + "a" * 9000,
        "",
        None,
    ]
    for bad in bad_urls:
        with pytest.raises(wechat.WeChatUserError):
            wechat._trusted_media_url(bad)


def test_status_reports_chrome_fallback_without_reading_cookies(monkeypatch, tmp_path):
    monkeypatch.setattr(wechat, "SECRET", tmp_path / "missing.txt")
    monkeypatch.setattr(wechat, "extract_cookies_from_browser", lambda *a, **k: pytest.fail("status must not read Chrome"))
    result = wechat.status()
    assert result["cookie_configured"] is False
    assert result["chrome_fallback"] is True


def test_login_route_maps_playwright_failure_to_user_error(monkeypatch):
    """用户中途关闭登录窗口（Playwright 异常）应得到 400 用户提示而非 500。"""

    class _FakePage:
        def goto(self, url, timeout=None):
            return None

        def wait_for_timeout(self, ms):
            return None

    class _FakeContext:
        def new_page(self):
            return _FakePage()

        def cookies(self, *args, **kwargs):
            from playwright.sync_api import Error as PlaywrightError
            raise PlaywrightError(
                "Target page, context or browser has been closed"
            )

    class _FakeBrowser:
        def new_context(self):
            return _FakeContext()

        def close(self):
            return None

    class _FakeChromium:
        def launch(self, **kwargs):
            return _FakeBrowser()

    class _FakePlaywright:
        def __init__(self):
            self.chromium = _FakeChromium()

    class _FakeSyncPlaywright:
        def __enter__(self):
            return _FakePlaywright()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("playwright.sync_api.sync_playwright", _FakeSyncPlaywright)
    from fastapi.testclient import TestClient
    from app import main
    client = TestClient(main.app, base_url='http://localhost')
    r = client.post('/api/wechat/login', headers={'origin': 'http://localhost'})
    assert r.status_code == 400
    assert '窗口' in r.json()['detail']


def test_legacy_discovering_record_is_migrated_and_never_zombie():
    """旧版 discovering 记录启动即迁移为 interrupted：cancel 不再制造
    永远 cancelling 的僵尸、delete 也不再永久 409。"""
    jobs = [{'id': 'legacy-disc', 'status': 'discovering',
             'url': 'https://weixin.qq.com/sph/A2uumghDtJ'}]

    wechat._migrate_stale_wechat_records(jobs)

    assert jobs[0]['status'] == 'interrupted'
    assert jobs[0]['message'] == '上次运行已中断，请重新提交链接。'


def test_corrupt_plaintext_secret_is_quarantined(tmp_path):
    """无前缀的二进制坏文件必须与损坏密文同样隔离，
    否则 status() 持续谎报登录态已配置而每次下载都失败。"""
    from app import wechat_secret

    secret = tmp_path / "yuanbao-cookie.txt"
    secret.write_bytes(b"\xff\xfe\x00garbage")

    import pytest
    with pytest.raises(OSError, match="隔离"):
        wechat_secret.load_secret(secret)

    assert not secret.exists()
    assert secret.with_name(secret.name + ".corrupt").exists()
