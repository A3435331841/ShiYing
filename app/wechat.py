"""Local Channels share-link adapter.

Supports the Tencent Yuanbao cookie flow (saved or read from Chrome).
Desktop proxy capture was removed; it was never enabled.
"""
import json
import math
import re
import secrets
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor  # noqa: F401  (兼容旧导入路径)
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .platforms import extract_url
from .downloader import safe_component
from .file_publish import publish_no_replace
from .wechat_isaac import decrypt_channels_data
from .wechat_secret import load_secret, save_secret
from yt_dlp.cookies import extract_cookies_from_browser

YUANBAO_DOMAIN = 'yuanbao.tencent.com'


def _share_code(value):
    """Return the share code from a bare code or a weixin.qq.com /sph/ link, else None."""
    if not isinstance(value, str):
        return None
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        return value
    parsed = urlsplit(value)
    try:
        valid_port = parsed.port in (None, 443)
    except ValueError:
        return None
    if (parsed.scheme != "https" or parsed.hostname != "weixin.qq.com"
            or parsed.username is not None or parsed.password is not None
            or not valid_port):
        return None
    match = re.fullmatch(r"/sph/([A-Za-z0-9_-]{1,128})/?", parsed.path)
    return match.group(1) if match else None


class _SilentCookieLogger:
    def debug(self, *_args, **_kwargs):
        pass

    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


def _chrome_yuanbao_cookie() -> str:
    """Build a Cookie header from the user's local Chrome session for Yuanbao only.

    Values stay in memory: they are never logged, returned to the UI, or written to disk.
    """
    try:
        jar = extract_cookies_from_browser('chrome', logger=_SilentCookieLogger())
    except Exception as exc:
        raise WeChatUserError('无法读取 Chrome 中的元宝登录态，请先完全退出 Chrome 后重试，或在设置中手动保存 Cookie。') from exc
    now = time.time()
    pairs = []
    for cookie in jar:
        domain = cookie.domain.lower().lstrip('.')
        if domain != YUANBAO_DOMAIN and not domain.endswith('.' + YUANBAO_DOMAIN):
            continue
        if cookie.expires and float(cookie.expires) <= now:
            continue
        if not cookie.name or '\n' in cookie.value or '\r' in cookie.value:
            continue
        pairs.append(f'{cookie.name}={cookie.value}')
    return '; '.join(pairs)


def load_yuanbao_cookie() -> str:
    """Return the saved Yuanbao cookie, or fall back to the local Chrome session."""
    try:
        stored = load_secret(SECRET)
    except Exception:
        # Do not chain the original error: it may carry details of the stored value.
        raise WeChatUserError('已保存的元宝登录态无法读取，请在设置中重新保存。') from None
    if stored:
        return stored
    return _chrome_yuanbao_cookie()

router = APIRouter(prefix='/api/wechat')
ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / 'data' / 'wechat-jobs.json'
SECRET = ROOT / 'data' / 'yuanbao-cookie.txt'
LOCK = threading.RLock()
# 旧任务线程池已随统一队列迁移移除；旧记录仅保留读取、取消与删除兼容。
CANCELLATIONS = {}

try:
    JOBS = json.loads(STATE.read_text('utf-8'))
except (OSError, ValueError):
    JOBS = []
for job in JOBS:
    if job.get('status') in ('queued', 'downloading', 'cancelling', 'capturing'):
        job.update(status='interrupted', message='上次运行已中断，请重新提交链接。')


class Input(BaseModel):
    url: str = Field(max_length=4096)


class CookieInput(BaseModel):
    cookie: str = Field(max_length=32768)


class WeChatUserError(ValueError):
    """A fixed, user-facing error with no upstream response data."""


def validate_link(value):
    url = extract_url(value)
    p = urlsplit(url)
    if p.scheme != 'https' or p.port not in (None, 443) or p.hostname not in ('weixin.qq.com', 'channels.weixin.qq.com'):
        raise ValueError('请输入微信视频号的 HTTPS 分享链接。')
    if not (p.path.startswith('/sph/') or p.path.startswith('/finder-preview/pages/')):
        raise ValueError('这个地址不是视频号分享页。')
    return url


def _save_jobs(jobs):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), 'utf-8')
    temp.replace(STATE)


def change(job, **values):
    with LOCK:
        job.update(values)
        _save_jobs(JOBS)


def profile_yuanbao(url, cookie):
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Origin': 'https://yuanbao.tencent.com',
        'Referer': 'https://yuanbao.tencent.com/',
        'Cookie': cookie,
    }
    with requests.Session() as session:
        r = session.post(
            'https://yuanbao.tencent.com/api/weixin/get_parse_result',
            json={'type': 'video_channel_url', 'url': url, 'scene': 1},
            headers=headers,
            timeout=30,
            allow_redirects=False,
        )
        if r.status_code in (401, 403):
            raise WeChatUserError('腾讯元宝登录态已失效或无权访问，请更新登录态后重试。')
        if 300 <= r.status_code < 400:
            raise WeChatUserError('腾讯元宝返回了意外跳转，请检查登录态后重试。')
        r.raise_for_status()
        data = r.json().get('data') or {}
        playable = data.get('playable_url', '')
        p = urlsplit(playable)
        try:
            valid_port = p.port in (None, 443)
        except ValueError:
            valid_port = False
        if (p.scheme != 'https' or p.hostname != 'channels.weixin.qq.com'
                or p.username is not None or p.password is not None or not valid_port):
            raise WeChatUserError('腾讯未返回播放信息。图集、直播回放等非视频作品暂不支持，'
                                  '普通视频请更新腾讯元宝 Cookie 后重试。')
        query = parse_qs(p.query)
        token, eid = query.get('token', [''])[0], query.get('eid', [''])[0]
        if not token or not eid:
            raise WeChatUserError('播放凭证不完整，请更新腾讯元宝登录态后重试。')
        r = session.post(
            'https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info',
            json={'baseReq': {'generalToken': token}, 'exportId': eid},
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': playable, 'Origin': 'https://channels.weixin.qq.com'},
            timeout=30,
            allow_redirects=False,
        )
        if 300 <= r.status_code < 400:
            raise WeChatUserError('微信播放接口返回了意外跳转，请重试。')
        r.raise_for_status()
        result = r.json()
        if result.get('errCode') or (result.get('data', {}).get('errMsg') or {}).get('type'):
            raise WeChatUserError('微信未允许播放该作品，请检查链接和账号观看权限。')
        feed_info = result.get('data', {}).get('feedInfo', {})
        author_info = result.get('data', {}).get('authorInfo', {})
        # If the feed names its share code, it must be the one the user submitted.
        share_code = _share_code(url)
        if share_code and any(feed_info.get(key) not in (None, share_code) for key in ('shortUri', 'short_uri')):
            raise WeChatUserError('微信返回的作品与所提供的链接不一致，已停止下载。')
        return feed_info, author_info


def work_identity(feed, url):
    """Return a verifiable work identity: the API-returned objectId/id, or the share code of the submitted link."""
    identity = str(feed.get('objectId') or feed.get('id') or '').strip()
    if identity:
        return identity
    code = _share_code(url)
    if code:
        return f'sph:{code}'
    return ''


def _trusted_media_url(value):
    # Same hardening tier as the other platforms: bounded length and explicit
    # rejection of whitespace/control characters (urlsplit silently strips
    # tabs/newlines, which could otherwise smuggle header or path content).
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise WeChatUserError('微信未返回可验证的视频地址。')
    if re.search(r'[\s\x00-\x1f\x7f]', value):
        raise WeChatUserError('微信返回的视频地址未通过安全验证。')
    parsed = urlsplit(value)
    try:
        valid_port = parsed.port in (None, 443)
    except ValueError:
        valid_port = False
    if (parsed.scheme != 'https' or parsed.hostname != 'finder.video.qq.com'
            or parsed.username is not None or parsed.password is not None or not valid_port):
        raise WeChatUserError('微信返回的视频地址未通过安全验证。')
    return value


def media_selection(feed):
    """Return one explicitly supplied trusted stream and its associated key."""
    if not isinstance(feed, dict):
        raise WeChatUserError('微信未返回可验证的视频信息。')
    candidates = []
    for name in ('videoInfo', 'h264VideoInfo', 'h265VideoInfo'):
        value = feed.get(name)
        if isinstance(value, dict) and (value.get('url') or value.get('videoUrl')):
            candidates.append(value)
    if feed.get('videoUrl'):
        candidates.append({'videoUrl': feed['videoUrl']})
    if not candidates:
        raise WeChatUserError('微信未返回可验证的视频地址。')
    video = candidates[0]
    url = _trusted_media_url(video.get('url') or video.get('videoUrl'))
    keys = [value for value in (
        video.get('decodeKey'), video.get('decode_key'),
        feed.get('decodeKey'), feed.get('decode_key'),
    ) if value is not None and value != '']
    if any(isinstance(value, bool) or not re.fullmatch(r'[0-9]{1,20}', str(value)) for value in keys):
        raise WeChatUserError('微信返回的视频解密信息不完整。')
    parsed = [int(value) for value in keys if int(value) != 0]
    if any(key >= 2**64 for key in parsed) or len(set(parsed)) > 1:
        raise WeChatUserError('微信返回的视频解密信息不一致。')
    return url, parsed[0] if parsed else 0


def media_info(feed):
    return media_selection(feed)[0]


def download_stream_and_decrypt(video_url: str, decode_key: int, output_path: Path, cancel_event: threading.Event, progress_callback=None):
    """Download and decrypt a stream; a failed transfer never leaves its .part file behind."""
    part_path = output_path.with_name(f"{output_path.name}.{secrets.token_hex(4)}.part")
    try:
        _download_stream_and_decrypt(video_url, decode_key, output_path, part_path, cancel_event, progress_callback)
    except BaseException:
        part_path.unlink(missing_ok=True)
        Path(output_path).with_suffix('.part').unlink(missing_ok=True)
        raise


def _download_stream_and_decrypt(video_url: str, decode_key: int, output_path: Path, part_path: Path, cancel_event: threading.Event, progress_callback=None):
    """Download video stream via HTTP Range/streaming and decrypt first 128KB if decode_key present."""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    for _ in range(6):
        r = requests.get(_trusted_media_url(video_url), headers=headers, stream=True, timeout=30, allow_redirects=False)
        if r.status_code not in (301, 302, 303, 307, 308):
            break
        location = r.headers.get('Location')
        r.close()
        if not location:
            raise WeChatUserError('视频地址重定向不完整，请重试。')
        video_url = _trusted_media_url(urljoin(video_url, location))
    else:
        raise WeChatUserError('视频地址重定向次数过多，请重试。')
    with r:
        r.raise_for_status()
        total = int(r.headers.get('content-length') or 0)
        downloaded = 0

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with part_path.open('wb') as f:
            first_chunk = bytearray()
            for chunk in r.iter_content(chunk_size=65536):
                if cancel_event.is_set():
                    raise ValueError('已取消')
                if not chunk:
                    continue
                downloaded += len(chunk)
                if len(first_chunk) < 131072:
                    first_chunk.extend(chunk)
                    if len(first_chunk) >= 131072:
                        decrypted_prefix = decrypt_channels_data(first_chunk[:131072], decode_key, 131072) if decode_key else first_chunk[:131072]
                        f.write(decrypted_prefix)
                        f.write(first_chunk[131072:])
                else:
                    f.write(chunk)
                
                # Report progress even when the server sends no Content-Length:
                # byte counts and the cancel check must not depend on a known total.
                if progress_callback:
                    progress_callback(downloaded, total or None)

            if len(first_chunk) < 131072 and len(first_chunk) > 0:
                decrypted_prefix = decrypt_channels_data(first_chunk, decode_key, len(first_chunk)) if decode_key else first_chunk
                f.write(decrypted_prefix)
            if not downloaded or (total and downloaded != total):
                raise WeChatUserError('视频文件为空或传输不完整，请重试。')
        
        part_path.replace(output_path)


def verify_video(path):
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,width,height:format=duration', '-of', 'json', str(path)], capture_output=True, timeout=30)
    try:
        info = json.loads(probe.stdout or b'{}')
        duration = float(info.get('format', {}).get('duration', 0))
        video = any(s.get('codec_type') == 'video' and int(s.get('width') or 0) > 0 and int(s.get('height') or 0) > 0 for s in info.get('streams', []))
        return probe.returncode == 0 and math.isfinite(duration) and duration > 0 and video
    except (ValueError, TypeError, AttributeError):
        return False


def download(job, output):
    cancel = CANCELLATIONS.get(job['id'], threading.Event())
    try:
        if cancel.is_set():
            change(job, status='cancelled', message='已取消')
            return

        # Read outside LOCK: reading Chrome's cookie store can take seconds and must not block status or cancel.
        cookie = load_yuanbao_cookie()
        if cookie:
            # 优先使用已配置的元宝 Cookie 流程
            change(job, status='downloading', message='正在解析腾讯元宝播放信息…')
            feed, author_info = profile_yuanbao(job['url'], cookie)
            media, decode_key = media_selection(feed)
            work_id = str(feed.get('objectId') or feed.get('id') or '').strip()
            if not work_id:
                raise WeChatUserError('微信未返回作品 ID，无法确认作品身份，已停止下载。')

            author = author_info.get('nickname') or feed.get('nickname') or '未知作者'
            folder = Path(output) / '视频号' / safe_component(author, fallback='未知作者', limit=72)
            folder.mkdir(parents=True, exist_ok=True)
            stem = re.sub(r'[<>:"/\|?*\x00-\x1f]', '_', feed.get('description') or '视频号视频')[:70].rstrip('. ') or '视频号视频'
            date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            identity = safe_component(work_id, limit=32)
            path = folder / f'{date}-{stem} [{identity}].mp4'
            if path.exists():
                raise WeChatUserError('目标文件已存在，请检查已有文件，避免覆盖。')
            staging = folder / f".{job['id']}.checking.mp4"
            change(job, title=feed.get('description') or '视频号视频', author=author, output_dir=str(folder))

            def on_progress(downloaded, total):
                pct = round(downloaded / total * 100, 1) if total else None
                change(job, percent=pct, downloaded_bytes=downloaded, total_bytes=total or None, message=f'正在下载 {pct}%' if pct is not None else '正在下载…')
            download_stream_and_decrypt(media, int(decode_key), staging, cancel, on_progress)

            if not verify_video(staging):
                if staging.exists():
                    staging.rename(staging.with_suffix('.unverified'))
                raise WeChatUserError('文件未通过视频流校验，未标记为成功。')
            if cancel.is_set():
                raise ValueError('已取消')
            # A hard link fails if a concurrent task has already published this name.
            publish_no_replace(staging, path)
            change(job, status='completed', percent=100, message='下载完成，已校验视频流', path=str(path), size_bytes=path.stat().st_size)
        else:
            # The capture queue cannot yet bind a stream to the submitted link.
            # Do not change system proxy settings or report a different work as success.
            raise WeChatUserError('微信客户端捕获暂不可用：尚不能确认捕获的视频与链接一致。请在设置中配置腾讯元宝登录态后重试。')

    except Exception as exc:
        if cancel.is_set():
            change(job, status='cancelled', message='已取消，未完成文件保留在下载目录。')
            return
        message = str(exc) if isinstance(exc, WeChatUserError) else '下载失败，请检查网络、登录态和保存目录后重试。'
        change(job, status='failed', message=message)
    finally:
        with LOCK:
            CANCELLATIONS.pop(job['id'], None)


def same_origin(request):
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise HTTPException(403, '只接受本机工具页面的操作。')


@router.get('/jobs')
def list_jobs():
    from .desktop import saved_file_facts

    with LOCK:
        jobs = list(reversed([dict(j) for j in JOBS]))
    for job in jobs:
        job['output_files'] = saved_file_facts([job['path']]) if job.get('path') else []
    return jobs


@router.delete('/jobs/{job_id}')
def delete_job_record(job_id: str, request: Request):
    same_origin(request)
    with LOCK:
        job = next((j for j in JOBS if j['id'] == job_id), None)
        if job is None:
            raise HTTPException(404, '没有找到该任务。')
        if job_id in CANCELLATIONS or job.get('status') in ('queued', 'capturing', 'downloading', 'cancelling'):
            raise HTTPException(409, '任务仍在运行，请等待停止后再移除记录。')
        remaining = [j for j in JOBS if j['id'] != job_id]
        _save_jobs(remaining)
        JOBS[:] = remaining
    return {'deleted': True}


def _browser_cookie_header(cookies):
    """Build a single-line Cookie header for yuanbao.tencent.com; empty when login cookies are missing."""
    found = {}
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get('domain', '')).lower().lstrip('.')
        if domain != YUANBAO_DOMAIN and domain != 'tencent.com' and not domain.endswith('.tencent.com'):
            continue
        name, value = cookie.get('name'), cookie.get('value')
        if not name or not value or '\r' in str(value) or '\n' in str(value):
            continue
        expires = cookie.get('expires')
        if isinstance(expires, (int, float)) and expires > 0 and expires < time.time():
            continue
        if name not in found or domain == YUANBAO_DOMAIN:
            found[name] = str(value)
    if 'hy_user' not in found or 'hy_token' not in found:
        return ''
    return '; '.join(f'{name}={value}' for name, value in found.items())


def login_with_browser(timeout: int = 300):
    """Open yuanbao in an independent browser window, wait for the user to sign in, and save the login state.

    The login is performed by the user in the browser window; the cookie value is never printed or logged.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise WeChatUserError('本机缺少 Playwright，无法打开扫码登录窗口。') from exc
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel='chrome', headless=False, timeout=30000)
        except Exception:
            browser = playwright.chromium.launch(headless=False, timeout=60000)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(f'https://{YUANBAO_DOMAIN}/', timeout=60000)
            deadline = time.monotonic() + max(60, int(timeout))
            header = ''
            while time.monotonic() < deadline:
                header = _browser_cookie_header(context.cookies())
                if header:
                    break
                page.wait_for_timeout(2000)
            if not header:
                raise WeChatUserError('扫码登录超时，未检测到元宝登录态，请重试。')
            with LOCK:
                SECRET.parent.mkdir(parents=True, exist_ok=True)
                save_secret(SECRET, header)
            return {'saved': True}
        finally:
            browser.close()


_LOGIN_LOCK = threading.Lock()


@router.post('/login')
def browser_login(request: Request):
    same_origin(request)
    if not _LOGIN_LOCK.acquire(blocking=False):
        raise HTTPException(409, '已有一个扫码登录窗口在进行中，请在原窗口完成登录或等待其结束后重试。')
    try:
        return login_with_browser()
    except WeChatUserError as exc:
        raise HTTPException(400, str(exc)) from None
    finally:
        _LOGIN_LOCK.release()


@router.get('/status')
def status():
    return {
        'cookie_configured': SECRET.exists() and bool(SECRET.stat().st_size),
        # Chrome is read only at download time; this flag never touches the cookie store or any value.
        'chrome_fallback': True,
    }


@router.post('/cookie')
def save_cookie(body: CookieInput, request: Request):
    same_origin(request)
    if '\n' in body.cookie or '\r' in body.cookie:
        raise HTTPException(422, '请粘贴单行 Cookie 请求头。')
    with LOCK:
        SECRET.parent.mkdir(parents=True, exist_ok=True)
        if body.cookie.strip():
            save_secret(SECRET, body.cookie.strip())
        else:
            SECRET.unlink(missing_ok=True)
    return {'saved': True}


@router.post('/jobs', status_code=201)
def create(body: Input, request: Request):
    """Legacy creation path, retired in 0.3.0.

    Old records stay readable/cancellable/deletable, but new tasks must go
    through the unified DownloadManager so they share the queue, cancellation
    and shutdown rules with every other platform.
    """

    same_origin(request)
    raise HTTPException(
        410,
        '旧视频号任务入口已停用。请在首页粘贴链接创建任务，'
        '新的任务会使用与其他平台相同的队列、暂停和退出规则。',
    )


@router.post('/jobs/{job_id}/cancel')
def cancel_job(job_id: str, request: Request):
    same_origin(request)
    with LOCK:
        job = next((j for j in JOBS if j['id'] == job_id), None)
        if job is None:
            raise HTTPException(404, '没有找到该任务。')
        if job['status'] not in ('queued', 'discovering', 'capturing', 'downloading', 'cancelling'):
            # Finished or already stopped jobs keep their final state.
            return dict(job)
        # Create the event if it is missing so a later check of the worker still sees the request.
        CANCELLATIONS.setdefault(job_id, threading.Event()).set()
        if job['status'] != 'cancelling':
            change(job, status='cancelling', message='正在停止请求…')
        return dict(job)

