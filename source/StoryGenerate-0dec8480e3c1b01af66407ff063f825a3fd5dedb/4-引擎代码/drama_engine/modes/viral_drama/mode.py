"""Viral Drama Mode —— Story Engine 的第一个 Mode Adapter。

原则：
- 不复制 Prompts、Validators、Graph 代码
- 只声明能力和 Profile
- validate_request 只在明确不兼容时抛错
"""
from __future__ import annotations

from ..base import ModeProfile, StoryMode
from .profile import VIRAL_DRAMA_PROFILE


class ViralDramaMode:
    """爆款广播剧模式。

    本类不是"重新实现"Viral Drama —— 它只是现有代码的 Mode 适配器。
    所有实际生成逻辑仍在原有的 prompts / nodes / validators 中。

    Phase 2 任务：把"这就是系统本身"变成"这是系统支持的第一个 Mode"。
    """

    key: str = VIRAL_DRAMA_PROFILE["key"]
    version: str = VIRAL_DRAMA_PROFILE["version"]

    def profile(self) -> ModeProfile:
        return ModeProfile(**VIRAL_DRAMA_PROFILE)

    def validate_request(self, brief: object) -> None:
        """Viral Drama 当前接受所有传统 brief。

        未来这里可以根据 brief 的内容判断是否应该路由到其他 Mode。
        现在先不做任何拒绝。
        """
        # 如果 brief 有 story_mode 字段但不等于 "viral_drama"，
        # 那外部调用方已经出错了，这是 Registry 的责任，不是这里的。
        pass

    # ----- 便捷查询 -----
    def supports(self, capability: str) -> bool:
        """检查本 Mode 是否支持某个 Capability。"""
        caps = self.profile().capabilities
        from ..base import Capability
        if isinstance(capability, str):
            try:
                capability = Capability(capability)
            except ValueError:
                return False
        return capability in caps