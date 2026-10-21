"""General Mode 的 Profile 数据。

身份声明，不是实现代码。
"""
from drama_engine.modes.base import Capability

GENERAL_PROFILE = {
    "key": "general",
    "version": "1.0",
    "label": "通用故事",
    "description": "灵活角色 + Behavior Card + 事实账本 + Template驱动叙事 + 证据级裁判。"
                   "不强制金手指、五槽位、穿越和爆款公式。",
    "capabilities": {
        Capability.BEHAVIOR,
        Capability.FACT_LEDGER,
        Capability.STRONG_HOOK,        # weakened: 保留 Hook 概念但不强制爆款公式
        Capability.EPISODE_PLAN,
        Capability.EPISODE_WRITER,
        Capability.EVIDENCE_JUDGE,
        Capability.AUDIO_CONSTRAINTS,
        Capability.SERIES_VALIDATION,
    },
    "rule_packs": [
        "character_consistency",
        "causal_consistency",
        "story_progression",
        "audio_story_constraints",
        "continuity",
    ],
    "default_content_form": "audio_drama",
    "metadata": {
        "min_characters": 2,
        "max_characters": 6,
        "recommended_characters": 4,
        "template_driven": True,
    },
}