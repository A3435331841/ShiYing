"""Compatibility facade for job orchestration.

The implementation lives in :mod:`app.manager`. This module keeps the historical
``app.task_manager`` import surface and mirrors module attribute assignments into
the engine package so existing ``monkeypatch.setattr("app.task_manager.X", ...)``
targets keep working.
"""

from __future__ import annotations

import sys as _sys
import types as _types

from .manager import *  # noqa: F401,F403
from .manager import MODULES as _MODULES
from .manager import __all__ as _manager_all

__all__ = list(_manager_all)


class _FacadeModule(_types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        for module in _MODULES:
            if hasattr(module, name):
                setattr(module, name, value)


_sys.modules[__name__].__class__ = _FacadeModule
