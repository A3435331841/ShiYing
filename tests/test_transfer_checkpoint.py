import json
import os
from pathlib import Path

import pytest

from app.engine import transfer
from app.models import MediaType, Platform
from app.runtime import ProjectLock, ProjectLockHeldError


def _paths(output_dir: Path, scope: str) -> tuple[Path, Path]:
    with transfer.direct_transfer_scope(scope):
        return transfer._direct_transfer_paths(
            output_dir,
            source_url="https://example.test/video",
            media_id="media-1",
            platform=Platform.BILIBILI,
            media_type=MediaType.VIDEO,
            asset_index=None,
            paired_stem=None,
        )


def test_transfer_temporary_paths_are_scoped_by_job(tmp_path: Path) -> None:
    first = _paths(tmp_path, "job-a:item-1")
    second = _paths(tmp_path, "job-b:item-1")

    assert first[0] != second[0]
    assert first[1] != second[1]


def test_transfer_temporary_file_has_kernel_owned_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("OMD_RUNTIME_DIR", str(runtime_dir))
    temporary = tmp_path / "media.part"
    lock_path = transfer._direct_transfer_lock_path(temporary)
    first = ProjectLock(lock_path)
    second = ProjectLock(lock_path)
    assert runtime_dir in lock_path.parents
    first.acquire(blocking=False)
    try:
        with pytest.raises(ProjectLockHeldError):
            second.acquire(blocking=False)
    finally:
        first.release()

    second.acquire(blocking=False)
    second.release()
    assert not list(tmp_path.glob("*.lock"))


@pytest.mark.parametrize("open_handle", [False, True])
def test_checkpoint_fsyncs_media_before_replacing_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, open_handle: bool
) -> None:
    temporary = tmp_path / "media.part"
    state_path = tmp_path / "media.part.json"
    temporary.write_bytes(b"media-data")
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def replace(source, destination) -> None:
        assert events == ["fsync", "fsync"]
        real_replace(source, destination)

    monkeypatch.setattr(transfer.os, "fsync", fsync)
    monkeypatch.setattr(transfer.os, "replace", replace)
    handle = temporary.open("r+b") if open_handle else None
    try:
        transfer._save_direct_transfer_state(
            state_path,
            offset=10,
            total=20,
            etag='"media-v1"',
            last_modified="",
            media_handle=handle,
        )
    finally:
        if handle is not None:
            handle.close()

    assert json.loads(state_path.read_text(encoding="utf-8"))["offset"] == 10
    assert events == ["fsync", "fsync"]
