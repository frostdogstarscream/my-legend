"""Phase 4.5C: Repair Decision Policy。

将 Finding 分为四类：
    none         — 无需修复（warning 级，不影响交付）
    deterministic — 纯代码修复（字段默认值、序号、schema 规范）
    targeted_llm — 定向 LLM 修复（只发送 affected beat/lines 上下文）
    regenerate   — 整集/整层重生成（最后手段）

配合 RepairBudget：
    max_total_repairs = 3
    max_repairs_per_stage = 1
    max_targeted_repairs = 2
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class RepairDecision:
    """修复决策枚举。"""

    none = "none"
    deterministic = "deterministic"
    targeted_llm = "targeted_llm"
    regenerate = "regenerate"


# 确定性可修复的 rule_id 集合（纯代码，不调 LLM）
DETERMINISTIC_RULES: set[str] = {
    "K01", "K02", "K12", "K13", "K14", "K15",  # 字段缺失/数量
    "R09", "R10", "R12",                          # 长度/音效/视觉
    "CH01", "CH02", "CH03", "CH04", "CH05", "CH06",  # 行为卡字段
    "RL01", "RL03", "RL04",                        # 关系字段
    "FC01", "FC02", "FC03", "FC04",               # 账本引用
    "S01", "S02",                                   # 赛道回退
}

# 不可降级为 warning 的 fatal rule_id（必须修复）
FATAL_RULES: set[str] = {
    "K01", "K02", "K06", "K11",
    "FC01", "FC02",
}

# 语义问题 → targeted LLM repair 的 rule_id（不整集重写）
TARGETED_RULES: set[str] = {
    "K06", "K08", "K16", "R13",      # 语义规则 — 可定向修复
    "EV01", "EV02", "EV03", "EV04", "EV05", "EV06",  # 证据评审 — 局部 fix
}

# 非 fatal 的 warning → 不触发 repair
NON_FATAL_WARNINGS: set[str] = {
    "K08_warn",     # 降维步骤不够强但结构完整
    "S02",           # 音频适配度过低
    "label_only",    # 轻微伪证
    "audio_adapt",   # 音效覆盖不足
}


@dataclass
class RepairBudget:
    max_total: int = 3
    max_per_stage: int = 1
    max_targeted: int = 2
    used: int = 0
    used_targeted: int = 0
    stages: dict[str, int] = field(default_factory=dict)

    def can_repair(self, stage: str, decision: str) -> bool:
        if self.used >= self.max_total:
            return False
        if decision == RepairDecision.targeted_llm and self.used_targeted >= self.max_targeted:
            return False
        if self.stages.get(stage, 0) >= self.max_per_stage:
            return False
        return True

    def record(self, stage: str, decision: str) -> None:
        self.used += 1
        self.stages[stage] = self.stages.get(stage, 0) + 1
        if decision == RepairDecision.targeted_llm:
            self.used_targeted += 1


def classify_repair(
    rule_id: str,
    severity: str = "error",
    deterministically_fixable: bool = False,
    has_default: bool = False,
) -> str:
    """根据 rule_id 和 severity 分类修复策略。

    优先级：
      1. deterministic_rules → deterministic（纯代码）
      2. targeted_rules + severity=error → targeted_llm
      3. non_fatal_warning + severity=warning → none
      4. 其他 → regenerate
    """
    # Tier 1: 确定性规则 + 有默认值 → 代码修复
    if rule_id in DETERMINISTIC_RULES and (deterministically_fixable or has_default):
        return RepairDecision.deterministic

    # Tier 2: 非 fatal 警告 → 不触发修复
    if severity == "warning" and rule_id not in FATAL_RULES:
        if rule_id in NON_FATAL_WARNINGS or "warn" in rule_id:
            return RepairDecision.none

    # Tier 3: 语义问题 → 定向 LLM 修复
    if rule_id in TARGETED_RULES:
        return RepairDecision.targeted_llm

    # Tier 4: 回退到整集重写
    return RepairDecision.regenerate


def find_offending_beats(
    findings: list[Any],
    episode_beats: list[Any],
) -> list[int]:
    """根据 findings 定位需要修复的 beat 索引。

    返回 beat 序号列表（1-based），用于 targeted repair 上下文。
    """
    affected: set[int] = set()
    for f in findings:
        loc = getattr(f, "location", "")
        if "beat" in str(loc).lower():
            try:
                import re
                nums = re.findall(r'\d+', str(loc))
                if nums:
                    affected.add(int(nums[0]))
            except (ValueError, TypeError):
                pass
    if not affected and episode_beats:
        # 如果无法定位，默认修复后半段（高潮/cliffhanger 区域）
        mid = len(episode_beats) // 2
        affected = set(range(mid + 1, len(episode_beats) + 1))
    return sorted(affected)[:5]  # 最多 5 个 beats


def build_targeted_context(
    episode: Any,
    beat_indices: list[int],
    findings: list[Any],
) -> dict:
    """构造 targeted repair 上下文：只包含 affected beats 和修复要求。

    不再发送完整 Episode 正文。
    """
    affected_beats = []
    for idx in beat_indices:
        if 1 <= idx <= len(episode.beats):
            beat = episode.beats[idx - 1]
            affected_beats.append({
                "index": idx,
                "segment": beat.segment,
                "start_sec": beat.start_sec,
                "end_sec": beat.end_sec,
                "lines": [
                    {"speaker": ln.speaker, "text": ln.text, "event": ln.event}
                    for ln in beat.lines
                ],
            })

    repair_items = []
    for f in findings:
        repair_items.append({
            "rule_id": getattr(f, "rule_id", ""),
            "severity": getattr(f, "severity", ""),
            "message": getattr(f, "message", ""),
            "repair_hint": getattr(f, "repair_hint", ""),
        })

    return {
        "episode_index": episode.episode,
        "title": getattr(episode, "title", ""),
        "affected_beats": affected_beats,
        "repair_items": repair_items,
        "forbidden_changes": [
            "character canon (name/gender/identity)",
            "ending cliffhanger",
            "emotional arc direction",
            "fact ledger references",
        ],
    }