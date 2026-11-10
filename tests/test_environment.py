from fastapi.testclient import TestClient

from app import environment, main


def test_environment_check_reports_actionable_missing_dependencies(monkeypatch):
    monkeypatch.setattr(environment.MediaDownloader, "_find_ffmpeg_executable", lambda: None)
    monkeypatch.setattr(environment.MediaDownloader, "_find_ffprobe_executable", lambda: "ffprobe")
    monkeypatch.setattr(environment, "_starts", lambda executable: executable == "ffprobe")
    monkeypatch.setattr(environment, "_chrome_path", lambda: None)
    monkeypatch.setattr(environment, "_writable", lambda directory: False)
    monkeypatch.setattr(environment, "_package_version", lambda name: "1.0" if name == "yt-dlp" else None)
    checks = {check["id"]: check for check in environment.check_environment("D:/missing")["checks"]}
    assert {name for name, check in checks.items() if not check["ok"]} == {"ffmpeg", "chrome", "download_dir", "playwright"}
    assert "权限" in checks["download_dir"]["message"]
    assert checks["ffprobe"]["ok"]


def test_environment_probe_reports_chrome_launch_failure(monkeypatch):
    monkeypatch.setattr(environment.MediaDownloader, "_find_ffmpeg_executable", lambda: "ffmpeg")
    monkeypatch.setattr(environment.MediaDownloader, "_find_ffprobe_executable", lambda: "ffprobe")
    monkeypatch.setattr(environment, "_starts", lambda executable: True)
    monkeypatch.setattr(environment, "_chrome_path", lambda: "C:/fake/chrome.exe")
    monkeypatch.setattr(environment, "_writable", lambda directory: True)
    monkeypatch.setattr(environment, "_package_version", lambda name: "1.0")
    monkeypatch.setattr(environment, "_chrome_launches", lambda executable: (False, "launch failed (timeout)"))
    monkeypatch.setattr(environment, "_chrome_launch_cache", None)
    checks = {check["id"]: check for check in environment.check_environment("D:/tmp")["checks"]}
    assert checks["chrome"]["ok"] is True
    assert checks["chrome_launch"]["ok"] is False
    assert "无法启动" in checks["chrome_launch"]["message"]
    assert "launch failed" in checks["chrome_launch"]["message"]


def test_environment_chrome_launch_result_is_cached(monkeypatch):
    calls = []

    def fake_launch(executable):
        calls.append(executable)
        return True, ""

    monkeypatch.setattr(environment, "_chrome_launches", fake_launch)
    monkeypatch.setattr(environment, "_chrome_launch_cache", None)
    monkeypatch.setattr(environment, "_chrome_path", lambda: "C:/fake/chrome.exe")
    environment.check_environment("D:/tmp")
    environment.check_environment("D:/tmp")
    assert len(calls) == 1


class _FakePage:
    def goto(self, url, timeout=None):
        assert url == "about:blank"


class _FakeBrowser:
    def __init__(self):
        self.closed = False
        self.launch_kwargs = None

    def new_page(self):
        return _FakePage()

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    def launch(self, **kwargs):
        self._browser.launch_kwargs = kwargs
        return self._browser


class _FakePlaywright:
    def __init__(self, browser):
        self.chromium = _FakeChromium(browser)


def test_chrome_launch_probe_uses_playwright_with_given_executable(monkeypatch):
    browser = _FakeBrowser()
    holder = {}

    class _FakeSyncPlaywright:
        def __init__(self):
            self._browser = browser

        def __enter__(self):
            holder["playwright"] = _FakePlaywright(self._browser)
            return holder["playwright"]

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("playwright.sync_api.sync_playwright", _FakeSyncPlaywright)
    ok, reason = environment._chrome_launches("C:/fake/chrome.exe")
    assert ok is True
    assert reason == ""
    assert browser.launch_kwargs["executable_path"] == "C:/fake/chrome.exe"
    assert browser.launch_kwargs["headless"] is True
    assert browser.closed is True


def test_chrome_launch_probe_reports_playwright_failure_sanitized(monkeypatch):
    class _BrokenSyncPlaywright:
        def __enter__(self):
            raise RuntimeError("browser closed unexpectedly at C:/secret/profile")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("playwright.sync_api.sync_playwright", _BrokenSyncPlaywright)
    ok, reason = environment._chrome_launches("C:/fake/chrome.exe")
    assert ok is False
    assert reason  # 原因被记录
    assert len(reason) <= 200


def test_directory_check_preserves_existing_files(tmp_path):
    existing = tmp_path / "important.txt"
    existing.write_text("keep", encoding="utf-8")
    assert environment._writable(str(tmp_path))
    assert existing.read_text(encoding="utf-8") == "keep"
    assert list(tmp_path.iterdir()) == [existing]
    assert not environment._writable(str(tmp_path / "missing"))


def test_environment_endpoint_uses_configured_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "get_config", lambda: main.AppConfig(download_dir=str(tmp_path)))
    monkeypatch.setattr(main, "check_environment", lambda directory: {"checks": [{"id": directory, "ok": True}]})
    response = TestClient(main.app, base_url="http://localhost").get("/api/environment")
    assert response.status_code == 200
    assert response.json()["checks"][0]["id"] == str(tmp_path)
