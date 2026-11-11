from __future__ import annotations

import hashlib
from pathlib import Path


APP_ID = "shiying-local-downloader"
APP_VERSION = "0.3.0"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _fingerprint_files(project_root: Path) -> list[Path]:
    """Every file whose change means the running build is out of date."""

    candidates = [
        project_root / "run.py",
        project_root / "launcher.py",
        project_root / "launch_shiying.py",
        project_root / "stop.py",
        project_root / "scripts" / "unix" / "start.command",
        project_root / "scripts" / "unix" / "start.sh",
        project_root / "scripts" / "dev" / "start.bat",
        project_root / "scripts" / "unix" / "stop.command",
        project_root / "scripts" / "unix" / "stop.sh",
        project_root / "scripts" / "dev" / "stop.bat",
        project_root / "启动拾影.bat",
        project_root / "停止拾影.bat",
        project_root / "scripts" / "dev" / "install_deps.bat",
        project_root / "pyproject.toml",
        project_root / "requirements.txt",
        project_root / "requirements.lock.txt",
        project_root / "requirements-dev.txt",
    ]
    # Recursive: the engine/ and manager/ subpackages are loaded by the web
    # process too, so edits there must flip restart_required as well.
    candidates.extend((project_root / "app").rglob("*.py"))
    candidates.extend((project_root / "app" / "static").rglob("*"))
    return [
        candidate
        for candidate in candidates
        if candidate.is_file() and "__pycache__" not in candidate.parts
    ]


_FINGERPRINT_CACHE: tuple[tuple[Path, int, int], ...] | None = None
_BUILD_ID_CACHE: str | None = None


def calculate_build_id(project_root: Path = PROJECT_ROOT) -> str:
    """Fingerprint the code and static assets loaded by the local web process.

    Health checks call this frequently, so the digest is cached against an
    mtime/size snapshot and only recomputed when a watched file changes.
    """

    global _FINGERPRINT_CACHE, _BUILD_ID_CACHE
    files = _fingerprint_files(project_root)
    snapshot = tuple(
        (path, stat.st_mtime_ns, stat.st_size)
        for path, stat in ((p, p.stat()) for p in files)
    )
    if _BUILD_ID_CACHE is not None and _FINGERPRINT_CACHE == snapshot:
        return _BUILD_ID_CACHE
    digest = hashlib.sha256()
    for path, _, _ in snapshot:
        relative = path.relative_to(project_root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    _FINGERPRINT_CACHE = snapshot
    _BUILD_ID_CACHE = digest.hexdigest()[:12]
    return _BUILD_ID_CACHE


BUILD_ID = calculate_build_id()
