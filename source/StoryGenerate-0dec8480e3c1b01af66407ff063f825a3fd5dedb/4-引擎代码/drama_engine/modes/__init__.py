"""Modes: Story Engine 的模式系统。

Phase 2 只提供 viral_drama 一个 Mode。
未来通过 register_mode() 注册 General / Mystery / Novel 等。
"""
from __future__ import annotations

from .base import Capability, ModeContext, ModeProfile, StoryMode
from .registry import ModeRegistry, get_mode, register_mode, available_modes, default_mode_key

__all__ = [
    "Capability",
    "ModeContext",
    "ModeProfile",
    "StoryMode",
    "ModeRegistry",
    "get_mode",
    "register_mode",
    "available_modes",
    "default_mode_key",
]