"""Repository maintenance scripts with backward-compatible module aliases."""

from __future__ import annotations

import sys
from .dev import (
    douyin_item_live_smoke,
    douyin_portable_live_smoke,
    douyin_profile_live_smoke,
    platform_matrix_live,
    ui_smoke,
)
from .release import package_release

# Expose aliases in sys.modules so `from scripts.douyin_xxx import yyy` resolves to the identical module instance
sys.modules[f"{__name__}.douyin_item_live_smoke"] = douyin_item_live_smoke
sys.modules[f"{__name__}.douyin_portable_live_smoke"] = douyin_portable_live_smoke
sys.modules[f"{__name__}.douyin_profile_live_smoke"] = douyin_profile_live_smoke
sys.modules[f"{__name__}.platform_matrix_live"] = platform_matrix_live
sys.modules[f"{__name__}.ui_smoke"] = ui_smoke
sys.modules[f"{__name__}.package_release"] = package_release
