"""Viral Drama Mode 的 Profile 数据。

这是 Viral Drama 的"身份声明"——不是实现代码。
实现放在 mode.py。
"""
from drama_engine.modes.base import Capability

VIRAL_DRAMA_PROFILE = {
    "key": "viral_drama",
    "version": "1.0",
    "label": "爆款广播剧",
    "description": "五槽位角色 + 金手指 + Behavior Card + 事实账本 + 强Hook + 证据级裁判",
    "capabilities": {
        Capability.GADGET,
        Capability.FIVE_SLOT_CAST,
        Capability.BEHAVIOR,
        Capability.FACT_LEDGER,
        Capability.STRONG_HOOK,
        Capability.EPISODE_PLAN,
        Capability.EPISODE_WRITER,
        Capability.EVIDENCE_JUDGE,
        Capability.AUDIO_CONSTRAINTS,
        Capability.SERIES_VALIDATION,
    },
    "rule_packs": [
        "drama_formula",
        "timetravel",
        "character_continuity",
    ],
    "default_content_form": "audio_drama",
}