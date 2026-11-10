"""Regression tests for publishing verified Kuaishou media without duplicates."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from app.downloader import MediaDownloader
from app.engine import platform_items
from app.errors import DownloadCancelledError, TemporaryAccessError


def publish(engine, temporary, output_dir, *, compact=False, should_cancel=lambda: False):
    return engine._publish_kuaishou_asset(
        temporary,
        output_dir,
        "2026-09-30",
        "Same [literal] title",
        "video-id",
        "jpg" if compact else "mp4",
        7 if compact else None,
        should_cancel=should_cancel,
        compact_names=compact,
    )


def names(compact):
    if compact:
        return "007.jpg", "007 (2).jpg", "007 (3).jpg"
    base = "2026-09-30-Same [literal] title"
    return f"{base}.mp4", f"{base} [video-id].mp4", f"{base} [video-id-2].mp4"


@pytest.mark.parametrize("compact", [False, True], ids=["readable", "compact"])
@pytest.mark.parametrize("gap", [0, 1], ids=["base-gap", "numbered-gap"])
def test_reuses_identical_candidate_after_a_gap(tmp_path, compact, gap):
    candidates = [tmp_path / name for name in names(compact)]
    if gap:
        candidates[0].write_bytes(b"different existing media")
    existing = candidates[gap + 1]
    existing.write_bytes(b"already verified media")
    temporary = tmp_path / ".verified.part"
    temporary.write_bytes(b"already verified media")

    result = publish(MediaDownloader(), temporary, tmp_path, compact=compact)

    assert result == existing
    assert existing.read_bytes() == b"already verified media"
    assert not candidates[gap].exists()
    assert not temporary.exists()
    if gap:
        assert candidates[0].read_bytes() == b"different existing media"


@pytest.mark.parametrize("compact", [False, True], ids=["readable", "compact"])
def test_concurrent_identical_writers_reuse_collision_winner(tmp_path, monkeypatch, compact):
    base, winner_name, _ = names(compact)
    (tmp_path / base).write_bytes(b"different existing media")
    sources = [tmp_path / ".first.part", tmp_path / ".second.part"]
    for source in sources:
        source.write_bytes(b"identical verified media")

    ready = Barrier(2)
    collided = Event()
    real_publish = platform_items.publish_no_replace

    def synchronized_publish(source, destination):
        if destination.name == winner_name:
            ready.wait(timeout=10)
        try:
            return real_publish(source, destination)
        except FileExistsError:
            if destination.name == winner_name:
                collided.set()
            raise

    monkeypatch.setattr(platform_items, "publish_no_replace", synchronized_publish)
    engine = MediaDownloader()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish, engine, source, tmp_path, compact=compact) for source in sources]
        results = [future.result(timeout=20) for future in futures]

    assert collided.is_set(), "the test must exercise the publication race"
    assert results == [tmp_path / winner_name] * 2
    assert results[0].read_bytes() == b"identical verified media"
    assert (tmp_path / base).read_bytes() == b"different existing media"
    assert not any(source.exists() for source in sources)
    assert {path.name for path in tmp_path.iterdir()} == {base, winner_name}


@pytest.mark.parametrize("compact", [False, True], ids=["readable", "compact"])
def test_different_middle_bytes_are_never_reused_or_overwritten(tmp_path, compact):
    base, second, third = (tmp_path / name for name in names(compact))
    prefix = b"a" * (1024 * 1024)
    suffix = b"z" * (1024 * 1024)
    original = prefix + b"1" + suffix
    other = prefix + b"2" + suffix
    incoming = prefix + b"3" + suffix
    base.write_bytes(original)
    second.write_bytes(other)
    temporary = tmp_path / ".verified.part"
    temporary.write_bytes(incoming)

    result = publish(MediaDownloader(), temporary, tmp_path, compact=compact)

    assert result == third
    assert base.read_bytes() == original
    assert second.read_bytes() == other
    assert third.read_bytes() == incoming
    assert not temporary.exists()


@pytest.mark.parametrize("compact", [False, True], ids=["readable", "compact"])
@pytest.mark.parametrize("link_index", [0, 2], ids=["base", "later-candidate"])
def test_identical_symlinks_are_not_reused(tmp_path, compact, link_index):
    candidates = [tmp_path / name for name in names(compact)]
    target = tmp_path / "other-user-file"
    target.write_bytes(b"same content")
    try:
        candidates[link_index].symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    temporary = tmp_path / ".verified.part"
    temporary.write_bytes(b"same content")

    result = publish(MediaDownloader(), temporary, tmp_path, compact=compact)

    assert result == candidates[1 if link_index == 0 else 0]
    assert not result.is_symlink()
    assert result.read_bytes() == b"same content"
    assert candidates[link_index].is_symlink()
    assert target.read_bytes() == b"same content"


def test_cancellation_keeps_temporary_and_existing_files(tmp_path):
    base = tmp_path / names(False)[0]
    base.write_bytes(b"existing media")
    temporary = tmp_path / ".verified.part"
    temporary.write_bytes(b"new media")

    with pytest.raises(DownloadCancelledError):
        publish(MediaDownloader(), temporary, tmp_path, should_cancel=lambda: True)

    assert base.read_bytes() == b"existing media"
    assert temporary.read_bytes() == b"new media"


def test_publish_error_is_reported_without_losing_temporary(tmp_path, monkeypatch):
    temporary = tmp_path / ".verified.part"
    temporary.write_bytes(b"new media")

    def fail_publish(source, destination):
        raise PermissionError("simulated output filesystem failure")

    monkeypatch.setattr(platform_items, "publish_no_replace", fail_publish)
    with pytest.raises(TemporaryAccessError, match="could not safely publish") as error:
        publish(MediaDownloader(), temporary, tmp_path)

    assert isinstance(error.value.__cause__, PermissionError)
    assert temporary.read_bytes() == b"new media"


@pytest.mark.parametrize("compact", [False, True], ids=["readable", "compact"])
def test_noncanonical_collision_names_are_not_reused(tmp_path, compact):
    if compact:
        lookalikes = ["007 (1).jpg", "007 (02).jpg", "007 (10002).jpg"]
    else:
        base = "2026-09-30-Same [literal] title"
        lookalikes = [
            f"{base} [video-id-1].mp4",
            f"{base} [video-id-02].mp4",
            f"{base} [video-id-10001].mp4",
            f"{base} [another-id].mp4",
        ]
    for name in lookalikes:
        (tmp_path / name).write_bytes(b"same content")
    temporary = tmp_path / ".verified.part"
    temporary.write_bytes(b"same content")

    result = publish(MediaDownloader(), temporary, tmp_path, compact=compact)

    assert result == tmp_path / names(compact)[0]
    assert result.read_bytes() == b"same content"
    assert all((tmp_path / name).read_bytes() == b"same content" for name in lookalikes)


@pytest.mark.parametrize("compact", [False, True], ids=["readable", "compact"])
def test_highest_numbered_candidate_is_found_after_gap(tmp_path, compact):
    name = (
        "007 (10001).jpg" if compact else
        "2026-09-30-Same [literal] title [video-id-10000].mp4"
    )
    existing = tmp_path / name
    existing.write_bytes(b"same content")
    temporary = tmp_path / ".verified.part"
    temporary.write_bytes(b"same content")

    result = publish(MediaDownloader(), temporary, tmp_path, compact=compact)

    assert result == existing
    assert not (tmp_path / names(compact)[0]).exists()
    assert not temporary.exists()


def test_candidate_replaced_during_comparison_is_not_reused(tmp_path, monkeypatch):
    base, second, _ = (tmp_path / name for name in names(False))
    base.write_bytes(b"verified media")
    temporary = tmp_path / ".verified.part"
    temporary.write_bytes(b"verified media")
    displaced = tmp_path / "displaced-user-file"
    real_compare = platform_items.are_files_identical
    replaced = False

    def replace_after_comparison(source, destination):
        nonlocal replaced
        identical = real_compare(source, destination)
        if destination == base and identical and not replaced:
            base.rename(displaced)
            base.write_bytes(b"different replacement")
            replaced = True
        return identical

    monkeypatch.setattr(platform_items, "are_files_identical", replace_after_comparison)
    result = publish(MediaDownloader(), temporary, tmp_path)

    assert replaced
    assert result == second
    assert result.read_bytes() == b"verified media"
    assert base.read_bytes() == b"different replacement"
    assert displaced.read_bytes() == b"verified media"


@pytest.mark.parametrize("becomes_link", [False, True], ids=["already-link", "changes-to-link"])
def test_symlink_candidate_is_not_reused_without_link_privileges(tmp_path, monkeypatch, becomes_link):
    base, second, _ = (tmp_path / name for name in names(False))
    base.write_bytes(b"same content")
    temporary = tmp_path / ".verified.part"
    temporary.write_bytes(b"same content")
    real_lstat = platform_items.Path.lstat
    real_compare = platform_items.are_files_identical
    comparison_finished = False

    class SymlinkFacts:
        st_mode = platform_items.stat.S_IFLNK

    def lstat_with_link(path, *args, **kwargs):
        if path == base and (not becomes_link or comparison_finished):
            return SymlinkFacts()
        return real_lstat(path, *args, **kwargs)

    def compare_then_replace(source, destination):
        nonlocal comparison_finished
        result = real_compare(source, destination)
        comparison_finished = True
        return result

    monkeypatch.setattr(platform_items.Path, "lstat", lstat_with_link)
    monkeypatch.setattr(platform_items, "are_files_identical", compare_then_replace)
    result = publish(MediaDownloader(), temporary, tmp_path)

    assert result == second
    assert result.read_bytes() == b"same content"
    assert base.read_bytes() == b"same content"
    assert comparison_finished is becomes_link


def test_cancellation_after_comparison_keeps_temporary(tmp_path, monkeypatch):
    base = tmp_path / names(False)[0]
    base.write_bytes(b"same content")
    temporary = tmp_path / ".verified.part"
    temporary.write_bytes(b"same content")
    real_compare = platform_items.are_files_identical
    cancelled = Event()

    def compare_then_cancel(source, destination):
        result = real_compare(source, destination)
        cancelled.set()
        return result

    monkeypatch.setattr(platform_items, "are_files_identical", compare_then_cancel)
    with pytest.raises(DownloadCancelledError):
        publish(MediaDownloader(), temporary, tmp_path, should_cancel=cancelled.is_set)

    assert base.read_bytes() == b"same content"
    assert temporary.read_bytes() == b"same content"


def test_directory_scan_error_preserves_publish_error_reporting(tmp_path):
    temporary = tmp_path / ".verified.part"
    temporary.write_bytes(b"new media")

    with pytest.raises(TemporaryAccessError, match="could not safely publish") as error:
        publish(MediaDownloader(), temporary, tmp_path / "missing-directory")

    assert isinstance(error.value.__cause__, FileNotFoundError)
    assert temporary.read_bytes() == b"new media"


def test_cancellation_during_directory_scan_keeps_temporary(tmp_path):
    for index in range(5):
        (tmp_path / f"unrelated-{index}").write_bytes(b"user data")
    temporary = tmp_path / ".verified.part"
    temporary.write_bytes(b"new media")
    calls = 0

    def cancel_during_scan():
        nonlocal calls
        calls += 1
        return calls == 3

    with pytest.raises(DownloadCancelledError):
        publish(MediaDownloader(), temporary, tmp_path, should_cancel=cancel_during_scan)

    assert calls == 3
    assert temporary.read_bytes() == b"new media"
    assert not (tmp_path / names(False)[0]).exists()


def test_cancel_callback_error_is_not_misreported_as_a_filesystem_error(tmp_path):
    temporary = tmp_path / ".verified.part"
    temporary.write_bytes(b"new media")
    calls = 0
    callback_error = OSError("cancellation callback failed")

    def failing_callback():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise callback_error
        return False

    with pytest.raises(OSError) as error:
        publish(MediaDownloader(), temporary, tmp_path, should_cancel=failing_callback)

    assert error.value is callback_error
    assert temporary.read_bytes() == b"new media"


def test_rechecks_different_winner_after_file_exists(tmp_path, monkeypatch):
    base, second, _ = (tmp_path / name for name in names(False))
    temporary = tmp_path / ".verified.part"
    temporary.write_bytes(b"new verified media")
    real_publish = platform_items.publish_no_replace
    collided = False

    def install_competing_winner(source, destination):
        nonlocal collided
        if destination == base and not collided:
            base.write_bytes(b"different concurrent media")
            collided = True
            raise FileExistsError(str(destination))
        return real_publish(source, destination)

    monkeypatch.setattr(platform_items, "publish_no_replace", install_competing_winner)
    result = publish(MediaDownloader(), temporary, tmp_path)

    assert collided
    assert result == second
    assert base.read_bytes() == b"different concurrent media"
    assert result.read_bytes() == b"new verified media"
    assert not temporary.exists()
