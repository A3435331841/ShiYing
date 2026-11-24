from __future__ import annotations

import json
from pathlib import Path

from app.capabilities import creation_policy, environment_impact, load_capabilities


def _manifest(tmp_path: Path, platforms: dict) -> Path:
    path = tmp_path / "capabilities.json"
    path.write_text(
        json.dumps({"schema_version": 1, "platforms": platforms}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_missing_file_marks_all_unavailable(tmp_path: Path) -> None:
    registry = load_capabilities(tmp_path / "missing.json", root=tmp_path)
    assert registry.get("bilibili").download == "unavailable"
    assert registry.get("unknown-platform").download == "unavailable"
    assert "缺失" in registry.get("bilibili").reason


def test_registry_states_and_channels(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        {
            "kuaishou": {
                "label": "快手",
                "download": "experimental",
                "requires_login": True,
                "reason": "实验通道",
            },
            "wechat": {
                "label": "视频号",
                "download": "experimental",
                "requires_login": True,
                "channels": {
                    "client_capture": {
                        "label": "客户端抓取",
                        "download": "unavailable",
                        "reason": "客户端抓取暂不可用，可用元宝登录态",
                    }
                },
            },
        },
    )
    registry = load_capabilities(path, root=tmp_path)
    assert registry.get("kuaishou").download == "experimental"
    channel = registry.get("wechat", "client_capture")
    assert channel.download == "unavailable"
    assert "客户端抓取" in channel.reason
    assert registry.get("bilibili").download == "unavailable"


def test_creation_policy_blocks_unavailable_and_unconfirmed(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        {
            "bilibili": {"download": "supported"},
            "kuaishou": {"download": "experimental", "reason": "实验通道"},
            "wechat": {
                "download": "experimental",
                "channels": {"client_capture": {"download": "unavailable", "reason": "抓取不可用"}},
            },
        },
    )
    registry = load_capabilities(path, root=tmp_path)
    assert creation_policy(registry, "bilibili")[0] is True
    blocked = creation_policy(registry, "kuaishou")
    assert blocked[0] is False
    assert blocked[1] == 409
    assert blocked[2] == "experimental_confirmation_required"
    assert creation_policy(registry, "kuaishou", confirmed=True)[0] is True
    unavailable = creation_policy(registry, "wechat", confirmed=True)
    assert unavailable[0] is True
    channel_blocked = creation_policy(registry, "wechat", confirmed=False)
    assert channel_blocked[0] is False


def test_evidence_missing_does_not_change_state(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        {
            "bilibili": {
                "download": "supported",
                "verified_at": "2026-11-20",
                "evidence": "docs/acceptance/none.md",
            }
        },
    )
    entry = load_capabilities(path, root=tmp_path).get("bilibili")
    assert entry.download == "supported"
    assert entry.evidence_missing is True


def test_claimed_verification_without_evidence_path_is_flagged(tmp_path: Path) -> None:
    """填了 verified_at 却没有 evidence 路径，不能被当成「无需证据」。"""
    path = _manifest(
        tmp_path,
        {"bilibili": {"download": "supported", "verified_at": "2026-11-20"}},
    )
    entry = load_capabilities(path, root=tmp_path).get("bilibili")
    assert entry.download == "supported"  # 标记缺失不改变能力状态
    assert entry.evidence_missing is True

    # 未声称验收、也没有证据路径的实验通道不应被误报为证据缺失。
    path = _manifest(tmp_path, {"kuaishou": {"download": "experimental"}})
    assert load_capabilities(path, root=tmp_path).get("kuaishou").evidence_missing is False


def test_checked_in_manifest_has_no_unbacked_verification():
    root = Path(__file__).resolve().parents[1]
    registry = load_capabilities(root / "data" / "capabilities.json", root=root)
    assert all(not entry.evidence_missing for entry in registry.all())


def test_frozen_app_loads_bundled_capability_registry(
    tmp_path: Path, monkeypatch
) -> None:
    from app import main

    bundle = tmp_path / "_internal"
    manifest = bundle / "data" / "capabilities.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "platforms": {"bilibili": {"download": "supported"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "PROJECT_ROOT", tmp_path / "portable")
    monkeypatch.setattr(
        main,
        "config",
        main.config.model_copy(update={"capabilities_path": "missing.json"}),
    )
    monkeypatch.setattr(main.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(main, "_capability_cache", None)

    registry = main._capabilities()

    assert registry.get("bilibili").download == "supported"
    assert registry.source == str(manifest)

    from fastapi.testclient import TestClient

    data_dir = tmp_path / "portable" / "data"
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "CONFIG_PATH", data_dir / "config.json")
    payload = main.AppConfig(
        download_dir=str(tmp_path / "downloads")
    ).model_dump(mode="json")
    client = TestClient(main.app, base_url="http://localhost")
    response = client.put("/api/config", json=payload)
    assert response.status_code == 200, response.text
    assert main.CONFIG_PATH.is_file()

    rejected = client.put(
        "/api/config",
        json={**payload, "capabilities_path": "data/missing.json"},
    )
    assert rejected.status_code == 422
    client.close()


def test_environment_impact_maps_missing_dependencies() -> None:
    impacts = environment_impact(
        [
            {"id": "ffmpeg", "ok": False},
            {"id": "deno", "ok": False},
            {"id": "chrome", "ok": True},
        ]
    )
    capabilities = {item["capability"] for item in impacts}
    assert capabilities == {"merging", "frame_extract", "youtube_hd"}


def test_registry_search_states_match_searchable_platforms() -> None:
    """能力登记的 search 三态必须与 AI 搜索实际可搜索的平台集合一致。"""
    from app.models import Platform
    from app.search.models import SEARCHABLE_PLATFORMS

    root = Path(__file__).resolve().parents[1]
    registry = load_capabilities(root / "data" / "capabilities.json", root=root)
    searchable = {platform.value for platform in SEARCHABLE_PLATFORMS}
    assert searchable == {
        Platform.BILIBILI.value,
        Platform.YOUTUBE.value,
        Platform.XIAOHONGSHU.value,
        Platform.DOUYIN.value,
        Platform.KUAISHOU.value,
    }
    for entry in registry.all():
        assert (entry.search == "supported") == (entry.platform in searchable), entry.platform