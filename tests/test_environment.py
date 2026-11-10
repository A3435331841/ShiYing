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
