"""Package a clean release ZIP and update SHA256 checksums."""
from __future__ import annotations

import hashlib
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASES_DIR = ROOT / "releases"

EXCLUDE_DIRS = {
    ".venv",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".claude",
    "data",
    "downloads",
    "releases",
    "backups",
    "graphify-out",
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
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]

            rel_root = Path(root).relative_to(ROOT)
            if any(part in EXCLUDE_DIRS for part in rel_root.parts):
                continue

            for file in files:
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
    for zip_file in sorted(RELEASES_DIR.glob("*.zip")):
        sha = compute_sha256(zip_file)
        checksums.append(f"{sha}  {zip_file.name}")

    sums_path = RELEASES_DIR / "SHA256SUMS.txt"
    sums_path.write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print(f"Updated: {sums_path}")


if __name__ == "__main__":
    package_release()
