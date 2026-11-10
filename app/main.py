from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .browser import open_chrome
from .build_info import APP_ID, APP_VERSION, BUILD_ID, calculate_build_id
from .downloader import DownloaderConfig, MediaDownloader
from .desktop import open_folder_foreground, open_saved_file, saved_file_facts
from .errors import SiteIssueCode
from .environment import check_environment
from .event_stream import JobEventBuffer
from .models import DownloadJob, ItemStatus, JobStatus, Platform, SourceKind
from .platforms import UnsupportedUrlError, identify_url
from .runtime import (
    RUNTIME_STOP_EVENT,
    STOP_TOKEN_HEADER,
    current_runtime_identity,
)
from .storage import JobNotFoundError
from .task_manager import (
    DownloadManager,
    ItemNotFoundError,
    ItemNotRetryableError,
    JobBusyError,
)
from .engine.common import safe_external_error_message
from .xiaohongshu import (
    is_trusted_xiaohongshu_note_url,
    is_trusted_xiaohongshu_profile_url,
    xiaohongshu_note_id,
    xiaohongshu_profile_id,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = DATA_DIR / "config.json"
DEFAULT_DOWNLOAD_DIR = PROJECT_ROOT / "downloads"
PUBLIC_DOUYIN_MEDIA_FIELDS = frozenset(
    {
        "asset_count",
        "author",
        "create_time",
        "duration_ms",
        "image_count",
        "live_photo_count",
        "media_id",
        "media_kind",
        "media_type",
        "minimum_height",
        "minimum_width",
        "owner_id",
        "title",
    }
)
PUBLIC_SENSITIVE_QUERY_FIELDS = frozenset({"xsec_token"})
_CONFIG_LOCK = threading.RLock()
CONFIG_RECOVERY: dict[str, object] | None = None


class AppConfig(BaseModel):
    download_dir: str = str(DEFAULT_DOWNLOAD_DIR)
    use_chrome_cookies: bool = False
    chrome_profile: str | None = None

    @field_validator("download_dir")
    @classmethod
    def validate_download_dir(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Download directory cannot be empty")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return str(path.resolve())


class CreateJobRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    auto_start: bool = True
    # Set by the UI when the user explicitly chose "仍然下载" for a duplicate.
    force: bool = False
    item_ids: list[str] | None = Field(default=None, min_length=1)


class PreviewRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4096)


class StartJobRequest(BaseModel):
    item_ids: list[str] | None = Field(default=None, min_length=1)


class RetryRequest(BaseModel):
    item_id: str | None = None


def _load_config() -> AppConfig:
    global CONFIG_RECOVERY
    if not CONFIG_PATH.is_file():
        return AppConfig()
    try:
        return AppConfig.model_validate_json(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        # Never let a corrupt config silently pretend the user's settings were
        # never saved: move the bad file aside and tell the UI what happened.
        backup = CONFIG_PATH.with_name(
            f"config.corrupt-{time.strftime('%Y%m%d-%H%M%S')}.json"
        )
        try:
            CONFIG_PATH.replace(backup)
        except OSError:
            backup = None
        CONFIG_RECOVERY = {
            "config_recovered": True,
            "config_backup": str(backup) if backup else None,
            "message": (
                "配置文件无法读取，本次已使用默认设置。"
                + (f"原文件已备份为 {backup.name}。" if backup else "")
                + "请重新设置保存目录和登录选项。"
            ),
        }
        return AppConfig()


def _save_config(config: AppConfig) -> None:
    # Windows can reject concurrent replacements even with distinct source files.
    with _CONFIG_LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=".config-", suffix=".tmp", dir=CONFIG_PATH.parent
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(config.model_dump(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(CONFIG_PATH)
        finally:
            temporary.unlink(missing_ok=True)


config = _load_config()
manager = DownloadManager(
    state_dir=DATA_DIR / "state",
    default_output_root=config.download_dir,
    organized_output=True,
    downloader_config=DownloaderConfig(
        filename_limit=96,
        anonymous_douyin=True,
        temporary_root=str(PROJECT_ROOT / "cache"),
        cookie_browser="chrome" if config.use_chrome_cookies else None,
        cookie_profile=config.chrome_profile,
    ),
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    # 视频号代理已移除，关闭时无需额外清理。
    
    manager.shutdown(wait=True, cancel_running=True)


app = FastAPI(
    title="Original Media Downloader",
    version=APP_VERSION,
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost"],
)


@app.middleware("http")
async def local_mutations_only(request: Request, call_next):
    # Local apps still need protection from cross-site form/fetch requests.
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        from fastapi.responses import JSONResponse
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site" or (
            origin and origin != str(request.base_url).rstrip("/")
        ):
            return JSONResponse({"detail": "只接受本机拾影页面的操作。"}, status_code=403)
    return await call_next(request)


class OpenFolderRequest(BaseModel):
    model_config = {"extra": "forbid"}

    job_id: str | None = Field(default=None, min_length=1, max_length=128)
    wechat: bool = False
    prepare_only: bool = False


def _resolve_within(path: Path, roots: list[Path]) -> Path:
    """Resolve a path and refuse anything outside the task's own output roots."""

    resolved = path.expanduser().resolve()
    for root in roots:
        try:
            resolved.relative_to(root.expanduser().resolve())
        except (ValueError, OSError):
            continue
        return resolved
    raise HTTPException(400, "该目录不在任务的保存范围内，已拒绝打开。")


@app.post("/api/folder/open")
def open_download_folder(body: OpenFolderRequest):
    if body.job_id and not body.wechat:
        try:
            job = manager.get_job(body.job_id)
            files = [Path(path) for item in job.items for path in item.output_paths]
            # Prefer the folder that actually holds the saved file. A task may
            # legitimately live outside its originally recorded output_dir after
            # the user changes the download directory, so no containment check
            # applies here; the path comes from job state, not from the request.
            target = files[0].parent if len(files) == 1 else job.output_path
        except HTTPException:
            raise
        except Exception as exc:
            raise _http_error(exc) from exc
    elif body.job_id:
        from .wechat import list_jobs as wechat_jobs
        job = next((j for j in wechat_jobs() if j["id"] == body.job_id), None)
        if job is None:
            raise HTTPException(404, "没有找到该任务。")
        recorded_root = job.get("output_root")
        if not recorded_root:
            raise HTTPException(400, "该旧任务没有记录保存目录，无法打开。")
        roots = [Path(recorded_root)]
        raw = Path(job["path"]).parent if job.get("path") else roots[0]
        target = _resolve_within(raw, roots)
    else:
        target = Path(get_config().download_dir)
    try:
        target.mkdir(parents=True, exist_ok=True)
        if body.prepare_only:
            return {"status": "ready", "path": str(target.resolve())}
        foreground = open_folder_foreground(str(target.resolve()))
    except OSError as exc:
        raise HTTPException(400, "无法打开文件夹，请检查目录是否可用。") from exc
    return {"status": "opened", "path": str(target.resolve()), "foreground": foreground}


class OpenSavedFileRequest(BaseModel):
    model_config = {"extra": "forbid"}

    job_id: str = Field(min_length=1, max_length=128)
    item_id: str | None = None
    file_index: int = Field(default=0, ge=0, strict=True)
    wechat: bool = False
    reveal: bool = False


@app.post("/api/files/open")
def open_job_file(body: OpenSavedFileRequest):
    if body.wechat:
        from .wechat import list_jobs as wechat_jobs

        job = next((job for job in wechat_jobs() if job["id"] == body.job_id), None)
        if job is None:
            raise HTTPException(404, "没有找到该任务。")
        if not job.get("path") or body.file_index != 0:
            raise HTTPException(404, "没有找到该任务的输出文件。")
        if not job.get("output_root"):
            raise HTTPException(400, "该旧任务没有记录保存范围，无法安全打开文件。")
        path = str(_resolve_within(Path(job["path"]), [Path(job["output_root"])]))
    else:
        try:
            job = manager.get_job(body.job_id)
        except Exception as exc:
            raise _http_error(exc) from exc
        item = next((item for item in job.items if item.id == body.item_id), None)
        if item is None:
            raise HTTPException(404, "没有找到该任务中的作品。")
        if body.file_index >= len(item.output_paths):
            raise HTTPException(404, "没有找到该作品的输出文件。")
        path = item.output_paths[body.file_index]
    try:
        open_saved_file(path, reveal=body.reveal)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise HTTPException(404, "文件已移动或删除，下载记录仍然保留。") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(400, "无法打开文件，请检查文件权限或系统默认应用。") from exc
    return {"status": "revealed" if body.reveal else "opened", "path": path}


@app.middleware("http")
async def disable_runtime_asset_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, (JobNotFoundError, ItemNotFoundError)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (JobBusyError, ItemNotRetryableError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, UnsupportedUrlError):
        return HTTPException(status_code=422, detail=str(exc))
    # Unknown exceptions reach the UI only through the shared sanitizer, so
    # stack-adjacent details (local paths, signed URLs, proxies) never leak.
    return HTTPException(status_code=400, detail=safe_external_error_message(exc))


def _redact_public_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.hostname in {"kuaishou.com", "www.kuaishou.com", "v.kuaishou.com", "m.gifshow.com"}:
            return urlunsplit(parsed._replace(query="", fragment=""))
    except (TypeError, ValueError):
        pass
    if "xsec_token" not in value.lower():
        return value
    try:
        parsed = urlsplit(value)
        filtered_query = [
            (name, item)
            for name, item in parse_qsl(parsed.query, keep_blank_values=True)
            if name.lower() not in PUBLIC_SENSITIVE_QUERY_FIELDS
        ]
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(filtered_query, doseq=True),
                parsed.fragment,
            )
        )
    except (TypeError, ValueError):
        return re.sub(
            r"([?&])xsec_token=[^&#]*&?",
            lambda match: match.group(1) if match.group(0).endswith("&") else "",
            value,
            flags=re.IGNORECASE,
        )


def _redact_public_value(value):
    if isinstance(value, str):
        return _redact_public_url(value)
    if isinstance(value, list):
        return [_redact_public_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_public_value(item) for item in value)
    if isinstance(value, dict):
        return {name: _redact_public_value(item) for name, item in value.items()}
    return value


def _public_job(job: DownloadJob) -> DownloadJob:
    public = job.model_copy(deep=True)
    public.source_url = _redact_public_url(public.source_url)
    if public.verification_url:
        public.verification_url = _redact_public_url(public.verification_url)
    for item in public.items:
        item.source_url = _redact_public_url(item.source_url)
        item.metadata = _redact_public_value(item.metadata)
        item.metadata.pop("wechat_media_url", None)
        item.metadata.pop("wechat_decode_key", None)
        if item.output_paths:
            files = saved_file_facts(item.output_paths)
            item.metadata["output_files"] = files
            item.metadata.pop("output_size_bytes", None)
            if files and all(file["exists"] is True for file in files):
                item.metadata["output_size_bytes"] = sum(file["size_bytes"] for file in files)
        else:
            item.metadata.pop("output_files", None)
            item.metadata.pop("output_size_bytes", None)
        for key in ("douyin_item_media", "douyin_profile_media"):
            cached = item.metadata.get(key)
            if isinstance(cached, dict):
                item.metadata[key] = {
                    name: value
                    for name, value in cached.items()
                    if name in PUBLIC_DOUYIN_MEDIA_FIELDS
                }
            elif key in item.metadata:
                item.metadata.pop(key, None)
    return public


@app.get("/api/health")
def health() -> dict[str, object]:
    source_build_id = calculate_build_id()
    instance_id, _, server_port = current_runtime_identity()
    recovery: dict[str, object] = dict(CONFIG_RECOVERY or {})
    unreadable = len(getattr(manager, "load_warnings", None) or [])
    if unreadable:
        recovery["unreadable_job_files"] = unreadable
        recovery.setdefault(
            "message",
            f"有 {unreadable} 个任务文件无法读取，已跳过；其余任务正常。",
        )
    payload: dict[str, object] = {
        "status": "ok",
        "app_id": APP_ID,
        "version": APP_VERSION,
        "build_id": BUILD_ID,
        "source_build_id": source_build_id,
        "restart_required": source_build_id != BUILD_ID,
        "instance_id": instance_id,
        "server_pid": os.getpid(),
        "server_port": server_port,
        "project_root": str(PROJECT_ROOT),
    }
    if recovery:
        payload["recovery"] = recovery
    return payload


def _request_process_stop() -> None:
    # Let the HTTP response leave the socket before Uvicorn begins shutdown.
    time.sleep(0.1)
    RUNTIME_STOP_EVENT.set()


def _stop_token_matches(supplied_token: str, expected_token: str) -> bool:
    """常时比较前先拒绝非 ASCII：compare_digest 对其抛 TypeError
    （请求头按 latin-1 解码可含 U+0080-U+00FF），会把本应 403 的
    探测变成 500。"""
    if not supplied_token or not supplied_token.isascii():
        return False
    return secrets.compare_digest(supplied_token, expected_token)


@app.post("/api/runtime/stop")
def stop_runtime(request: Request) -> dict[str, str]:
    client_host = request.client.host if request.client is not None else ""
    if client_host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Runtime stop is local-only")

    instance_id, expected_token, _ = current_runtime_identity()
    supplied_token = request.headers.get(STOP_TOKEN_HEADER, "")
    if not expected_token:
        raise HTTPException(
            status_code=503,
            detail="This runtime was not started with a stop token",
        )
    if not _stop_token_matches(supplied_token, expected_token):
        raise HTTPException(status_code=403, detail="Invalid runtime stop token")

    threading.Thread(
        target=_request_process_stop,
        name="runtime-stop",
        daemon=True,
    ).start()
    return {
        "status": "stopping",
        "instance_id": instance_id,
    }


@app.get("/api/config", response_model=AppConfig)
def get_config() -> AppConfig:
    with _CONFIG_LOCK:
        return config.model_copy(deep=True)


@app.get("/api/environment")
def environment_status() -> dict:
    return check_environment(get_config().download_dir)


CACHE_DIR = PROJECT_ROOT / "cache"
# Only jobs with a live worker block cache clean-up. Paused or interrupted jobs
# keep resume data, but clearing it is an explicit user choice, not a hard block.
_CACHE_BUSY_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.DISCOVERING,
    JobStatus.DOWNLOADING,
}


def _cache_usage() -> tuple[int, int]:
    files = 0
    total = 0
    if not CACHE_DIR.is_dir():
        return 0, 0
    for path in CACHE_DIR.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                files += 1
                total += path.stat().st_size
        except OSError:
            continue
    return files, total


@app.get("/api/cache")
def cache_status() -> dict:
    files, total = _cache_usage()
    jobs = manager.list_jobs()
    busy = sum(1 for job in jobs if job.status in _CACHE_BUSY_STATUSES)
    paused = sum(
        1
        for job in jobs
        if job.status in {JobStatus.PAUSED, JobStatus.INTERRUPTED, JobStatus.NEEDS_AUTH}
    )
    return {
        "path": str(CACHE_DIR),
        "files": files,
        "bytes": total,
        "busy_jobs": busy,
        "paused_jobs": paused,
        "can_clear": busy == 0,
    }


@app.post("/api/cache/clear")
def clear_cache() -> dict:
    # 忙检查与条目快照在管理器锁内完成；慢速的删除 IO 在锁外按快照执行。
    # 快照之后才创建的缓存条目不在快照里，不会被误删——"提交即清缓存"
    # 的 TOCTOU 仍被排除，而 /api/jobs 等接口不再被数秒的磁盘 IO 阻塞。
    with manager._lock:
        busy = [job for job in manager.list_jobs() if job.status in _CACHE_BUSY_STATUSES]
        if busy:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"还有 {len(busy)} 个任务正在下载，请先完成或取消它们再清理。"
                ),
            )
        _, freed = _cache_usage()
        snapshot = list(CACHE_DIR.iterdir()) if CACHE_DIR.is_dir() else []
    for entry in snapshot:
        try:
            if entry.is_symlink() or entry.is_file():
                entry.unlink(missing_ok=True)
            else:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue
    return {"freed_bytes": freed, "files": _cache_usage()[0]}


@app.put("/api/config", response_model=AppConfig)
def update_config(request: AppConfig) -> AppConfig:
    global config
    request = request.model_copy(deep=True)
    output_dir = Path(request.download_dir)
    with _CONFIG_LOCK:
        if request.chrome_profile != config.chrome_profile:
            profile = (request.chrome_profile or "").strip()
            if profile and not re.fullmatch(r"(?:Default|Profile [1-9][0-9]*)", profile):
                raise HTTPException(
                    status_code=422,
                    detail="Chrome Profile must be Default or Profile N",
                )
            request.chrome_profile = profile or None
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Cannot create download directory: {exc}",
            ) from exc
        try:
            _save_config(request)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Could not save settings. Check free disk space and write "
                    "permissions for the project's data folder, then save again. "
                    "The previous settings are still in use."
                ),
            ) from exc
        config = request
        manager.default_output_root = output_dir
        manager.downloader_config.cookie_browser = (
            "chrome" if request.use_chrome_cookies else None
        )
        manager.downloader_config.cookie_profile = request.chrome_profile
        return config.model_copy(deep=True)


@app.post("/api/jobs", status_code=201)
def create_job(request: CreateJobRequest):
    snapshot = get_config()
    try:
        create_kwargs = {
            "output_root": snapshot.download_dir,
            "cookie_browser": "chrome" if snapshot.use_chrome_cookies else None,
            "cookie_profile": snapshot.chrome_profile,
            "auto_start": request.auto_start,
        }
        if request.item_ids is not None:
            create_kwargs["target_item_ids"] = request.item_ids
        return _public_job(manager.create_job(request.url, **create_kwargs))
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/preview")
def preview_link(request: PreviewRequest):
    snapshot = get_config()
    try:
        url_info = identify_url(request.url)
        if url_info.kind == SourceKind.PROFILE:
            raise HTTPException(
                status_code=422,
                detail=(
                    "主页作品较多，逐条预览会让页面等待很久。请直接点击"
                    "「解析并下载」，任务列表会显示解析进度，可以随时暂停。"
                ),
            )
        engine = MediaDownloader(
            config=DownloaderConfig(
                cookie_browser="chrome" if snapshot.use_chrome_cookies else None,
                cookie_profile=snapshot.chrome_profile,
                anonymous_douyin=True,
            )
        )
        result = engine.discover(url_info.url, url_info.platform, url_info.kind)
        items_data = [
            {
                "id": item.id,
                "title": item.title,
                "author": item.author,
                "media_type": item.media_type.value,
                "resolution": item.resolution,
                "playlist_index": item.playlist_index,
            }
            for item in result.items
        ]
        return {
            "platform": url_info.platform.value,
            "author": result.author,
            "total_items": len(result.items),
            "items": items_data,
            "warning": result.warning,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/jobs/{job_id}/start")
def start_job_with_selection(job_id: str, request: StartJobRequest):
    try:
        return _public_job(manager.start_job(job_id, target_item_ids=request.item_ids))
    except Exception as exc:
        raise _http_error(exc) from exc


def _source_key(platform: Platform, url: str) -> str:
    """Stable identity for duplicate detection, ignoring tracking parameters.

    主机名小写并剥掉 "www." 前缀：www.xiaohongshu.com 与 xiaohongshu.com
    必须判为同一作品，否则重复检测会静默放行第二个任务。
    """

    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if platform == Platform.YOUTUBE:
        video_id = dict(parse_qsl(parsed.query)).get("v")
        if video_id:
            return f"youtube:{video_id}"
        if path.startswith("/shorts/"):
            return f"youtube:shorts:{path.rsplit('/', 1)[-1]}"
        if host == "youtu.be" and path:
            # youtu.be/<id> 与 watch?v=<id> 是同一作品。
            return f"youtube:{path.lstrip('/')}"
    return f"{platform.value}:{host}{path}"


@app.get("/api/jobs/exists")
def find_existing_job(url: str):
    """Return an existing task for the same canonical source, if any."""

    try:
        info = identify_url(url)
    except Exception:
        return {"exists": False, "job": None}
    wanted = _source_key(info.platform, info.url)
    for job in manager.list_jobs():
        if job.platform != info.platform:
            continue
        if _source_key(job.platform, job.source_url) != wanted:
            continue
        return {
            "exists": True,
            "job": {
                "id": job.id,
                "title": next(
                    (item.title for item in job.items if item.title),
                    job.source_url,
                ),
                "status": job.status.value,
                "platform": job.platform.value,
                "created_at": job.created_at.isoformat(),
            },
        }
    return {"exists": False, "job": None}


@app.get("/api/jobs")
def list_jobs():
    return [_public_job(job) for job in manager.list_jobs()]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    try:
        return _public_job(manager.get_job(job_id))
    except Exception as exc:
        raise _http_error(exc) from exc


THUMBNAILS_DIR = PROJECT_ROOT / "data" / "thumbnails"
THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _safe_thumbnail_path(job_id: str) -> Path:
    """Reject job ids that could escape the thumbnail directory.

    Mirrors ``JobStore._SAFE_ID`` so a valid job id always yields a valid
    thumbnail path and nothing else does.
    """
    if not _JOB_ID_RE.fullmatch(job_id or ""):
        raise HTTPException(400, "Invalid job id")
    return THUMBNAILS_DIR / f"{job_id}.jpg"


_THUMBNAIL_GENERATION_LOCKS: dict[str, threading.Lock] = {}
_THUMBNAIL_GENERATION_LOCKS_GUARD = threading.Lock()


def _thumbnail_generation_lock(key: str) -> threading.Lock:
    with _THUMBNAIL_GENERATION_LOCKS_GUARD:
        lock = _THUMBNAIL_GENERATION_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THUMBNAIL_GENERATION_LOCKS[key] = lock
        return lock


def _verify_thumbnail_decodable(path: Path) -> bool | None:
    """Fully decode a thumbnail candidate to prove it is not truncated.

    Magic bytes and nonzero size only prove the head is intact; a partial
    write can still leave a file that serves as 200 for a full day. When no
    decoder is available the answer is ``None`` (unknown), which callers
    treat conservatively instead of deleting or long-caching the file.
    """
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
    except OSError:
        return False
    ffmpeg = MediaDownloader._find_ffmpeg_executable()
    if not ffmpeg:
        return None
    try:
        result = subprocess.run(
            [
                ffmpeg, "-v", "error", "-xerror", "-err_detect", "explode",
                "-i", str(path), "-f", "null", "-",
            ],
            capture_output=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return result.returncode == 0


def _extract_video_thumbnail(video_path: Path, output_path: Path) -> bool:
    if not video_path.is_file():
        return False
    with _thumbnail_generation_lock(output_path.name):
        return _extract_video_thumbnail_locked(video_path, output_path)


def _extract_video_thumbnail_locked(video_path: Path, output_path: Path) -> bool:
    """Extract and atomically publish a thumbnail; the caller holds the lock."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _verify_thumbnail_decodable(output_path)
    if existing:
        return True
    if existing is False:
        # A corrupt cache is a derived file, safe to drop and regenerate.
        output_path.unlink(missing_ok=True)
    ffmpeg = MediaDownloader._find_ffmpeg_executable()
    if not ffmpeg:
        return False
    temp_path = output_path.with_name(f"{output_path.name}.{secrets.token_hex(4)}.tmp")
    try:
        for ss in ("00:00:01", "00:00:00"):
            cmd = [
                ffmpeg, "-ss", ss, "-i", str(video_path),
                "-vframes", "1", "-vf", "scale=360:-1", "-q:v", "3",
                str(temp_path), "-y",
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, timeout=10)
            except (subprocess.TimeoutExpired, OSError):
                continue
            if (
                res.returncode == 0
                and temp_path.is_file()
                and temp_path.stat().st_size > 0
                and _verify_thumbnail_decodable(temp_path)
            ):
                # Atomic publish: concurrent readers observe either the old
                # cache or the complete new one, never a partial write.
                os.replace(temp_path, output_path)
                return True
        return False
    finally:
        temp_path.unlink(missing_ok=True)


def _is_safe_cover_url(value: str) -> bool:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return False
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"}:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        if parsed.port not in (None, 80, 443):
            return False
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (
            not hostname
            or hostname in {"localhost", "loopback"}
            or hostname.endswith(".local")
            or hostname.endswith(".internal")
        ):
            return False
        try:
            ip = ipaddress.ip_address(hostname)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            ):
                return False
        except ValueError:
            pass
        return True
    except Exception:
        return False


def _publish_cover_cache_bytes(content: bytes, cache_path: Path) -> bool:
    """Atomically publish freshly fetched cover bytes into the derived cache.

    A same-directory temp file plus ``os.replace`` means a concurrent reader
    observes either the old cache or the complete new one, never a partial.
    Only bytes that pass the magic-byte sniff check are kept.
    """
    if not content:
        return False
    temp_path = cache_path.with_name(f"{cache_path.name}.{secrets.token_hex(4)}.tmp")
    try:
        temp_path.write_bytes(content)
        os.replace(temp_path, cache_path)
        return True
    except OSError:
        return False
    finally:
        with contextlib.suppress(OSError):
            temp_path.unlink(missing_ok=True)


@app.get("/api/jobs/{job_id}/cover")
def get_job_cover(job_id: str):
    cache_path = _safe_thumbnail_path(job_id)
    if cache_path.is_file() and cache_path.stat().st_size > 0:
        decodable = _verify_thumbnail_decodable(cache_path)
        if decodable is False:
            # A corrupt cache is a derived file, safe to drop and regenerate.
            cache_path.unlink(missing_ok=True)
        else:
            # Verified, or unknown because no decoder is available: serve it.
            # Long-cache only a fully verified image; an unknown cache is
            # served short-lived so a later request re-checks and can rebuild.
            cache_control = (
                "public, max-age=86400"
                if decodable
                else "no-cache, no-store, max-age=0, must-revalidate"
            )
            return FileResponse(cache_path, media_type="image/jpeg", headers={"Cache-Control": cache_control})

    job = None
    try:
        job = manager.get_job(job_id)
    except Exception:
        pass

    if job is None:
        try:
            from .wechat import list_jobs as wechat_jobs
            for wj in wechat_jobs():
                if wj.get("id") == job_id:
                    job = wj
                    break
        except Exception:
            pass

    if job is None:
        raise HTTPException(404, "任务不存在")

    items = getattr(job, "items", None) or (job.get("items") if isinstance(job, dict) else [])

    # 1. 优先从已下载到本地的媒体文件中提取（视频截帧 / 本地图片）
    for it in items:
        output_paths = getattr(it, "output_paths", None) or (it.get("output_paths") if isinstance(it, dict) else [])
        for op in output_paths:
            fp = Path(op)
            if not fp.is_file():
                continue
            suffix = fp.suffix.lower()
            if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
                media_type = f"image/{suffix.lstrip('.')}" if suffix != ".jpg" else "image/jpeg"
                return FileResponse(fp, media_type=media_type)
            if suffix in {".mp4", ".mkv", ".webm", ".mov", ".ts", ".flv"}:
                if _extract_video_thumbnail(fp, cache_path):
                    return FileResponse(cache_path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

    # 2. 如果本地文件尚未就绪，尝试从解析出的原始 cover_url 远程获取并缓存
    for it in items:
        metadata = getattr(it, "metadata", None) or (it.get("metadata") if isinstance(it, dict) else {})
        cover_url = metadata.get("cover_url") or metadata.get("thumbnail")
        if cover_url and isinstance(cover_url, str) and _is_safe_cover_url(cover_url):
            try:
                import requests
                resp = requests.get(
                    cover_url,
                    timeout=4,
                    allow_redirects=False,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if (
                    resp.status_code == 200
                    and resp.content
                    and len(resp.content) <= 10 * 1024 * 1024
                    and (
                        resp.headers.get("Content-Type", "").startswith("image/")
                        or resp.content.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF"))
                    )
                ):
                    if _publish_cover_cache_bytes(resp.content, cache_path):
                        # Remote bytes were sniffed but not fully decoded, so
                        # serve them short-lived rather than pinning a full day
                        # of possibly-truncated content in the browser cache.
                        return FileResponse(
                            cache_path,
                            media_type="image/jpeg",
                            headers={"Cache-Control": "no-cache, no-store, max-age=0, must-revalidate"},
                        )
            except Exception:
                pass

    raise HTTPException(
        status_code=404,
        detail="暂无可用封面",
        headers={"Cache-Control": "no-cache, no-store, max-age=0, must-revalidate"},
    )


@app.delete("/api/jobs/{job_id}")
def delete_job_record(job_id: str):
    thumb = _safe_thumbnail_path(job_id)
    try:
        manager.delete_job_record(job_id)
        thumb.unlink(missing_ok=True)
        return {"deleted": True}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str, request: Annotated[RetryRequest, Body()]):
    try:
        if request.item_id:
            return _public_job(manager.retry_item(job_id, request.item_id))
        return _public_job(manager.retry_failed(job_id))
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    try:
        return _public_job(manager.cancel_job(job_id))
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str):
    try:
        return _public_job(manager.pause_job(job_id))
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    try:
        return _public_job(manager.resume_job(job_id))
    except Exception as exc:
        raise _http_error(exc) from exc


_open_chrome = open_chrome


def _canonical_xiaohongshu_verification_note_url(
    value: str,
    expected_note_id: str,
) -> str | None:
    if (
        not is_trusted_xiaohongshu_note_url(value)
        or xiaohongshu_note_id(value) != expected_note_id
    ):
        return None
    try:
        parsed = urlsplit(value)
        allowed: dict[str, str] = {}
        for name, item in parse_qsl(parsed.query, keep_blank_values=True):
            normalized_name = name.lower()
            if normalized_name not in {"xsec_token", "xsec_source"}:
                continue
            if normalized_name in allowed:
                return None
            if not item or len(item) > 2_048 or any(ord(char) < 32 for char in item):
                return None
            if normalized_name == "xsec_source" and not re.fullmatch(
                r"[A-Za-z0-9_-]{1,64}", item
            ):
                return None
            allowed[normalized_name] = item
    except (TypeError, ValueError):
        return None
    query = urlencode(
        [
            (name, allowed[name])
            for name in ("xsec_token", "xsec_source")
            if name in allowed
        ]
    )
    return urlunsplit(
        ("https", "www.xiaohongshu.com", f"/explore/{expected_note_id}", query, "")
    )


def _xiaohongshu_verification_target(job: DownloadJob) -> str | None:
    if (
        job.platform != Platform.XIAOHONGSHU
        or job.status != JobStatus.NEEDS_AUTH
        or job.verification_url is None
    ):
        return None
    verification_note_id = xiaohongshu_note_id(job.verification_url)
    if not verification_note_id:
        return None
    candidates = [item for item in job.items if item.status == ItemStatus.NEEDS_AUTH]
    if len(candidates) != 1:
        return None
    item = candidates[0]
    if str(item.media_id or "").lower() != verification_note_id:
        return None
    item_url = _canonical_xiaohongshu_verification_note_url(
        item.source_url,
        verification_note_id,
    )
    verification_url = _canonical_xiaohongshu_verification_note_url(
        job.verification_url,
        verification_note_id,
    )
    if not item_url or not verification_url:
        return None

    if job.source_kind == SourceKind.ITEM:
        bound = xiaohongshu_note_id(job.source_url) == verification_note_id
    elif job.source_kind == SourceKind.PROFILE:
        profile_id = xiaohongshu_profile_id(job.source_url)
        bound = bool(
            profile_id
            and item.metadata.get("profile_note_membership_verified") is True
            and item.metadata.get("xiaohongshu_profile_id") == profile_id
        )
    elif job.source_kind == SourceKind.SHORT_LINK:
        resolved_kind = job.resolved_source_kind
        resolved_id = str(job.resolved_source_id or "").lower()
        metadata_kind = str(
            item.metadata.get("xiaohongshu_resolved_source_kind") or ""
        )
        metadata_url = str(
            item.metadata.get("xiaohongshu_resolved_source_url") or ""
        )
        if resolved_kind == SourceKind.ITEM:
            bound = bool(
                resolved_id == verification_note_id
                and metadata_kind == SourceKind.ITEM.value
                and xiaohongshu_note_id(metadata_url) == verification_note_id
            )
        elif resolved_kind == SourceKind.PROFILE:
            metadata_profile_id = xiaohongshu_profile_id(metadata_url)
            bound = bool(
                resolved_id
                and metadata_kind == SourceKind.PROFILE.value
                and metadata_profile_id == resolved_id
                and item.metadata.get("profile_note_membership_verified") is True
                and item.metadata.get("xiaohongshu_profile_id") == resolved_id
            )
        else:
            bound = False
    else:
        bound = False
    return verification_url if bound else None


def _is_trusted_xiaohongshu_verification_source(
    source_url: str,
    source_kind: SourceKind,
) -> bool:
    if source_kind == SourceKind.PROFILE:
        return is_trusted_xiaohongshu_profile_url(source_url)
    if source_kind == SourceKind.ITEM:
        return is_trusted_xiaohongshu_note_url(source_url)
    if source_kind != SourceKind.SHORT_LINK:
        return False
    try:
        parsed = urlsplit(source_url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        return (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and parsed.port in {None, 443}
            and (hostname == "xhslink.cn" or hostname == "xhslink.com" or hostname.endswith(".xhslink.com"))
        )
    except (TypeError, ValueError):
        return False


@app.post("/api/jobs/{job_id}/verify")
def open_verification(job_id: str) -> dict[str, str]:
    try:
        job = manager.get_job(job_id)
        if job.status != JobStatus.NEEDS_AUTH or job.issue_code not in {
            None,
            SiteIssueCode.UNKNOWN,
            SiteIssueCode.LOGIN_REQUIRED,
            SiteIssueCode.VERIFICATION_REQUIRED,
        }:
            raise ItemNotRetryableError(
                "This task does not currently require Chrome login or verification"
            )
        source = identify_url(job.source_url)
        if source.platform != job.platform or source.kind != job.source_kind:
            raise UnsupportedUrlError("The original task URL is no longer verifiable")
        if (
            job.platform == Platform.XIAOHONGSHU
            and not _is_trusted_xiaohongshu_verification_source(
                source.url,
                source.kind,
            )
        ):
            raise UnsupportedUrlError("The original task URL is no longer trusted")
        if (
            job.platform == Platform.XIAOHONGSHU
            and job.status == JobStatus.NEEDS_AUTH
            and job.cookie_browser == "chrome"
            and (
                job.cookie_profile is None
                or job.cookie_profile_auto_selected
            )
        ):
            job = manager.bind_xiaohongshu_verification_profile(job_id)
        url = _xiaohongshu_verification_target(job) or source.url
        profile = job.cookie_profile if job.cookie_browser == "chrome" else None
        _open_chrome(url, profile)
        return {"status": "opened", "url": _redact_public_url(url)}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/events")
async def events() -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        messages = JobEventBuffer()
        manager.add_listener(messages.publish)
        try:
            yield ": connected\n\n"
            while not RUNTIME_STOP_EVENT.is_set():
                try:
                    jobs = await messages.take()
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                for job in jobs:
                    payload = json.dumps(
                        _public_job(job).model_dump(mode="json"),
                        ensure_ascii=False,
                    )
                    yield f"event: job\ndata: {payload}\n\n"
        finally:
            messages.close()
            manager.remove_listener(messages.publish)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    content = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    content = (
        content.replace("__APP_ID__", APP_ID)
        .replace("__APP_VERSION__", APP_VERSION)
        .replace("__BUILD_ID__", BUILD_ID)
    )
    return HTMLResponse(
        content,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


from .wechat import router as wechat_router
app.include_router(wechat_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
