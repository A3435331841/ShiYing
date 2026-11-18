"""AI 搜索的 LLM API Key 存储。

复用微信视频号登录态的 DPAPI 加密实现（app.wechat_secret），密钥路径由
调用方注入，便于测试与将来抽离。对外只暴露“是否已配置”，绝不回显明文。
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from ..wechat_secret import load_secret, save_secret


class SearchKeyStore:
    """DPAPI 加密的搜索 API Key 存储。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def configured(self) -> bool:
        try:
            return bool(self.load())
        except OSError:
            return False

    def load(self) -> str:
        """读取明文；文件缺失返回空串。损坏密文由底层隔离并抛 OSError。"""

        try:
            return load_secret(self.path).strip()
        except OSError:
            raise

    def save(self, value: str) -> None:
        value = (value or "").strip()
        if not value:
            self.clear()
            return
        save_secret(self.path, value)

    def clear(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        # 与视频号登录态一致：清理时一并移除损坏隔离文件。
        with contextlib.suppress(FileNotFoundError):
            self.path.with_name(self.path.name + ".corrupt").unlink()
