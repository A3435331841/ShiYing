"""基于 Windows 数据保护 API (DPAPI) 的敏感凭据硬件加密存储模块。

在持久化微信视频号（腾讯元宝 Session）等敏感鉴权凭证时，绝对禁止以明文形式写入磁盘。
本模块调用 Windows 内核 crypt32.dll 提供的 CryptProtectData / CryptUnprotectData 接口：
1. 密钥绑定当前 Windows 登录用户的本地主密钥（Master Key），即使数据库或文本被脱机复制也无法解密；
2. 传递标志位 1 (CRYPTPROTECT_UI_FORBIDDEN)，禁止弹出任何系统 UI 对话框；
3. 遵循 ACID 规范，采用 .tmp 临时文件 + fsync 强制落盘 + os.replace 原子重命名替换，防断电截断损坏。
"""
import ctypes
import os
from ctypes import wintypes
from pathlib import Path

# DPAPI 保护密文的头部签名，用于防篡改与自愈识别
PREFIX = b"SHIYING-DPAPI-1\n"


class _Blob(ctypes.Structure):
    """Win32 DATA_BLOB 结构体定义，用于向 crypt32 传递原始与解密缓冲区。"""
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]


def _crypt(data: bytes, decrypt: bool) -> bytes:
    """调用 Windows crypt32.dll 执行基于当前用户安全上下文的对称加密或解密。

    Args:
        data: 待加密或解密的原始二进制字节。
        decrypt: 若为 True 则调用 CryptUnprotectData，若为 False 则调用 CryptProtectData。

    Returns:
        加密或解密后的二进制结果。

    Raises:
        RuntimeError: 非 Windows 操作系统环境。
        ctypes.WinError: Windows DPAPI 加解密失败（如凭据非本用户加密、硬件密钥损坏）。
    """
    if os.name != "nt":
        raise RuntimeError("视频号登录态保护需要 Windows。")
    buffer = ctypes.create_string_buffer(data, len(data))
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    result = _Blob()
    crypt = ctypes.windll.crypt32
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def save_secret(path: Path, value: str) -> None:
    """使用 Windows DPAPI 加密并以原子事务方式将敏感凭据写入文件。

    Args:
        path: 目标加密文件的存储路径。
        value: 待保护的明文字符串（如 Cookie、Token）。
    """
    protected = PREFIX + _crypt(value.encode("utf-8"), False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".protected.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(protected)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_secret(path: Path) -> str:
    """从目标文件中读取并使用 Windows DPAPI 解密敏感凭据。

    若文件缺失或未携带 DPAPI 头部前缀，则回退兼容尝试明文或返回空字符串。

    Args:
        path: 加密凭据所在文件路径。

    Returns:
        解密后的明文字符串。
    """
    if not path.exists():
        return ""
    raw = path.read_bytes()
    if raw.startswith(PREFIX):
        try:
            return _crypt(raw[len(PREFIX):], True).decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            # A truncated or corrupted ciphertext can never decrypt; quarantine
            # the file so status() stops reporting the login state as valid.
            quarantine = path.with_name(path.name + ".corrupt")
            try:
                path.replace(quarantine)
            except OSError:
                pass
            raise OSError("保存的元宝登录态已损坏，已隔离该文件，请重新保存登录态。") from exc
    # Upgrade credentials saved by older versions only after encryption succeeds.
    try:
        value = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        # 无前缀的二进制垃圾与损坏密文同样隔离，否则 status() 会持续把
        # 登录态报成已配置，而每次下载都失败。
        quarantine = path.with_name(path.name + ".corrupt")
        try:
            path.replace(quarantine)
        except OSError:
            pass
        raise OSError("保存的元宝登录态已损坏，已隔离该文件，请重新保存登录态。") from exc
    if value:
        save_secret(path, value)
    return value
