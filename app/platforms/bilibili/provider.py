"""Bilibili platform provider."""

from ..engine_backed import EngineBackedProvider
from ...models import Platform


class BilibiliProvider(EngineBackedProvider):
    platform = Platform.BILIBILI
    output_folder = "B站"
    session_strategy = "chrome-cookies"
    session_notes = "匿名访问优先，可选 Chrome Cookie；多 P、专栏与分离轨处理由下载引擎执行。"
    remux_required = True
    process_notes = "音视频分离轨由 FFmpeg 无损封装，专栏图文由下载引擎执行"
