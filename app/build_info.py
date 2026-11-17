"""发行版本与源码运行指纹计算。

扫描入口脚本、依赖清单、app/ 源码与静态资源生成稳定 build id，供 /api/health
判断 restart_required。纯文件读取与哈希，无可变状态，主进程与工作线程均可调用。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path


APP_ID = "shiying-local-downloader"
APP_VERSION = "0.3.1"


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _frozen_project_root() -> Path:
    """EXE 所在目录：便携包的数据目录（data/downloads/cache）跟随 exe 落位。

    这是冻结模式下 PROJECT_ROOT 的唯一定义；run.py / stop.py / app.main /
    app.wechat 的同名常量在冻结分支委托到这里，保证身份记录相互匹配。
    """
    return Path(sys.executable).resolve().parent


PROJECT_ROOT = _frozen_project_root() if _is_frozen() else Path(__file__).resolve().parent.parent


def project_root_for(default: Path) -> Path:
    """冻结运行（EXE 包）返回 exe 目录，源码运行返回调用方给定的 default。

    main.py / run.py / stop.py / wechat.py 各自定义 PROJECT_ROOT 时统一走这里，
    保证冻结模式下身份记录、数据目录与停止工具指向同一位置。
    """
    return _frozen_project_root() if _is_frozen() else default


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


def _frozen_build_id() -> str | None:
    """构建期由 scripts/release/build_exe.py 写入包内的源码指纹。"""

    if not _is_frozen():
        return None
    marker = Path(getattr(sys, "_MEIPASS", "")) / "build_id.txt"
    try:
        return marker.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def calculate_build_id(project_root: Path = PROJECT_ROOT) -> str:
    """Fingerprint the code and static assets loaded by the local web process.

    Health checks call this frequently, so the digest is cached against an
    mtime/size snapshot and only recomputed when a watched file changes.
    冻结运行（EXE 包）没有源码树可扫描，改用构建期固化的指纹。
    """

    global _FINGERPRINT_CACHE, _BUILD_ID_CACHE
    frozen = _frozen_build_id()
    if frozen:
        _BUILD_ID_CACHE = frozen
        return frozen
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
