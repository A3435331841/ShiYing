"""构建拾影 Windows 便携包（onedir zip）。

流程：
1. 依据源码指纹生成 build/pyinstaller/build_id.txt（冻结模式 /api/health 的 build_id）。
2. 准备包内 bin/：venv 的 deno.exe + 捆绑的 ffmpeg/ffprobe（gyan.dev essentials，
   首次下载后把 URL+SHA256 锁定进 scripts/release/ffmpeg.lock.json，后续构建强校验）。
3. 用 PyInstaller 执行 scripts/release/shiying.spec（onedir，双入口）。
4. dist/拾影 打包为 releases/拾影_v{版本}_win64_portable_{日期}.zip，并重算 SHA256SUMS。

用法：
    python scripts/release/build_exe.py                # 完整构建
    python scripts/release/build_exe.py --skip-ffmpeg  # 复用已缓存的 ffmpeg
    python scripts/release/build_exe.py --ffmpeg-zip PATH
    python scripts/release/build_exe.py --repin        # 上游滚动版本更新后重新锁定
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.build_info import APP_VERSION, calculate_build_id  # noqa: E402

BUILD_DIR = REPO_ROOT / "build" / "pyinstaller"
DIST_DIR = BUILD_DIR / "dist"
FFMPEG_CACHE = REPO_ROOT / "build" / "ffmpeg"
FFMPEG_LOCK = Path(__file__).resolve().parent / "ffmpeg.lock.json"
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
RELEASES = REPO_ROOT / "releases"

README_NAME = "使用说明.txt"


def log(message: str) -> None:
    print(f"[build_exe] {message}", flush=True)


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    log("安装 PyInstaller（仅构建用，不进入运行时依赖）…")
    uv = shutil.which("uv")
    if uv:
        subprocess.run(
            [uv, "pip", "install", "--python", sys.executable, "pyinstaller>=6,<7"],
            check=True,
        )
    else:
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller>=6,<7"], check=True)


def write_build_id() -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    build_id = calculate_build_id()
    (BUILD_DIR / "build_id.txt").write_text(build_id + "\n", encoding="utf-8")
    log(f"build_id = {build_id}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_ffmpeg(zip_path: Path | None, skip: bool, repin: bool) -> None:
    FFMPEG_CACHE.mkdir(parents=True, exist_ok=True)
    ffmpeg = FFMPEG_CACHE / "ffmpeg.exe"
    ffprobe = FFMPEG_CACHE / "ffprobe.exe"
    if skip and ffmpeg.is_file() and ffprobe.is_file():
        log("复用已缓存的 ffmpeg/ffprobe")
        return
    if zip_path is None:
        lock = json.loads(FFMPEG_LOCK.read_text("utf-8")) if FFMPEG_LOCK.is_file() else None
        if lock and not repin:
            source_url, expected = lock["url"], lock["sha256"]
        else:
            source_url, expected = FFMPEG_URL, None
        download = FFMPEG_CACHE / "ffmpeg-download.zip"
        if not download.is_file() or repin or not lock:
            log(f"下载 FFmpeg essentials：{source_url}")
            urllib.request.urlretrieve(source_url, download)
        actual = _sha256(download)
        if expected and actual != expected:
            raise SystemExit(
                "FFmpeg 下载校验和不匹配：上游滚动版本已更新。"
                "确认来源可信后运行 build_exe.py --repin 重新锁定。"
            )
        if not expected:
            FFMPEG_LOCK.write_text(
                json.dumps({"url": source_url, "sha256": actual}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log(f"已锁定 FFmpeg 来源 SHA256 → {FFMPEG_LOCK.name}")
        zip_path = download
    with zipfile.ZipFile(zip_path) as archive:
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            member = next(entry for entry in archive.namelist() if entry.endswith("/bin/" + name))
            target = FFMPEG_CACHE / name
            with archive.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            log(f"已提取 {name}")


def prepare_bin() -> None:
    bin_dir = BUILD_DIR / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    deno_src = Path(sys.executable).resolve().parent / "deno.exe"
    if not deno_src.is_file():
        raise SystemExit(f"venv 中找不到 deno.exe：{deno_src}")
    shutil.copy2(deno_src, bin_dir / "deno.exe")
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        source = FFMPEG_CACHE / name
        if not source.is_file():
            raise SystemExit(f"缺少 {source}，请先完成 FFmpeg 下载步骤")
        shutil.copy2(source, bin_dir / name)
    log("包内 bin/ 已就绪（deno/ffmpeg/ffprobe）")


def run_pyinstaller() -> None:
    spec = Path(__file__).resolve().parent / "shiying.spec"
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean",
            "--distpath", str(DIST_DIR),
            "--workpath", str(BUILD_DIR / "work"),
            str(spec),
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )


def write_package_readme(bundle: Path) -> None:
    (bundle / README_NAME).write_text(
        """拾影 · 本地影像档案（便携版）

启动：双击「拾影.exe」。数据（任务记录、下载、缓存）保存在本文件夹内，移动整个文件夹即完成搬家。
退出：设置 → 完全退出拾影；或双击「停止拾影.exe」。直接关窗口不会停止后台下载。

依赖：内置 FFmpeg/FFprobe 与 Deno。需要系统已安装 Google Chrome（用于辅助会话）与 WebView2 运行时
（Windows 10/11 通常自带）。B 站/YouTube 等平台的画质与可用性受登录态影响。

默认地址 http://127.0.0.1:8766 ，仅监听本机；不上传云端，不收集数据。
首次启动如提示端口被占用，请先运行「停止拾影.exe」。
""",
        encoding="utf-8",
    )


def make_zip() -> Path:
    bundle = DIST_DIR / "拾影"
    if not (bundle / "拾影.exe").is_file():
        raise SystemExit(f"PyInstaller 产物缺失：{bundle}")
    write_package_readme(bundle)
    RELEASES.mkdir(exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    zip_path = RELEASES / f"拾影_v{APP_VERSION}_win64_portable_{date}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(bundle.parent).as_posix())
    log(f"便携包已生成：{zip_path}（{_sha256(zip_path)[:12]}…）")
    return zip_path


def update_checksums() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from package_release import update_checksums as refresh

    refresh()
    log("releases/SHA256SUMS.txt 已更新")


def find_iscc(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"指定的 ISCC 不存在：{path}")
        return path
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
    ]
    where = shutil.which("ISCC")
    if where:
        candidates.insert(0, Path(where))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "未找到 Inno Setup 6（ISCC.exe）。请安装：winget install JRSoftware.InnoSetup"
        "，或用 --iscc 指定 ISCC.exe 路径。"
    )


def build_installer(iscc: Path | None) -> Path:
    if sys.platform != "win32":
        raise SystemExit("安装程序构建仅支持在 Windows 上执行。")
    iscc_path = find_iscc(iscc)
    log(f"使用 ISCC：{iscc_path}")
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    output = RELEASES / f"拾影_v{APP_VERSION}_win64_setup_{date}.exe"
    iss = Path(__file__).resolve().parent / "installer" / "shiying.iss"
    subprocess.run(
        [
            str(iscc_path),
            f"/DAPP_VERSION={APP_VERSION}",
            f"/DDistDir={DIST_DIR / '拾影'}",
            f"/DOutputBase=拾影_v{APP_VERSION}_win64_setup_{date}",
            "/Q",
            str(iss),
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )
    if not output.is_file():
        # ISCC 以 /DOutputBase 生成的文件名可能与预期有差异，做一次宽容查找。
        produced = sorted(RELEASES.glob(f"拾影_v{APP_VERSION}_win64_setup_*.exe"))
        if not produced:
            raise SystemExit("ISCC 未产出安装程序。")
        output = produced[-1]
    log(f"安装程序已生成：{output}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="构建拾影 Windows 便携包")
    parser.add_argument("--ffmpeg-zip", type=Path, default=None, help="使用本地 FFmpeg essentials zip")
    parser.add_argument("--skip-ffmpeg", action="store_true", help="复用 build/ffmpeg 缓存")
    parser.add_argument("--repin", action="store_true", help="重新下载并锁定 FFmpeg 来源")
    parser.add_argument("--no-pyinstaller-install", action="store_true", help="不自动安装 PyInstaller")
    parser.add_argument("--installer", action="store_true", help="便携包之外再用 Inno Setup 产出安装程序")
    parser.add_argument("--iscc", type=str, default=None, help="ISCC.exe 路径（默认自动探测）")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("便携包构建仅支持在 Windows 上执行。")
    if not args.no_pyinstaller_install:
        ensure_pyinstaller()
    write_build_id()
    prepare_ffmpeg(args.ffmpeg_zip, args.skip_ffmpeg, args.repin)
    prepare_bin()
    run_pyinstaller()
    make_zip()
    if args.installer:
        build_installer(args.iscc)
    update_checksums()
    log("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
