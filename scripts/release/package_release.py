"""Package a clean release ZIP and update SHA256 checksums."""
from __future__ import annotations

import hashlib
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASES_DIR = ROOT / "releases"

EXCLUDE_DIRS = {
    ".venv",
    ".git",
    "build",
    "worktrees",
    "__pycache__",
    ".pytest_cache",
    ".claude",
    "data",
    "downloads",
    "releases",
    "backups",
    "graphify-out",
    # 运行态数据与审计快照：可能包含下载中的媒体、截图与工具痕迹，
    # 绝不进源码发行包。
    "cache",
    "artifacts",
    "审计产物",
}

EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".log",
    ".tmp",
    ".pem",
    ".key",
    ".env",
    # 扩展打包产物与证书/私钥容器不是源码，随包分发无收益且带来凭据风险。
    ".crx",
    ".p12",
    ".cer",
}


def compute_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def package_release(version: str | None = None) -> Path:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app.build_info import APP_VERSION

    ver = version or APP_VERSION
    date_str = datetime.now().strftime("%Y%m%d")
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    zip_name = f"拾影_v{ver}_源码_{date_str}.zip"
    zip_path = RELEASES_DIR / zip_name

    print(f"Packaging Shiying v{ver} into {zip_path}...")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(ROOT):
            rel_root = Path(root).relative_to(ROOT)
            if rel_root == Path("data"):
                dirs[:] = []
            else:
                # data/ 仅放行能力登记文件，其余运行态数据仍保持排除。
                dirs[:] = [
                    d
                    for d in dirs
                    if (d == "data" and rel_root == Path("."))
                    or (d not in EXCLUDE_DIRS and not d.startswith("."))
                ]

            if rel_root != Path("data") and any(
                part in EXCLUDE_DIRS for part in rel_root.parts
            ):
                continue

            for file in files:
                if rel_root == Path("data") and file != "capabilities.json":
                    continue
                file_path = Path(root) / file
                if file_path.suffix in EXCLUDE_SUFFIXES or file.startswith("."):
                    continue
                arcname = str((rel_root / file).as_posix())
                zf.write(file_path, arcname)

    sha = compute_sha256(zip_path)
    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"Created: {zip_name} ({size_mb:.2f} MB, SHA256: {sha})")

    update_checksums()
    return zip_path


def update_checksums() -> None:
    checksums = []
    # 源码 zip 与便携包 zip 走 *.zip；Inno Setup 安装程序是 *.exe，一并纳入校验。
    artifacts = sorted(RELEASES_DIR.glob("*.zip")) + sorted(RELEASES_DIR.glob("*.exe"))
    for artifact in artifacts:
        sha = compute_sha256(artifact)
        checksums.append(f"{sha}  {artifact.name}")

    sums_path = RELEASES_DIR / "SHA256SUMS.txt"
    sums_path.write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print(f"Updated: {sums_path}")


if __name__ == "__main__":
    package_release()
