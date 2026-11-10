"""Compatibility facade for the download engine.

The implementation lives in :mod:`app.engine`. This module keeps the historical
``app.downloader`` import surface (``from app.downloader import MediaDownloader``)
and keeps legacy ``monkeypatch.setattr("app.downloader.X", ...)`` targets working:
assigning an attribute here mirrors it into every engine module that defines that
name, because methods resolve those names from their own module globals.
"""

from __future__ import annotations

import sys as _sys
import types as _types

from .engine import *  # noqa: F401,F403
from .engine import MODULES as _MODULES
from .engine import __all__ as _engine_all

__all__ = list(_engine_all)


class _FacadeModule(_types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        for module in _MODULES:
            if hasattr(module, name):
                setattr(module, name, value)


_sys.modules[__name__].__class__ = _FacadeModule
