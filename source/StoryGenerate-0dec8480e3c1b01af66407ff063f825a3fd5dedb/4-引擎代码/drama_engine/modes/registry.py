"""Mode Registry: 注册和发现 Mode 实例。

核心规则：
- 用户未传 story_mode → 默认 viral_drama
- 用户传了不存在的 story_mode → MODE_NOT_SUPPORTED
- 不会偷偷 fallback

设计：
- 全局单例 ModeRegistry
- 通过便捷函数 get_mode / register_mode / available_modes 访问
"""
from __future__ import annotations

from .base import StoryMode
from ..core.errors import EngineErrorCode, StoryEngineError

# ---- 默认 Mode ----
DEFAULT_MODE_KEY = "viral_drama"


class ModeNotFoundError(StoryEngineError):
    """用户指定的 story_mode 在 Registry 中不存在。"""

    def __init__(self, mode_key: str) -> None:
        available = ", ".join(ModeRegistry._instance._modes.keys()) if ModeRegistry._instance else ""
        hint = f"可用模式: {available}" if available else "请先注册模式"
        super().__init__(
            code=EngineErrorCode.MODE_NOT_SUPPORTED,
            message=f"模式 '{mode_key}' 不支持。{hint}",
        )


def _ensure_registry() -> "ModeRegistry":
    """延迟获取或创建全局 Registry。"""
    if ModeRegistry._instance is None:
        ModeRegistry()
    return ModeRegistry._instance


# ============================================================================
# ModeRegistry
# ============================================================================
class ModeRegistry:
    """全局 Mode 注册中心。

    单例：确保整个进程内只注册一次。
    """

    _instance: "ModeRegistry | None" = None

    def __init__(self) -> None:
        if ModeRegistry._instance is not None:
            raise RuntimeError("ModeRegistry 是单例，请使用 get_mode() / register_mode()")
        self._modes: dict[str, StoryMode] = {}
        ModeRegistry._instance = self
        self._bootstrap()

    def _bootstrap(self) -> None:
        """自动注册内建 Mode。"""
        from .viral_drama.mode import ViralDramaMode
        from .general.mode import GeneralMode
        from .mystery.mode import MysteryMode      # Phase 9
        from .serialized.mode import SerializedMode  # Phase 9
        self.register(ViralDramaMode())
        self.register(GeneralMode())
        self.register(MysteryMode())               # Phase 9
        self.register(SerializedMode())             # Phase 9

    def register(self, mode: StoryMode) -> None:
        """注册一个 Mode。重复注册相同 key 会覆盖（非生产推荐，但允许测试）。"""
        self._modes[mode.key] = mode

    def get(self, key: str | None) -> StoryMode:
        """获取 Mode。

        key=None → 返回默认 viral_drama
        key=不存在 → raise ModeNotFoundError
        """
        key = key or DEFAULT_MODE_KEY
        mode = self._modes.get(key)
        if mode is None:
            raise ModeNotFoundError(key)
        return mode

    def available_keys(self) -> list[str]:
        return sorted(self._modes.keys())

    def is_registered(self, key: str) -> bool:
        return key in self._modes


# ============================================================================
# 便捷函数
# ============================================================================
def get_mode(key: str | None = None) -> StoryMode:
    """获取 Mode 实例。key=None 返回默认 viral_drama。"""
    return _ensure_registry().get(key)


def register_mode(mode: StoryMode) -> None:
    """注册新的 Mode。已在 Registry 中的 key 会覆盖。"""
    _ensure_registry().register(mode)


def available_modes() -> list[str]:
    """返回已注册的 Mode key 列表。"""
    return _ensure_registry().available_keys()


def default_mode_key() -> str:
    return DEFAULT_MODE_KEY