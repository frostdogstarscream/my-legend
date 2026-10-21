"""Serialized Mode Profile."""
from drama_engine.modes.base import Capability

SERIALIZED_PROFILE = {
    "key": "serialized",
    "version": "1.0",
    "label": "连续剧/连载",
    "description": "跨集主线、未解决线程、延续问题、可选悬念结尾。不等于 long_form。",
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
        "serialized_continuity",
        "serialized_hook",
        "continuity",
    ],
    "default_content_form": "prose_story",
    "metadata": {
        "min_characters": 2,
        "max_characters": 6,
        "recommended_characters": 4,
        "template_driven": True,
        "series_progression_required": True,
    },
}