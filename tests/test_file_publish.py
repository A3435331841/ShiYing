import errno
import stat
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app import file_publish
from app.file_publish import (
    are_files_identical,
    publish_no_replace,
    publish_or_reuse_identical,
    publish_pair_image,
    publish_unique,
)
from app.downloader import MediaDownloader
from app.models import Platform


def test_concurrent_publish_preserves_all_files(tmp_path):
    target = tmp_path / "001.webp"
    target.write_bytes(b"original")
    barrier = Barrier(2)

    def publish(number):
        source = tmp_path / f".{number}.part"
        source.write_text(str(number))
        barrier.wait(timeout=5)
        return publish_unique(source, target)

    with ThreadPoolExecutor(max_workers=2) as executor:
        paths = list(executor.map(publish, (1, 2)))

    assert target.read_bytes() == b"original"
    assert len(set(paths)) == 2
    assert {path.read_text() for path in paths} == {"1", "2"}
    assert not list(tmp_path.glob("*.part"))


def test_pair_skips_partial_old_versions_and_preserves_basename(tmp_path):
    (tmp_path / "001.mp4").write_bytes(b"old motion")
    (tmp_path / "001 (2).jpg").write_bytes(b"old image")
    image_temp = tmp_path / ".image.part"
    motion_temp = tmp_path / ".motion.part"
    image_temp.write_bytes(b"new image")
    motion_temp.write_bytes(b"new motion")

    image = publish_pair_image(image_temp, tmp_path / "001.heic")
    motion = image.with_suffix(".mp4")
    publish_no_replace(motion_temp, motion)

    assert image.name == "001 (3).heic"
    assert motion.name == "001 (3).mp4"
    assert (tmp_path / "001.mp4").read_bytes() == b"old motion"
    assert (tmp_path / "001 (2).jpg").read_bytes() == b"old image"


def test_concurrent_pair_images_with_different_formats_use_distinct_stems(tmp_path):
    barrier = Barrier(2)

    def publish(extension):
        source = tmp_path / f".{extension}.part"
        source.write_bytes(extension.encode())
        barrier.wait(timeout=5)
        return publish_pair_image(source, tmp_path / f"001.{extension}")

    with ThreadPoolExecutor(max_workers=2) as executor:
        images = list(executor.map(publish, ("jpg", "heic")))
    assert images[0].stem != images[1].stem


def test_compact_album_names_do_not_overwrite(tmp_path):
    engine = MediaDownloader()
    assert engine._xhs_output_path(
        tmp_path, "2026-01-01", "a long title", "id", "heic", 3,
        Platform.XIAOHONGSHU, compact_names=True,
    ).name == "003.heic"
    (tmp_path / "003.webp").write_bytes(b"old")
    temporary = tmp_path / ".verified.part"
    temporary.write_bytes(b"new")
    result = engine._publish_kuaishou_asset(
        temporary, tmp_path, "2026-01-01", "long title", "id", "webp", 3,
        should_cancel=lambda: False, compact_names=True,
    )
    assert result.name == "003 (2).webp"
    assert (tmp_path / "003.webp").read_bytes() == b"old"


def test_are_files_identical_detects_matching_and_differing_files(tmp_path):
    f1 = tmp_path / "a.bin"
    f2 = tmp_path / "b.bin"
    f3 = tmp_path / "c.bin"
    f1.write_bytes(b"hello world")
    f2.write_bytes(b"hello world")
    f3.write_bytes(b"other world")

    assert are_files_identical(f1, f2) is True
    assert are_files_identical(f1, f3) is False
    assert are_files_identical(f1, tmp_path / "non_existent.bin") is False

    empty1 = tmp_path / "empty1.bin"
    empty2 = tmp_path / "empty2.bin"
    empty1.write_bytes(b"")
    empty2.write_bytes(b"")
    # 0-byte files should not be considered identical valid media files
    assert are_files_identical(empty1, empty2) is False


def test_publish_or_reuse_identical_reuses_existing_without_suffix(tmp_path):
    destination = tmp_path / "2024-05-18-Video [BV123].mp4"
    destination.write_bytes(b"completed video stream payload")

    temporary = tmp_path / ".temp-download.part"
    temporary.write_bytes(b"completed video stream payload")

    published = publish_or_reuse_identical(temporary, destination)
    # Must reuse original destination instead of creating (2)
    assert published == destination
    assert published.name == "2024-05-18-Video [BV123].mp4"
    assert not temporary.exists()
    assert not (tmp_path / "2024-05-18-Video [BV123] (2).mp4").exists()


def test_publish_or_reuse_identical_creates_unique_when_content_differs(tmp_path):
    destination = tmp_path / "2024-05-18-Video [BV123].mp4"
    destination.write_bytes(b"original 720p content")

    temporary = tmp_path / ".temp-download-1080p.part"
    temporary.write_bytes(b"new 1080p higher quality content")

    published = publish_or_reuse_identical(temporary, destination)
    # Content differs, so it must create (2) to preserve original file
    assert published.name == "2024-05-18-Video [BV123] (2).mp4"
    assert destination.read_bytes() == b"original 720p content"
    assert published.read_bytes() == b"new 1080p higher quality content"
    assert not temporary.exists()


def test_publish_or_reuse_identical_reuses_existing_variant_if_matching(tmp_path):
    v1 = tmp_path / "video.mp4"
    v1.write_bytes(b"720p")
    v2 = tmp_path / "video (2).mp4"
    v2.write_bytes(b"1080p")

    # Downloading 1080p again should reuse video (2).mp4 and NOT create video (3).mp4
    temporary = tmp_path / ".temp.part"
    temporary.write_bytes(b"1080p")

    published = publish_or_reuse_identical(temporary, v1)
    assert published == v2
    assert published.name == "video (2).mp4"
    assert not (tmp_path / "video (3).mp4").exists()
    assert not temporary.exists()


def test_publish_or_reuse_identical_finds_match_past_gap(tmp_path):
    destination = tmp_path / "video.mp4"
    destination.write_bytes(b"different version")
    match = tmp_path / "video (3).mp4"
    match.write_bytes(b"matching version")
    temporary = tmp_path / ".download.part"
    temporary.write_bytes(b"matching version")

    assert publish_or_reuse_identical(temporary, destination) == match
    assert not (tmp_path / "video (2).mp4").exists()
    assert destination.read_bytes() == b"different version"
    assert not temporary.exists()


def test_publish_or_reuse_identical_finds_match_after_99(tmp_path):
    destination = tmp_path / "video.mp4"
    destination.write_bytes(b"original")
    for number in range(2, 101):
        (tmp_path / f"video ({number}).mp4").write_bytes(f"old {number}".encode())
    match = tmp_path / "video (101).mp4"
    match.write_bytes(b"new variant")
    temporary = tmp_path / ".download.part"
    temporary.write_bytes(b"new variant")

    assert publish_or_reuse_identical(temporary, destination) == match
    assert not (tmp_path / "video (102).mp4").exists()
    assert not temporary.exists()


def test_publish_or_reuse_identical_finds_match_at_allocation_limit(tmp_path):
    destination = tmp_path / "video.mp4"
    destination.write_bytes(b"original")
    match = tmp_path / "video (10001).mp4"
    match.write_bytes(b"matching version")
    temporary = tmp_path / ".download.part"
    temporary.write_bytes(b"matching version")

    assert publish_or_reuse_identical(temporary, destination) == match
    assert not (tmp_path / "video (2).mp4").exists()
    assert not temporary.exists()


def test_publish_or_reuse_identical_preserves_large_file_with_different_middle(tmp_path):
    destination = tmp_path / "video.mp4"
    prefix = b"a" * (1024 * 1024)
    suffix = b"z" * (1024 * 1024)
    original = prefix + b"x" + suffix
    downloaded = prefix + b"y" + suffix
    destination.write_bytes(original)
    temporary = tmp_path / ".download.part"
    temporary.write_bytes(downloaded)

    assert not are_files_identical(temporary, destination)
    published = publish_or_reuse_identical(temporary, destination)
    assert published == tmp_path / "video (2).mp4"
    assert destination.read_bytes() == original
    assert published.read_bytes() == downloaded
    assert not temporary.exists()


def test_publish_or_reuse_identical_concurrent_identical_publishes(tmp_path, monkeypatch):
    destination = tmp_path / "video.mp4"
    barrier = Barrier(2)
    real_publish = file_publish.publish_no_replace

    def synchronized_publish(temporary, candidate):
        if candidate == destination:
            barrier.wait(timeout=30)
        return real_publish(temporary, candidate)

    monkeypatch.setattr(file_publish, "publish_no_replace", synchronized_publish)

    def publish(number):
        temporary = tmp_path / f".{number}.part"
        temporary.write_bytes(b"same verified download")
        return publish_or_reuse_identical(temporary, destination)

    with ThreadPoolExecutor(max_workers=2) as executor:
        published = list(executor.map(publish, (1, 2)))

    assert published == [destination, destination]
    assert destination.read_bytes() == b"same verified download"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["video.mp4"]


def test_publish_or_reuse_identical_preserves_different_race_winner(tmp_path, monkeypatch):
    destination = tmp_path / "video.mp4"
    temporary = tmp_path / ".download.part"
    temporary.write_bytes(b"our download")
    real_publish = file_publish.publish_no_replace

    def competing_publish(source, candidate):
        if candidate == destination:
            candidate.write_bytes(b"other download")
        return real_publish(source, candidate)

    monkeypatch.setattr(file_publish, "publish_no_replace", competing_publish)
    published = publish_or_reuse_identical(temporary, destination)

    assert destination.read_bytes() == b"other download"
    assert published == tmp_path / "video (2).mp4"
    assert published.read_bytes() == b"our download"
    assert not temporary.exists()


def test_publish_or_reuse_identical_preserves_link_race(tmp_path, monkeypatch):
    destination = tmp_path / "video.mp4"
    temporary = tmp_path / ".download.part"
    temporary.write_bytes(b"same download")
    real_publish = file_publish.publish_no_replace
    path_type = type(destination)
    real_is_symlink = path_type.is_symlink

    def competing_link(source, candidate):
        if candidate == destination:
            candidate.write_bytes(b"same download")
        return real_publish(source, candidate)

    # Simulate link metadata so the collision path is tested even on Windows
    # installations where creating real symbolic links requires privileges.
    def is_symlink(path):
        return path == destination or real_is_symlink(path)

    monkeypatch.setattr(file_publish, "publish_no_replace", competing_link)
    monkeypatch.setattr(path_type, "is_symlink", is_symlink)
    published = publish_or_reuse_identical(temporary, destination)

    assert destination.read_bytes() == b"same download"
    assert published == tmp_path / "video (2).mp4"
    assert published.read_bytes() == b"same download"
    assert not temporary.exists()


def test_publish_or_reuse_identical_preserves_symlinks(tmp_path):
    target = tmp_path / "existing.bin"
    target.write_bytes(b"same verified download")
    destination = tmp_path / "video.mp4"
    dangling = tmp_path / "video (2).mp4"
    missing_target = tmp_path / "missing.bin"
    try:
        destination.symlink_to(target)
        dangling.symlink_to(missing_target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")
    temporary = tmp_path / ".download.part"
    temporary.write_bytes(b"same verified download")

    published = publish_or_reuse_identical(temporary, destination)

    assert published == tmp_path / "video (3).mp4"
    assert published.read_bytes() == b"same verified download"
    assert destination.is_symlink()
    assert dangling.is_symlink()
    assert target.read_bytes() == b"same verified download"
    assert not missing_target.exists()
    assert not temporary.exists()


def test_publish_or_reuse_identical_stops_at_allocation_limit(tmp_path, monkeypatch):
    destination = tmp_path / "video.mp4"
    temporary = tmp_path / ".download.part"
    temporary.write_bytes(b"our download")
    attempted = []

    def occupied(_temporary, candidate):
        attempted.append(candidate)
        raise FileExistsError

    monkeypatch.setattr(file_publish, "publish_no_replace", occupied)
    with pytest.raises(FileExistsError, match="Could not find an unused output filename"):
        publish_or_reuse_identical(temporary, destination)

    assert len(attempted) == 10001
    assert attempted[0] == destination
    assert attempted[-1] == tmp_path / "video (10001).mp4"
    assert temporary.read_bytes() == b"our download"


@pytest.mark.parametrize("windows_error", [False, True], ids=["errno", "winerror"])
def test_publish_or_reuse_identical_handles_long_numbered_names(tmp_path, monkeypatch, windows_error):
    destination = tmp_path / "video.mp4"
    temporary = tmp_path / ".download.part"
    temporary.write_bytes(b"our download")
    path_type = type(destination)
    real_lstat = path_type.lstat
    probed = []

    def limited_lstat(path):
        probed.append(path.name)
        if path.name == "video (10).mp4":
            if windows_error:
                error = FileNotFoundError(errno.ENOENT, "filename too long")
                error.winerror = 206
                raise error
            raise OSError(errno.ENAMETOOLONG, "filename too long")
        return real_lstat(path)

    monkeypatch.setattr(path_type, "lstat", limited_lstat)
    published = publish_or_reuse_identical(temporary, destination)

    assert published == destination
    assert destination.read_bytes() == b"our download"
    assert "video (11).mp4" not in probed


def test_published_file_gets_world_readable_permissions_on_posix(
    tmp_path, monkeypatch
):
    """mkstemp 产物 0600：POSIX 上发布后应为 0644（同机其他用户可读）。"""
    import os

    from app import file_publish

    monkeypatch.setattr(file_publish.os, "name", "posix")
    source = tmp_path / "staged.mp4"
    source.write_bytes(b"published media")
    destination = tmp_path / "final.mp4"

    file_publish.publish_no_replace(source, destination)

    mode = stat.S_IMODE(destination.stat().st_mode)
    # 平台对位的确切映射不同（Windows 只区分只读位）；要验证的是
    # mkstemp 的 0600 默认已被覆盖、属主可写保留。
    assert mode != 0o600
    assert mode & 0o200
    assert not source.exists()


def test_published_permissions_best_effort_on_chmod_failure(
    tmp_path, monkeypatch
):
    """chmod 失败不得让已成功的发布报错。"""
    import os

    from app import file_publish

    monkeypatch.setattr(file_publish.os, "name", "posix")

    def failing_chmod(path, mode):
        raise PermissionError("read-only volume")

    monkeypatch.setattr(file_publish.os, "chmod", failing_chmod)
    source = tmp_path / "staged.mp4"
    source.write_bytes(b"published media")
    destination = tmp_path / "final.mp4"

    file_publish.publish_no_replace(source, destination)

    assert destination.read_bytes() == b"published media"
