"""模型分级路由 —— Phase 1 重构。

变更:
  - 逻辑档位：FAST / BALANCED / STRONG / LONG（新）
  - 向后兼容：cheap→FAST, balanced→BALANCED, strong/reasoning→STRONG, long→LONG
  - ModelTierMap 从 Settings 读取默认模型映射
  - resolve_spec 自动附加 node_timeout_sec
"""
from __future__ import annotations

from typing import Any

from ..core.settings import Settings, get_settings
from .base import LLMSpec

# ---- 逻辑档位常量 ----
TIER_FAST = "FAST"
TIER_BALANCED = "BALANCED"
TIER_STRONG = "STRONG"
TIER_LONG = "LONG"

# 旧档位 → 新档位映射
_LEGACY_TIER_MAP: dict[str, str] = {
    "cheap": TIER_FAST,
    "balanced": TIER_BALANCED,
    "strong": TIER_STRONG,
    "reasoning": TIER_STRONG,
    "long": TIER_LONG,
    "fast": TIER_FAST,
}


def normalize_tier(tier: str) -> str:
    """将旧档位名标准化为新档位名。"""
    return _LEGACY_TIER_MAP.get(tier.lower(), tier.upper())


# 节点 role → 配置。
# tier 可使用新名（FAST/BALANCED/STRONG/LONG）或旧名（cheap/reasoning/strong）。
MODEL_ROUTING: dict[str, dict] = {
    # ===== FAST =====
    "topic_select":        {"tier": TIER_FAST,     "temperature": 0.3, "max_tokens": 8192},
    "fact_ledger":         {"tier": TIER_FAST,     "temperature": 0.2, "max_tokens": 32768},
    "judge":               {"tier": TIER_FAST,     "temperature": 0.0, "max_tokens": 16384},
    "audio_adapt":         {"tier": TIER_FAST,     "temperature": 0.4, "max_tokens": 16384},
    "performance_annotation": {"tier": TIER_FAST,  "temperature": 0.3, "max_tokens": 8192},
    "repair_json":         {"tier": TIER_FAST,     "temperature": 0.3, "max_tokens": 8192},
    "repair_audio":        {"tier": TIER_FAST,     "temperature": 0.3, "max_tokens": 8192},

    # ===== BALANCED =====
    "cast_design":         {"tier": TIER_BALANCED, "temperature": 0.6, "max_tokens": 16384},
    "repair_cast":         {"tier": TIER_BALANCED, "temperature": 0.4, "max_tokens": 8192},
    # Phase 1.5: 旧 episode_beats 已拆为 episode_plan + episode_writer
    "episode_writer":      {"tier": TIER_BALANCED, "temperature": 0.85, "max_tokens": 16384},
    "episode_beats":       {"tier": TIER_BALANCED, "temperature": 0.85, "max_tokens": 16384},
    "repair_beat":         {"tier": TIER_BALANCED, "temperature": 0.7, "max_tokens": 16384},

    # ===== STRONG =====
    "gadget_design":       {"tier": TIER_STRONG,   "temperature": 0.5, "max_tokens": 16384},
    "behavior_design":     {"tier": TIER_STRONG,   "temperature": 0.7, "max_tokens": 32768},
    "outline":             {"tier": TIER_STRONG,   "temperature": 0.5, "max_tokens": 32768},
    # Approved StoryCraft deliveries prioritize first-draft quality. Legacy
    # episode_writer calls retain their original BALANCED cost profile.
    "storycraft_episode_writer": {"tier": TIER_STRONG, "temperature": 0.7, "max_tokens": 16384},
    "storycraft_operation_judge": {"tier": TIER_STRONG, "temperature": 0.0, "max_tokens": 16384},
    # Phase 1.5: 只规划不写正文
    "episode_plan":        {"tier": TIER_STRONG,   "temperature": 0.7, "max_tokens": 8192},
    "hook_open":           {"tier": TIER_STRONG,   "temperature": 0.9, "max_tokens": 4096},
    "hook_reversal":       {"tier": TIER_STRONG,   "temperature": 0.9, "max_tokens": 4096},
    "hook_cliffhanger":    {"tier": TIER_STRONG,   "temperature": 0.95, "max_tokens": 4096},
    "repair_gadget":       {"tier": TIER_STRONG,   "temperature": 0.4, "max_tokens": 8192},
    "repair_behavior":     {"tier": TIER_STRONG,   "temperature": 0.6, "max_tokens": 16384},
    "repair_outline":      {"tier": TIER_STRONG,   "temperature": 0.4, "max_tokens": 16384},

    # ===== FAST ===== (also repair_ledger)
    "repair_ledger":       {"tier": TIER_FAST,     "temperature": 0.3, "max_tokens": 16384},
    "repair_compliance":   {"tier": TIER_LONG,     "temperature": 0.3, "max_tokens": 8192},
}

DEFAULT_ROUTE = {"tier": TIER_FAST, "temperature": 0.7, "max_tokens": 16384}


class ModelTierMap:
    """档位 → 具体模型名。

    优先级：构造函数 mapping > STORY_LLM_*_MODEL 环境变量 > DRAMA_LLM_MODEL > mock
    """

    def __init__(self, mapping: dict[str, str] | None = None,
                 settings: Settings | None = None) -> None:
        import os

        self._settings = settings or get_settings()
        env_model = os.environ.get("DRAMA_LLM_MODEL")

        self.mapping: dict[str, str] = {
            TIER_FAST:     mapping.get(TIER_FAST) if mapping else (
                self._settings.llm_fast_model or env_model or "mock"),
            TIER_BALANCED: mapping.get(TIER_BALANCED) if mapping else (
                self._settings.llm_balanced_model or env_model or "mock"),
            TIER_STRONG:   mapping.get(TIER_STRONG) if mapping else (
                self._settings.llm_strong_model or env_model or "mock"),
            TIER_LONG:     mapping.get(TIER_LONG) if mapping else (
                self._settings.llm_long_model or env_model or "mock"),
        } if mapping else {
            TIER_FAST:     self._settings.llm_fast_model or env_model or "mock",
            TIER_BALANCED: self._settings.llm_balanced_model or env_model or "mock",
            TIER_STRONG:   self._settings.llm_strong_model or env_model or "mock",
            TIER_LONG:     self._settings.llm_long_model or env_model or "mock",
        }

        # 向后兼容旧档位名
        self.mapping["cheap"] = self.mapping[TIER_FAST]
        self.mapping["reasoning"] = self.mapping[TIER_STRONG]
        self.mapping["strong"] = self.mapping[TIER_STRONG]

    def resolve(self, tier: str) -> str:
        normalized = normalize_tier(tier)
        return self.mapping.get(normalized, self.mapping.get(TIER_FAST, "mock"))

    def describe(self) -> dict[str, str]:
        return {t: self.resolve(t) for t in
                (TIER_FAST, TIER_BALANCED, TIER_STRONG, TIER_LONG)}


def resolve_spec(role: str, tier_map: ModelTierMap | None = None,
                 settings: Settings | None = None, **overrides) -> LLMSpec:
    """解析节点 → LLMSpec。

    自动附加 node_timeout_sec（从 settings.node_timeouts 读取）。
    """
    tier_map = tier_map or ModelTierMap(settings=settings)
    s = settings or get_settings()

    config: dict[str, Any] = dict(MODEL_ROUTING.get(role, DEFAULT_ROUTE))
    tier = normalize_tier(config.pop("tier"))
    model = tier_map.resolve(tier)
    config.update(overrides)

    # 自动注入 node_timeout_sec
    extra = config.setdefault("extra", {})
    if role in s.node_timeouts:
        extra.setdefault("node_timeout_sec", s.node_timeouts[role])

    return LLMSpec(role=role, model=model, tier=tier, **config)
