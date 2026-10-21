"""General Mode —— Story Engine 的第二个 Mode Adapter。

原则：
- 不复制 Prompts、Validators、Graph 代码
- 只声明能力和 Profile
- 不强制 Gadget、Five-slot、穿越
- Template-driven 叙事（通过 ResolvedStoryTemplate）
"""
from __future__ import annotations

from ..base import ModeProfile, StoryMode
from .profile import GENERAL_PROFILE


class GeneralMode:
    """通用故事模式。

    适合：现实题材、职场、校园、友情、家庭、成长、轻喜剧、情感、冒险、普通悬疑等。
    不支持：金手指、强制五槽位、穿越、爆款短剧公式。

    Phase 5B: Template-driven outline - 叙事结构由 StoryTemplate 决定。
    """

    key: str = GENERAL_PROFILE["key"]

    def profile(self) -> ModeProfile:
        return ModeProfile(**GENERAL_PROFILE)

    def validate_request(self, brief: object) -> None:
        """General Mode 接受大多数请求。"""
        pass

    def supports(self, capability: str) -> bool:
        caps = self.profile().capabilities
        from ..base import Capability
        if isinstance(capability, str):
            try:
                capability = Capability(capability)
            except ValueError:
                return False
        return capability in caps