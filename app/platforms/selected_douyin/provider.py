"""Selected Douyin platform provider."""

from ..engine_backed import EngineBackedProvider
from ...models import Platform


class SelectedDouyinProvider(EngineBackedProvider):
    platform = Platform.SELECTED_DOUYIN
    output_folder = "Selected-Douyin"
    session_strategy = "chrome-cookies"
    session_notes = "沿用抖音签名链路，但保持独立的平台身份与归档目录。"
    process_notes = "精选平台复用抖音媒体处理链路，并保持独立身份"
