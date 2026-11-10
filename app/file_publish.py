"""Publish a verified file without overwriting an existing file."""
import errno
import os
import stat
import threading
from pathlib import Path

# ponytail: one process-wide lock serializes pair-name selection; use
# per-directory locks if concurrent album publication becomes a bottleneck.
_PAIR_NAME_LOCK = threading.Lock()


def publish_no_replace(temporary: Path, destination: Path) -> None:
    try:
        os.link(temporary, destination)
    except FileExistsError:
        raise
    except OSError:
        if os.name != "nt":
            raise
        # On Windows, rename within a volume is atomic and fails if the
        # destination exists. Both paths must live on the same filesystem.
        os.rename(temporary, destination)
        return
    temporary.unlink()


def publish_unique(temporary: Path, destination: Path) -> Path:
    """Atomically publish under an unused name without replacing user files."""
    for number in range(1, 10002):
        candidate = destination if number == 1 else destination.with_name(
            f"{destination.stem} ({number}){destination.suffix}"
        )
        try:
            publish_no_replace(temporary, candidate)
            return candidate
        except FileExistsError:
            continue
    raise FileExistsError("Could not find an unused output filename")


def are_files_identical(file1: Path, file2: Path) -> bool:
    """Return True if both files exist, are regular files, and have identical size and content."""
    try:
        if file1.is_symlink() or file2.is_symlink():
            return False
        s1 = file1.stat().st_size
        s2 = file2.stat().st_size
        if s1 != s2 or s1 == 0:
            return False
        if s1 <= 1024 * 1024:
            return file1.read_bytes() == file2.read_bytes()
        # Compare the full content: head-and-tail-only checks would treat a
        # same-size file with a corrupted middle as identical and delete the
        # freshly downloaded copy in its favour.
        with file1.open("rb") as f1, file2.open("rb") as f2:
            while True:
                chunk1 = f1.read(1024 * 1024)
                chunk2 = f2.read(1024 * 1024)
                if chunk1 != chunk2:
                    return False
                if not chunk1:
                    return True
    except OSError:
        return False


def publish_or_reuse_identical(temporary: Path, destination: Path) -> Path:
    """Publish temporary to destination, or reuse an existing identical file without creating duplicates."""
    def candidate_for(number: int) -> Path:
        return destination if number == 1 else destination.with_name(
            f"{destination.stem} ({number}){destination.suffix}"
        )

    # Check the entire bounded allocation range before filling a gap: an
    # identical version may remain after an earlier numbered file was removed.
    first_free = None
    for number in range(1, 10002):
        candidate = candidate_for(number)
        try:
            mode = candidate.lstat().st_mode
        except OSError as exc:
            if exc.errno == errno.ENAMETOOLONG or getattr(exc, "winerror", None) == 206:
                # Later numbered names cannot be shorter than this one.
                break
            if isinstance(exc, FileNotFoundError):
                if first_free is None:
                    first_free = number
                continue
            raise
        if stat.S_ISREG(mode) and are_files_identical(temporary, candidate):
            temporary.unlink(missing_ok=True)
            return candidate

    # Another publisher may have claimed a free name since the scan. Compare
    # its winning file before trying the next name; links remain occupied.
    for number in range(first_free or 1, 10002):
        candidate = candidate_for(number)
        try:
            publish_no_replace(temporary, candidate)
            return candidate
        except FileExistsError:
            if candidate.is_file() and are_files_identical(temporary, candidate):
                temporary.unlink(missing_ok=True)
                return candidate
    raise FileExistsError("Could not find an unused output filename")


def publish_pair_image(temporary: Path, destination: Path) -> Path:
    """Reserve an unused basename for a static image and its motion file."""
    with _PAIR_NAME_LOCK:
        occupied = {path.stem for path in destination.parent.iterdir()}
        for number in range(1, 10002):
            stem = destination.stem if number == 1 else f"{destination.stem} ({number})"
            if stem in occupied:
                continue
            candidate = destination.with_name(stem + destination.suffix)
            try:
                publish_no_replace(temporary, candidate)
                return candidate
            except FileExistsError:
                continue
    raise FileExistsError("Could not find an unused Live Photo basename")
