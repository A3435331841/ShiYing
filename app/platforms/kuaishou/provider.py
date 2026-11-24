"""Kuaishou platform provider."""

from ..engine_backed import EngineBackedProvider
from ...models import Platform


class KuaishouProvider(EngineBackedProvider):
    platform = Platform.KUAISHOU
    output_folder = "快手"
    session_strategy = "chrome-cookies"
    session_notes = "需要 Chrome 网页登录态；被动监听与图集切片由下载引擎执行。"
    process_notes = "快手图集切片与视频处理由下载引擎执行"
