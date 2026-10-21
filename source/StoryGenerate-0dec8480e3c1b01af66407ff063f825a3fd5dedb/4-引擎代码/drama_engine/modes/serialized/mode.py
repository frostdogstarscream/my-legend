"""Serialized Mode: continuous storytelling engine.

Core responsibilities:
  - Episode goal + local arc + series-level unresolved threads
  - Continuation hook / cliffhanger support
  - Respects ending_preference (cliffhanger vs closed vs open)
  - NOT generation_scale = long_form — must work with standard too

Key distinction:
  serialized = HOW the story is told (continuous, threaded)
  long_form  = HOW MUCH to generate (multi-chapter job)
  They are orthogonal: serialized+standard and general+long_form both valid.
"""
from __future__ import annotations

from ..base import ModeProfile, StoryMode
from .profile import SERIALIZED_PROFILE


class SerializedMode:
    """连续剧/连载模式。

    核心：跨集主线推进 + 未解决线程 + 延续问题。
    不等于 long_form：可以与 standard scale 一起使用。
    """

    key: str = SERIALIZED_PROFILE["key"]

    def profile(self) -> ModeProfile:
        return ModeProfile(**SERIALIZED_PROFILE)

    def validate_request(self, brief: object) -> None:
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