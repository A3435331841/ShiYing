"""Protect the locally stored Yuanbao credential with Windows DPAPI."""
import ctypes
import os
from ctypes import wintypes
from pathlib import Path

PREFIX = b"SHIYING-DPAPI-1\n"


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]


def _crypt(data: bytes, decrypt: bool) -> bytes:
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
