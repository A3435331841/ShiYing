#!/usr/bin/env python3
"""Download one sample per platform and work type through the app API, then probe the files.

Uses the app's own preview -> job -> start flow with a temporary download folder.
Chrome cookies stay disabled; Yuanbao login comes only from the saved DPAPI secret.
Prints no cookie values. Writes a JSON summary next to the download folder.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

SAMPLES = [
    ("B站", "单视频", "https://www.bilibili.com/video/BV1xx411c7mD?p=1"),
    ("B站", "多P（取全部）", "https://www.bilibili.com/video/BV1qCDiB4Esa"),
    ("B站", "合集", "https://space.bilibili.com/454617261/lists/9508534?type=season"),
    ("B站", "图文 Opus（应被拦截或完成）", "https://www.bilibili.com/opus/1189486481077436417"),
    ("抖音", "普通视频", "https://www.douyin.com/video/7705270054837536847"),
    ("抖音", "普通图文", "https://www.douyin.com/note/7703017496857514105"),
    ("抖音", "实况图", "https://www.douyin.com/video/7488897845194460425"),
    ("抖音", "精选视频", "https://www.douyin.com/video/7705457259534486822"),
    ("快手", "普通视频", "https://v.kuaishou.com/K4HGpAeI"),
    ("快手", "横版图集", "https://v.kuaishou.com/nnsTg7n9"),
    ("快手", "竖版长图（已知仅切片）", "https://v.kuaishou.com/EYhNCZ"),
    # 2026-11-11 实测：下方图文短链已过期（预览返回网络错误类提示，行为正确），
    # 视频样本 ID 非法（产品正确拒绝）；运行前请换成新复制的分享链接。
    ("小红书", "图文（短链样本已过期）", "https://xhslink.com/AH3CnLCUzyN"),
    ("小红书", "视频（样本ID无效）", "https://www.xiaohongshu.com/explore/8FugXY6yiE"),
    ("小红书", "实况图", "https://xhslink.cn/o/2JXQbZCrUf9"),
    ("视频号", "用户样本（需已保存元宝登录态）", "https://weixin.qq.com/sph/A2uumghDtJ"),
]

PER_JOB_TIMEOUT = 900
TERMINAL = {"completed", "partial", "failed", "cancelled"}


def probe(path: Path) -> dict:
    # 正文等非媒体产物没有流信息，ffprobe 必然失败；直接标注跳过。
    if path.suffix.lower() in {".txt", ".json", ".md"}:
        return {"ok": None, "skipped": "non-media file"}
    ffprobe = shutil.which("ffprobe") or str(ROOT / "tools" / "ffprobe.exe")
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries",
             "format=duration,size:stream=codec_type,width,height",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": type(exc).__name__}
    if out.returncode != 0:
        return {"ok": False, "error": "ffprobe_failed"}
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams", [])
    return {
        "ok": True,
        "size": path.stat().st_size,
        "streams": sorted({s.get("codec_type", "?") for s in streams}),
        "duration": (data.get("format") or {}).get("duration"),
    }


def run_sample(client: TestClient, url: str) -> dict:
    preview = client.post("/api/preview", json={"url": url})
    if preview.status_code != 200:
        return {"stage": "preview", "http": preview.status_code,
                "detail": str(preview.json().get("detail", ""))[:200]}
    items = preview.json().get("items", [])
    created = client.post("/api/jobs", json={"url": url, "auto_start": True})
    if created.status_code != 201:
        return {"stage": "create", "http": created.status_code}
    job_id = created.json()["id"]
    deadline = time.time() + PER_JOB_TIMEOUT
    while True:
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] in TERMINAL or time.time() > deadline:
            break
        time.sleep(2)
    result_items = []
    for item in payload.get("items", []):
        files = []
        for p in item.get("output_paths", []):
            files.append({"name": Path(p).name, **probe(Path(p))})
        result_items.append({
            "title": str(item.get("title", ""))[:60],
            "status": item.get("status"),
            "error": str(item.get("error") or item.get("message") or "")[:160],
            "files": files,
        })
    return {"stage": "done", "job_status": payload["status"],
            "preview_items": len(items), "items": result_items}


def main_run() -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "cache" / f"platform-matrix-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    # The context manager runs the app lifespan, which the workers need.
    with TestClient(main.app, base_url="http://localhost") as client:
        cfg = client.get("/api/config").json()
        cfg.update({"download_dir": str(out_dir / "downloads"),
                    "use_chrome_cookies": False})
        put = client.put("/api/config", json=cfg)
        print("config", put.status_code, flush=True)
        for platform, kind, url in SAMPLES:
            print(f"== {platform} / {kind}", flush=True)
            try:
                result = run_sample(client, url)
            except Exception as exc:  # keep going; record the class only
                result = {"stage": "exception", "type": type(exc).__name__}
            result.update({"platform": platform, "kind": kind, "url": url})
            summary.append(result)
            print(json.dumps({k: v for k, v in result.items() if k != "url"},
                             ensure_ascii=False), flush=True)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"SUMMARY {out_dir / 'summary.json'}", flush=True)
    # Worker threads may be non-daemon; exit explicitly once the summary is written.
    import os
    os._exit(0)


if __name__ == "__main__":
    main_run()
