from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


def load_release_module(name: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / "release" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"shiying_release_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configure_ffmpeg_paths(module, tmp_path: Path) -> tuple[Path, Path]:
    cache = tmp_path / "cache"
    lock = tmp_path / "ffmpeg.lock.json"
    module.FFMPEG_CACHE = cache
    module.FFMPEG_LOCK = lock
    return cache, lock


def make_zip(path: Path) -> dict[str, str]:
    binaries = {}
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in (("ffmpeg.exe", b"ffmpeg"), ("ffprobe.exe", b"ffprobe")):
            archive.writestr(f"ffmpeg/bin/{name}", payload)
            binaries[name] = hashlib.sha256(payload).hexdigest()
    return binaries


def test_skip_ffmpeg_verifies_both_cached_binaries(tmp_path, monkeypatch) -> None:
    module = load_release_module("build_exe")
    cache, lock = configure_ffmpeg_paths(module, tmp_path)
    cache.mkdir()
    (cache / "ffmpeg.exe").write_bytes(b"ffmpeg")
    (cache / "ffprobe.exe").write_bytes(b"ffprobe")
    lock.write_text(
        json.dumps(
            {
                "url": "https://example.invalid/ffmpeg.zip",
                "sha256": "a" * 64,
                "binaries": {
                    "ffmpeg.exe": digest(cache / "ffmpeg.exe"),
                    "ffprobe.exe": digest(cache / "ffprobe.exe"),
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module.urllib.request,
        "urlretrieve",
        lambda *args: pytest.fail("skip mode must not download"),
    )

    module.prepare_ffmpeg(None, True, False)


def test_skip_ffmpeg_rejects_tampered_cache(tmp_path) -> None:
    module = load_release_module("build_exe")
    cache, lock = configure_ffmpeg_paths(module, tmp_path)
    cache.mkdir()
    (cache / "ffmpeg.exe").write_bytes(b"tampered")
    (cache / "ffprobe.exe").write_bytes(b"ffprobe")
    lock.write_text(
        json.dumps(
            {
                "url": "https://example.invalid/ffmpeg.zip",
                "sha256": "a" * 64,
                "binaries": {
                    "ffmpeg.exe": "b" * 64,
                    "ffprobe.exe": digest(cache / "ffprobe.exe"),
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="校验和不匹配"):
        module.prepare_ffmpeg(None, True, False)


def test_skip_ffmpeg_rejects_legacy_lock_without_binary_hashes(tmp_path) -> None:
    module = load_release_module("build_exe")
    cache, lock = configure_ffmpeg_paths(module, tmp_path)
    cache.mkdir()
    (cache / "ffmpeg.exe").write_bytes(b"ffmpeg")
    (cache / "ffprobe.exe").write_bytes(b"ffprobe")
    lock.write_text(
        json.dumps({"url": "https://example.invalid/ffmpeg.zip", "sha256": "a" * 64}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="缺少完整"):
        module.prepare_ffmpeg(None, True, False)


def test_custom_ffmpeg_zip_requires_sha256(tmp_path) -> None:
    module = load_release_module("build_exe")
    configure_ffmpeg_paths(module, tmp_path)
    archive = tmp_path / "custom.zip"
    make_zip(archive)

    with pytest.raises(SystemExit, match="必须同时提供"):
        module.prepare_ffmpeg(archive, False, False)


def test_custom_ffmpeg_zip_rejects_wrong_sha256(tmp_path) -> None:
    module = load_release_module("build_exe")
    configure_ffmpeg_paths(module, tmp_path)
    archive = tmp_path / "custom.zip"
    make_zip(archive)

    with pytest.raises(SystemExit, match="SHA256 不匹配"):
        module.prepare_ffmpeg(archive, False, False, "a" * 64)
    assert not (tmp_path / "cache" / "ffmpeg.exe").exists()


def test_custom_ffmpeg_zip_extracts_only_after_hash_verification(tmp_path) -> None:
    module = load_release_module("build_exe")
    cache, _ = configure_ffmpeg_paths(module, tmp_path)
    archive = tmp_path / "custom.zip"
    expected = make_zip(archive)

    module.prepare_ffmpeg(archive, False, False, digest(archive))

    assert {name: digest(cache / name) for name in module.FFMPEG_BINARY_NAMES} == expected


def test_source_package_excludes_build_and_worktrees(tmp_path, monkeypatch) -> None:
    module = load_release_module("package_release")
    root = tmp_path / "repo"
    releases = root / "releases"
    for directory in ("src", "build", "worktrees/example", "data/state"):
        path = root / directory
        path.mkdir(parents=True)
        (path / "marker.txt").write_text(directory, encoding="utf-8")
    (root / "data" / "capabilities.json").write_text("{}", encoding="utf-8")
    (root / "data" / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "RELEASES_DIR", releases)

    archive_path = module.package_release("9.9.9")

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert "src/marker.txt" in names
    assert "data/capabilities.json" in names
    assert "data/config.json" not in names
    assert "data/state/marker.txt" not in names
    assert not any(name.startswith(("build/", "worktrees/")) for name in names)


def test_repin_refreshes_ffmpeg_zip_and_binary_hashes(tmp_path, monkeypatch) -> None:
    module = load_release_module("build_exe")
    cache, lock = configure_ffmpeg_paths(module, tmp_path)
    cache.mkdir()
    lock.write_text(
        json.dumps(
            {
                "url": "https://example.invalid/old.zip",
                "sha256": "a" * 64,
                "binaries": {
                    "ffmpeg.exe": "b" * 64,
                    "ffprobe.exe": "c" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    def download(_url, destination) -> None:
        make_zip(Path(destination))

    monkeypatch.setattr(module.urllib.request, "urlretrieve", download)

    module.prepare_ffmpeg(None, False, True)

    updated = json.loads(lock.read_text(encoding="utf-8"))
    assert updated["sha256"] == digest(cache / "ffmpeg-download.zip")
    assert updated["sha256"] != "a" * 64
    assert updated["binaries"]["ffmpeg.exe"] == digest(cache / "ffmpeg.exe")
    assert updated["binaries"]["ffprobe.exe"] == digest(cache / "ffprobe.exe")


def test_frozen_spec_packages_capability_registry() -> None:
    spec = Path(__file__).resolve().parents[1] / "scripts" / "release" / "shiying.spec"
    content = spec.read_text(encoding="utf-8")
    assert '(str(REPO_ROOT / "data" / "capabilities.json"), "data")' in content


def test_find_iscc_uses_program_files_environment(tmp_path, monkeypatch) -> None:
    module = load_release_module("build_exe")
    iscc = tmp_path / "Inno Setup 6" / "ISCC.exe"
    iscc.parent.mkdir()
    iscc.write_bytes(b"iscc")
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "missing"))
    monkeypatch.setattr(module.shutil, "which", lambda name: None)

    assert module.find_iscc(None) == iscc
