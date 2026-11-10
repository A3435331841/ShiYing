import copy
import winreg
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.wechat_network import ProxyConflictError, ProxyRecovery
from app.wechat_secret import PREFIX


class Registry:
    def __init__(self):
        self.values = {
            "ProxyEnable": {"present": True, "type": winreg.REG_DWORD, "value": 1},
            "ProxyServer": {"present": True, "type": winreg.REG_SZ, "value": "127.0.0.1:7890"},
            "ProxyOverride": {"present": True, "type": winreg.REG_SZ, "value": "<local>"},
            "AutoConfigURL": {"present": False},
            "AutoDetect": {"present": False},
        }
        self.fail_on = None

    def snapshot(self):
        return copy.deepcopy(self.values)

    def write(self, name, value):
        if self.fail_on == name:
            raise OSError("simulated interrupted write")
        self.values[name] = copy.deepcopy(value)

    def refresh(self):
        pass


def test_proxy_journal_restores_original_and_preserves_unmodified_fields(tmp_path):
    registry = Registry()
    before = registry.snapshot()
    journal = tmp_path / "proxy.dat"
    recovery = ProxyRecovery(journal, registry)
    recovery.prepare(9018)
    assert journal.read_bytes().startswith(PREFIX)
    assert b"127.0.0.1:7890" not in journal.read_bytes()
    recovery.apply()
    assert registry.values["ProxyServer"]["value"] == "127.0.0.1:9018"
    registry.values["ProxyOverride"]["value"] = "changed by user"
    assert recovery.recover()
    assert registry.values["ProxyServer"] == before["ProxyServer"]
    assert registry.values["ProxyEnable"] == before["ProxyEnable"]
    assert registry.values["ProxyOverride"]["value"] == "changed by user"
    assert not journal.exists()
    assert not recovery.recover()


def test_proxy_recovers_after_interrupted_first_write(tmp_path):
    registry = Registry()
    before = registry.snapshot()
    recovery = ProxyRecovery(tmp_path / "proxy.dat", registry)
    recovery.prepare(9018)
    registry.fail_on = "ProxyEnable"
    with pytest.raises(OSError):
        recovery.apply()
    assert registry.values["ProxyServer"] == before["ProxyServer"]
    registry.fail_on = None
    assert recovery.recover()
    assert registry.snapshot() == before


def test_proxy_failed_apply_rolls_back_immediately_when_possible(tmp_path):
    registry = Registry()
    before = registry.snapshot()
    recovery = ProxyRecovery(tmp_path / "proxy.dat", registry)
    recovery.prepare(9018)
    original_write = registry.write
    failed = False
    def fail_once(name, value):
        nonlocal failed
        if name == "ProxyEnable" and not failed:
            failed = True
            raise OSError("simulated interrupted write")
        original_write(name, value)
    registry.write = fail_once
    with pytest.raises(OSError):
        recovery.apply()
    assert registry.snapshot() == before
    assert not recovery.journal.exists()


def test_proxy_recovery_does_not_overwrite_external_change(tmp_path):
    registry = Registry()
    recovery = ProxyRecovery(tmp_path / "proxy.dat", registry)
    recovery.prepare(9018)
    recovery.apply()
    registry.values["ProxyServer"]["value"] = "127.0.0.1:8888"
    with pytest.raises(ProxyConflictError):
        recovery.recover()
    assert registry.values["ProxyServer"]["value"] == "127.0.0.1:8888"
    assert recovery.journal.exists()


def test_proxy_recovery_rejects_unprotected_record(tmp_path):
    registry = Registry()
    before = registry.snapshot()
    journal = tmp_path / "proxy.dat"
    journal.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Unprotected"):
        ProxyRecovery(journal, registry).recover()
    assert registry.snapshot() == before
    assert journal.exists()


def test_proxy_recovery_preserves_absent_values(tmp_path):
    registry = Registry()
    registry.values["ProxyServer"] = {"present": False}
    registry.values["ProxyEnable"] = {"present": False}
    original = registry.snapshot()
    recovery = ProxyRecovery(tmp_path / "proxy.dat", registry)
    recovery.prepare(9018)
    recovery.apply()
    recovery.recover()
    assert registry.snapshot() == original


def test_proxy_recovery_retries_after_interrupted_restore(tmp_path):
    registry = Registry()
    original = registry.snapshot()
    recovery = ProxyRecovery(tmp_path / "proxy.dat", registry)
    recovery.prepare(9018)
    recovery.apply()
    registry.fail_on = "ProxyEnable"
    with pytest.raises(OSError):
        recovery.recover()
    assert recovery.journal.exists()
    registry.fail_on = None
    assert recovery.recover()
    assert registry.snapshot() == original


def test_concurrent_prepare_cannot_replace_recovery_record(tmp_path):
    registry = Registry()
    journal = tmp_path / "proxy.dat"
    def prepare(port):
        try:
            ProxyRecovery(journal, registry).prepare(port)
            return "ok"
        except ProxyConflictError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(prepare, [9018, 9019]))
    assert sorted(results) == ["conflict", "ok"]
    assert journal.exists()
