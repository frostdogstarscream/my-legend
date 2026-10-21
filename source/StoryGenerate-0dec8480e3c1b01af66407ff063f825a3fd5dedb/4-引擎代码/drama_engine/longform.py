"""Phase 7: Long-form Domain Model — Generation Scale × Chapter Planning.

Long-form 是第四个正交维度（Generation Scale），不是新的 Mode 或 Content Form:

    Story Mode × Template × Content Form × Generation Scale

本模块提供:
  - GenerationScale: standard | long_form
  - LongFormProfile: 数据驱动配置
  - LongFormPlan: 全局结构化计划
  - ChapterPlan: 单章计划
  - ChapterResult: 单章执行结果
  - LongFormContext: 跨章连续性状态
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════
# Generation Scale
# ═══════════════════════════════════════════════════════════════════
class GenerationScale(str, Enum):
    """第四个正交维度 — 生成规模。"""
    STANDARD = "standard"
    LONG_FORM = "long_form"


# ═══════════════════════════════════════════════════════════════════
# Profile
# ═══════════════════════════════════════════════════════════════════
class LongFormProfile(BaseModel):
    """数据驱动 Long-form 配置。"""
    key: str = "long_form"
    version: str = "1.0"

    target_chapters: int = 0          # 0 = 由 Planner 决定
    chapter_target_chars: int | None = None   # None = 无强制
    min_chapter_chars: int = 400

    checkpoint_every: int = 1         # 每 N 章保存 checkpoint
    allow_parallel: bool = False      # Phase 7 第一版 = False
    allow_replan: bool = False        # Phase 7 第一版 = False

    default: bool = False             # 是否默认 profile


# ═══════════════════════════════════════════════════════════════════
# Chapter Plan
# ═══════════════════════════════════════════════════════════════════
class ChapterPlan(BaseModel):
    """单章计划 — 跨章连续性的最小载体。"""
    chapter_index: int = Field(ge=1)
    title: str = ""

    goal: str = ""                    # 本章目标
    conflict: str = ""                # 本章冲突
    turning_point: str = ""           # 本章转折
    ending_state: str = ""            # 本章结束时的状态

    required_facts: list[str] = Field(default_factory=list)    # 必须引用的事实 ID
    forbidden_changes: list[str] = Field(default_factory=list) # 不得修改的事实 ID

    unresolved_threads_in: list[str] = Field(default_factory=list)  # 进入本章的未解决线索
    unresolved_threads_out: list[str] = Field(default_factory=list) # 离开本章的未解决线索

    character_focus: list[str] = Field(default_factory=list)   # 本章重点角色


# ═══════════════════════════════════════════════════════════════════
# Long-form Plan
# ═══════════════════════════════════════════════════════════════════
class LongFormPlan(BaseModel):
    """全局 Long-form 计划 — 必须先生成，再逐章执行。"""
    story_id: str | None = None

    title: str | None = None
    global_goal: str = ""
    global_conflict: str = ""
    ending_target: str = ""

    chapter_count: int = 0
    chapters: list[ChapterPlan] = Field(default_factory=list)

    # Template 影响痕迹
    template_id: str | None = None
    template_version: str | None = None

    # 全局约束
    fixed_facts: list[str] = Field(default_factory=list)
    character_roster: list[str] = Field(default_factory=list)

    # 版本
    plan_version: str = "1.0"


# ═══════════════════════════════════════════════════════════════════
# Chapter Result
# ═══════════════════════════════════════════════════════════════════
class ChapterResult(BaseModel):
    """Long-form 单章执行结果。"""
    chapter_index: int
    plan: ChapterPlan

    # 复用 EpisodeRenderResult
    render_result: dict[str, Any] = Field(default_factory=dict)
    validation_passed: bool = False
    output_guard_passed: bool = False

    # 连续性 delta
    fact_delta: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_threads: list[str] = Field(default_factory=list)

    status: str = "pending"  # pending | running | completed | failed


# ═══════════════════════════════════════════════════════════════════
# Long-form Context (跨章连续性)
# ═══════════════════════════════════════════════════════════════════
class LongFormContext(BaseModel):
    """跨章运行的连续状态载体。"""
    plan: LongFormPlan | None = None
    completed_chapters: dict[int, ChapterResult] = Field(default_factory=dict)
    current_chapter: int = 0
    fact_ledger_snapshot: dict[str, Any] = Field(default_factory=dict)
    character_states: dict[str, Any] = Field(default_factory=dict)
    unresolved_threads: list[str] = Field(default_factory=list)
    status: str = "planned"  # planned | running | completed | failed


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════
DEFAULT_LONG_FORM_PROFILE = LongFormProfile(default=True)


def resolve_long_form_profile(chapter_count: int | None = None) -> LongFormProfile:
    """解析 long_form profile。Phase 7 第一版使用默认配置。"""
    profile = DEFAULT_LONG_FORM_PROFILE.model_copy(deep=True)
    if chapter_count is not None:
        profile.target_chapters = chapter_count
    return profile