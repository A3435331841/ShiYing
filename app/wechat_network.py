"""Crash-recoverable ownership of the two WinINet values we change.

This module does not start capture or install a certificate. A journal must be
written before any proxy change, and is kept if restoration is uncertain.
"""
from __future__ import annotations

import ctypes
import json
import re
import threading
import winreg
from pathlib import Path

from .wechat_secret import PREFIX, load_secret, save_secret

INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
SNAPSHOT_FIELDS = ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL", "AutoDetect")
CHANGED_FIELDS = ("ProxyServer", "ProxyEnable")
# ponytail: one process-wide lock protects journal transitions; capture permits
# one active system proxy and the launcher prevents multiple app instances.
_RECOVERY_LOCK = threading.RLock()


class ProxyConflictError(RuntimeError):
    pass


class WindowsProxyRegistry:
    def snapshot(self) -> dict:
        result = {}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_READ) as key:
            for name in SNAPSHOT_FIELDS:
                try:
                    value, value_type = winreg.QueryValueEx(key, name)
                    result[name] = {"present": True, "type": value_type, "value": value}
                except FileNotFoundError:
                    result[name] = {"present": False}
        return result

    def write(self, name: str, record: dict) -> None:
        if name not in CHANGED_FIELDS:
            raise ValueError("Unexpected proxy field")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_SET_VALUE) as key:
            if record["present"]:
                winreg.SetValueEx(key, name, 0, record["type"], record["value"])
            else:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass

    def refresh(self) -> None:
        wininet = ctypes.windll.wininet
        if not wininet.InternetSetOptionW(0, 39, 0, 0) or not wininet.InternetSetOptionW(0, 37, 0, 0):
            raise OSError("Could not refresh Windows proxy settings")


class ProxyRecovery:
    def __init__(self, journal: Path, registry=None):
        self.journal = Path(journal)
        self.registry = registry or WindowsProxyRegistry()

    def prepare(self, port: int) -> None:
        with _RECOVERY_LOCK:
            self._prepare(port)

    def _prepare(self, port: int) -> None:
        if self.journal.exists():
            raise ProxyConflictError("Existing proxy recovery record must be resolved first")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid local proxy port")
        original = self.registry.snapshot()
        self._validate_snapshot(original)
        applied = {
            "ProxyServer": {"present": True, "type": winreg.REG_SZ, "value": f"127.0.0.1:{port}"},
            "ProxyEnable": {"present": True, "type": winreg.REG_DWORD, "value": 1},
        }
        save_secret(self.journal, json.dumps({"version": 1, "original": original, "applied": applied}))

    def apply(self) -> None:
        with _RECOVERY_LOCK:
            self._apply()

    def _apply(self) -> None:
        state = self._load()
        current = self.registry.snapshot()
        if any(current[name] != state["original"][name] for name in CHANGED_FIELDS):
            raise ProxyConflictError("Proxy settings changed since preparation")
        try:
            for name in CHANGED_FIELDS:
                self.registry.write(name, state["applied"][name])
            self.registry.refresh()
        except BaseException:
            # Leave the encrypted journal in place if recovery also fails.
            try:
                self._recover()
            except Exception:
                pass
            raise

    def recover(self) -> bool:
        with _RECOVERY_LOCK:
            return self._recover()

    def _recover(self) -> bool:
        if not self.journal.exists():
            return False
        state = self._load()
        current = self.registry.snapshot()
        # A crash can occur after either write. Values outside the two states
        # indicate a user or another application changed proxy settings.
        if any(current[name] not in (state["original"][name], state["applied"][name])
               for name in CHANGED_FIELDS):
            raise ProxyConflictError("Proxy settings changed externally; manual recovery is required")
        for name in CHANGED_FIELDS:
            self.registry.write(name, state["original"][name])
        self.registry.refresh()
        restored = self.registry.snapshot()
        if any(restored[name] != state["original"][name] for name in CHANGED_FIELDS):
            raise ProxyConflictError("Proxy settings could not be verified after recovery")
        self.journal.unlink()
        return True

    @staticmethod
    def _validate_snapshot(snapshot: object) -> None:
        if not isinstance(snapshot, dict) or set(snapshot) != set(SNAPSHOT_FIELDS):
            raise ValueError("Invalid proxy recovery record")
        for name, record in snapshot.items():
            if not isinstance(record, dict) or type(record.get("present")) is not bool:
                raise ValueError("Invalid proxy recovery record")
            if record["present"]:
                expected_type = winreg.REG_DWORD if name in ("ProxyEnable", "AutoDetect") else winreg.REG_SZ
                expected_value_type = int if expected_type == winreg.REG_DWORD else str
                if set(record) != {"present", "type", "value"} or record["type"] != expected_type or type(record["value"]) is not expected_value_type:
                    raise ValueError("Invalid proxy recovery record")
            elif set(record) != {"present"}:
                raise ValueError("Invalid proxy recovery record")

    def _load(self) -> dict:
        if not self.journal.read_bytes().startswith(PREFIX):
            raise ValueError("Unprotected proxy recovery record")
        state = json.loads(load_secret(self.journal))
        if (not isinstance(state, dict) or set(state) != {"version", "original", "applied"}
                or type(state.get("version")) is not int or state["version"] != 1):
            raise ValueError("Invalid proxy recovery record")
        self._validate_snapshot(state.get("original"))
        applied = state.get("applied")
        if not isinstance(applied, dict) or set(applied) != set(CHANGED_FIELDS):
            raise ValueError("Invalid proxy recovery record")
        server, enabled = applied.get("ProxyServer"), applied.get("ProxyEnable")
        if (not isinstance(server, dict) or set(server) != {"present", "type", "value"}
                or server.get("present") is not True or server.get("type") != winreg.REG_SZ
                or not isinstance(server.get("value"), str)
                or not re.fullmatch(r"127\.0\.0\.1:(?:[1-9][0-9]{0,4})", server["value"])
                or int(server["value"].split(":")[1]) > 65535
                or enabled != {"present": True, "type": winreg.REG_DWORD, "value": 1}):
            raise ValueError("Invalid proxy recovery record")
        return state
