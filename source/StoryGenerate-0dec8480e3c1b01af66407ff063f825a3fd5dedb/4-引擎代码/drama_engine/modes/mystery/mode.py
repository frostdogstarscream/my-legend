"""Mystery Mode: clue-ledger-driven detective story engine.

Responsibilities:
  - Clue Ledger: structured evidence tracking (introduced, supported, revealed)
  - Fair-play: resolution must reference previously introduced clues
  - Red herring: allowed but not accepted as evidence by default
  - Open/closed ending support
"""
from __future__ import annotations

from ..base import ModeProfile, StoryMode
from .profile import MYSTERY_PROFILE


class MysteryMode:
    """悬疑推理解谜模式。

    核心差异：Clue Ledger 线索账本。
    不强制 GADGET / FIVE_SLOT_CAST。
    """

    key: str = MYSTERY_PROFILE["key"]

    def profile(self) -> ModeProfile:
        return ModeProfile(**MYSTERY_PROFILE)

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