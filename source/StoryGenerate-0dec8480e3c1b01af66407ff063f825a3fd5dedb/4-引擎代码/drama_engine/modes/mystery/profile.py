"""Mystery Mode Profile."""
from drama_engine.modes.base import Capability

MYSTERY_PROFILE = {
    "key": "mystery",
    "version": "1.0",
    "label": "悬疑推理解谜",
    "description": "线索组织、误导、证据闭环、揭示机制。不强制金手指和五槽位。",
    "capabilities": {
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
        "character_consistency",
        "causal_consistency",
        "story_progression",
        "mystery_clue",
        "mystery_fair_play",
        "continuity",
    ],
    "default_content_form": "prose_story",
    "metadata": {
        "min_characters": 2,
        "max_characters": 6,
        "recommended_characters": 4,
        "template_driven": True,
        "clue_ledger_required": True,
    },
}