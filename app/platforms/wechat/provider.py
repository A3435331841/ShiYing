"""WeChat Channels platform provider."""

from ...models import Platform
from ..base import MediaStream, ProcessedMedia
from ..engine_backed import EngineBackedProvider


class WeChatProvider(EngineBackedProvider):
    platform = Platform.WECHAT
    output_folder = "视频号"
    session_strategy = "platform-secret"
    session_notes = "使用用户配置的腾讯元宝登录态；凭据内容不进入 SPI 会话描述。"
    process_notes = "ISAAC64 解密依赖媒体密钥，必须由下载引擎在流式下载阶段执行"

    def process_stream(
        self, stream: MediaStream, raw_data: bytes
    ) -> ProcessedMedia:
        raise RuntimeError(
            "微信视频号解密必须由下载引擎结合媒体密钥执行，不能调用通用流处理钩子"
        )
