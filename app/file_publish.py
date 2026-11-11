"""原子文件发布、物理防重冲突解决与实况成对独占锁管理。

本模块承担所有下载产物从临时分块文件 (.part) 移动至最终归档目录的核心落盘安全保障：
1. 原子发布防覆盖 (publish_no_replace)：利用操作系统原子硬链接 (os.link) 或原子重命名 (os.rename)，
   遇到已有同名文件时强制抛出 FileExistsError，绝不静默覆盖已有数据；
2. 相同内容智能复用 (publish_or_reuse_identical)：全量对比文件体积与分块哈希，若发现目标目录已有完全
   相同内容的文件，立即删除临时暂存并复用已有路径（秒级完成）；
3. 重名递增避让 (publish_unique)：在内容不同时自动追加 " (2)" 直至 10001，避免冲突；
4. Live Photo 实况照片同步锁 (publish_pair_image)：加持进程级 _PAIR_NAME_LOCK，锁定未占用的 BaseName，
   确保静态图与同名动态视频序号强一致，杜绝并发下的错位问题。
"""
import contextlib
import errno
import os
import stat
import threading
from pathlib import Path

# 全局独占锁：串行化 Live Photo 实况照片（静态图+动态视频）同名基名的分配与发布
_PAIR_NAME_LOCK = threading.Lock()


def _apply_published_permissions(path: Path) -> None:
    """对已发布的媒体文件应用标准权限（尽力而为方式）。

    临时文件 (mkstemp) 默认创建权限为 0600；最终归档的媒体资产应对同机其他用户可读 (0644)。
    Windows 使用 ACL 权限模型故跳过。即使 chmod 失败也不阻塞发布流程。
    """
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        os.chmod(path, 0o644)


def publish_no_replace(temporary: Path, destination: Path) -> None:
    """原子化将临时文件发布至目标路径，若目标已存在则绝对拒绝覆盖。

    实现逻辑：
    - POSIX: 使用 os.link 建立硬链接（若目标存在触发 FileExistsError），成功后删除临时文件；
    - Windows: 在同卷下利用 os.rename 实现原子性移动，目标存在时直接抛出拒绝。

    Args:
        temporary: 临时下载或解密完成的文件路径。
        destination: 期望落盘的最终归档路径。

    Raises:
        FileExistsError: 目标路径已存在同名文件或符号链接。
        OSError: 跨卷移动或底层文件系统 I/O 异常。
    """
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
    _apply_published_permissions(destination)


def publish_unique(temporary: Path, destination: Path) -> Path:
    """原子化发布文件；若文件名冲突则在安全边界内递增编号 (" (2)".." (10001)")，绝不覆盖已有文件。

    Args:
        temporary: 临时暂存文件路径。
        destination: 期望落盘的目标基础路径。

    Returns:
        最终成功落盘的实际物理文件路径。

    Raises:
        FileExistsError: 遍历尝试超过 10001 个序号仍未能找到空闲名称。
    """
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
    """全面校验两个物理文件的大小与二进制内容是否完全一致。

    比对策略：
    - 拒绝任何符号链接 (Symlink) 以防穿透劫持；
    - 快速排查：文件大小不相等或为 0 字节直接判定不一致；
    - 体积 <= 1MB：一次性读取全部字节进行内存快速比对；
    - 体积 > 1MB：流式按 1MB 数据块分块逐字节全量比对（杜绝仅靠首尾检测造成的中间坏道误判）。

    Args:
        file1: 第一个比较文件路径。
        file2: 第二个比较文件路径。

    Returns:
        若两者为常规物理文件且二进制内容逐字节完全相同则返回 True，否则返回 False。
    """
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
    """将临时文件原子发布至目标路径；若目标目录下已存在完全相同内容的文件，则直接复用并删除临时文件。

    该函数实现了“极速去重秒传”与“重名安全递增”的双重保障：
    1. 扫描 1..10001 的候选序号，若发现同名或已有编号文件中存在二进制完全相同的副本，立即复用其路径并
       删除当前临时文件，避免产生重复垃圾文件；
    2. 若内容不相同，则抢占首个未被占用的空闲文件名执行原子落盘；
    3. 能够防御并发进程间的竞争条件 (Race Condition)。

    Args:
        temporary: 已下载或合并完成的暂存文件路径。
        destination: 期望落盘的初始目标路径。

    Returns:
        最终复用的已有文件路径或新发布的落盘文件路径。

    Raises:
        FileExistsError: 序号超过 10001 仍无法取得有效文件名。
    """
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
    """为 Live Photo 实况照片（静态主图与同名动态 MP4）原子锁定并发布未占用的成对基名。

    加持全局 _PAIR_NAME_LOCK 锁，同时扫描父目录中已被占用的文件主干名 (stem)，
    确保生成的静态图与随后的动态视频拥有完全相同的序号与前缀（如 '001.heic' 与 '001.mp4'，
    或重名避让时的 '001 (2).heic' 与 '001 (2).mp4'），绝不发生序号错位。

    Args:
        temporary: 临时静态图片路径。
        destination: 目标落盘路径（如 '.../001.heic'）。

    Returns:
        最终分配并落盘的成对静态图路径。

    Raises:
        FileExistsError: 尝试超过 10001 个候选基名仍被完全占用。
    """
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
