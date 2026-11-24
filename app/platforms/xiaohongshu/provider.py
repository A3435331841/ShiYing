"""Xiaohongshu platform provider."""

from ..engine_backed import EngineBackedProvider
from ...models import Platform


class XiaohongshuProvider(EngineBackedProvider):
    platform = Platform.XIAOHONGSHU
    output_folder = "小红书"
    session_strategy = "chrome-cookies"
    session_notes = "笔记与主页优先使用 Chrome 登录态；风控与短链解析由下载引擎处理。"
    process_notes = "小红书图片、视频与实况处理由下载引擎执行"
