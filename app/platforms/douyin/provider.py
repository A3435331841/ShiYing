"""Douyin platform provider."""

from ..engine_backed import EngineBackedProvider
from ...models import Platform


class DouyinProvider(EngineBackedProvider):
    platform = Platform.DOUYIN
    output_folder = "抖音"
    session_strategy = "chrome-cookies"
    session_notes = "优先使用 Chrome 登录态；签名、双源探测与实况处理由下载引擎执行。"
    process_notes = "抖音签名、画质探测与实况处理由下载引擎执行"
