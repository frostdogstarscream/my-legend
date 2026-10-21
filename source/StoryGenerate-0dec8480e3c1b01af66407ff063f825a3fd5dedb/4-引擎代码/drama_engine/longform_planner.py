"""Phase 7B: Long-form Planner.

生成全局 LongFormPlan: Template → Global Arc → Chapter Distribution.

Planner 使用现有 Model Tier / Provider DI，不硬编码模型名称。
"""
from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from .longform import (
    ChapterPlan,
    GenerationScale,
    LongFormPlan,
    LongFormProfile,
    resolve_long_form_profile,
)
from .schemas import Brief


# ═══════════════════════════════════════════════════════════════════
# Long-form Planner (template-aware)
# ═══════════════════════════════════════════════════════════════════
def plan_long_form(
    brief: Brief,
    template: dict | None = None,
    character_roster: list[str] | None = None,
    profile: LongFormProfile | None = None,
    generation_scale: GenerationScale = GenerationScale.STANDARD,
    runtime: Any = None,
    config: RunnableConfig | None = None,
) -> LongFormPlan:
    """生成全局 LongFormPlan。

    Args:
        brief: 用户创意（normalized）
        template: Phase 5 resolved template
        character_roster: 全局角色列表
        profile: long_form 配置
        generation_scale: 生成规模
        runtime: Runtime with LLM provider
        config: LangGraph config
    """
    if profile is None:
        profile = resolve_long_form_profile()

    chapter_count = max(profile.target_chapters, 1)

    # Build plan using LLM (or mock)
    template_id = (template or {}).get("template_id", "")
    template_name = (template or {}).get("name", "")

    # Generate chapters
    chapters = _generate_chapter_plans(
        brief=brief,
        template_id=template_id,
        template_name=template_name,
        chapter_count=chapter_count,
        character_roster=character_roster or [],
        runtime=runtime,
        config=config,
    )

    return LongFormPlan(
        title=brief.raw_idea[:80] if brief.raw_idea else None,
        global_goal=_derive_global_goal(brief),
        global_conflict=_derive_global_conflict(brief),
        ending_target=_derive_ending_target(brief, template),
        chapter_count=len(chapters),
        chapters=chapters,
        template_id=template_id,
        template_version=str((template or {}).get("version", "")),
        fixed_facts=[],
        character_roster=character_roster or [],
    )


# ═══════════════════════════════════════════════════════════════════
# Internal: chapter plan generation
# ═══════════════════════════════════════════════════════════════════
def _generate_chapter_plans(
    brief: Brief,
    template_id: str,
    template_name: str,
    chapter_count: int,
    character_roster: list[str],
    runtime: Any = None,
    config: RunnableConfig | None = None,
) -> list[ChapterPlan]:
    """生成 ChapterPlan 列表。使用 runtime.llm 进行 LLM 调用。"""

    provider = getattr(runtime, "llm", None) if runtime else None

    # Try LLM-based generation first
    if provider is not None:
        try:
            return _generate_via_llm(
                provider, brief, template_id, template_name,
                chapter_count, character_roster, config,
            )
        except Exception:
            pass  # Fall through to deterministic

    # Fallback: deterministic chapter plans
    return _generate_deterministic(
        brief, template_id, template_name, chapter_count, character_roster,
    )


def _generate_via_llm(
    provider, brief, template_id, template_name,
    chapter_count, character_roster, config,
) -> list[ChapterPlan]:
    """通过 LLM 生成 ChapterPlan。"""
    import json

    system_prompt = (
        f"You are planning a {chapter_count}-chapter story using the '{template_name}' template. "
        f"Output a JSON array of chapter plans."
    )
    user_prompt = (
        f"Premise: {brief.raw_idea}\n"
        f"Template: {template_name} ({template_id})\n"
        f"Chapters: {chapter_count}\n"
        f"Characters: {', '.join(character_roster) if character_roster else 'auto-generate'}\n\n"
        f"Return JSON array with objects: chapter_index, title, goal, conflict, turning_point, ending_state, "
        f"character_focus, required_facts, forbidden_changes, unresolved_threads_in, unresolved_threads_out."
    )

    raw = provider.invoke(
        messages=[{"role": "system", "content": system_prompt},
                   {"role": "user", "content": user_prompt}],
        config=config,
    )
    content = raw.content if hasattr(raw, "content") else str(raw)
    data = json.loads(content) if isinstance(content, str) else content

    plans = []
    for item in data:
        plans.append(ChapterPlan(
            chapter_index=item.get("chapter_index", len(plans) + 1),
            title=item.get("title", f"Chapter {len(plans) + 1}"),
            goal=item.get("goal", ""),
            conflict=item.get("conflict", ""),
            turning_point=item.get("turning_point", ""),
            ending_state=item.get("ending_state", ""),
            character_focus=item.get("character_focus", []),
            required_facts=item.get("required_facts", []),
            forbidden_changes=item.get("forbidden_changes", []),
            unresolved_threads_in=item.get("unresolved_threads_in", []),
            unresolved_threads_out=item.get("unresolved_threads_out", []),
        ))
    return plans


def _generate_deterministic(
    brief: Brief, template_id: str, template_name: str,
    chapter_count: int, character_roster: list[str],
) -> list[ChapterPlan]:
    """Deterministic fallback: 基于 template beats 生成 ChapterPlan。"""
    # Map template beats to chapter distribution
    beats = _template_beats(template_id, chapter_count)

    chapters: list[ChapterPlan] = []
    for i in range(chapter_count):
        beat = beats[i] if i < len(beats) else {}
        ch = ChapterPlan(
            chapter_index=i + 1,
            title=f"{brief.raw_idea[:40]}（第{i+1}章）",
            goal=beat.get("goal", f"Chapter {i+1} goal"),
            conflict=beat.get("conflict", f"Chapter {i+1} conflict"),
            turning_point=beat.get("turning_point", ""),
            ending_state=beat.get("ending_state", ""),
            character_focus=character_roster[:3] if character_roster else [],
        )
        # Thread continuity
        if i > 0:
            ch.unresolved_threads_in = [f"thread_ch{i}"]
        ch.unresolved_threads_out = [f"thread_ch{i+1}"]
        chapters.append(ch)
    return chapters


def _template_beats(template_id: str, chapter_count: int) -> list[dict]:
    """Map template beats to chapter count."""
    if template_id == "GENERAL_THREE_ACT":
        beats_6 = [
            {"goal": "建立场景与核心人物", "conflict": "日常状态与隐含问题", "turning_point": "", "ending_state": "问题浮现"},
            {"goal": "引入打破日常的事件", "conflict": "冲突起点", "turning_point": "事件发生", "ending_state": "冲突明确"},
            {"goal": "冲突升级", "conflict": "角色必须做出艰难选择", "turning_point": "选择改变局面", "ending_state": "局势紧张"},
            {"goal": "剧情转折", "conflict": "新信息使局势不可逆转", "turning_point": "关键信息揭露", "ending_state": "不可逆转"},
            {"goal": "情感与行动高潮", "conflict": "角色面对最大障碍", "turning_point": "最终行动", "ending_state": "高潮结束"},
            {"goal": "结局与收束", "conflict": "展示改变后的状态", "turning_point": "", "ending_state": "故事收束"},
        ]
        # Distribute 6 beats across N chapters
        if chapter_count >= 6:
            return beats_6[:chapter_count]
        # Collapse beats into fewer chapters
        result = []
        per_chapter = max(1, 6 // chapter_count)
        for i in range(chapter_count):
            start = i * per_chapter
            end = min(start + per_chapter, 6)
            merged = beats_6[start:end]
            result.append({
                "goal": merged[0]["goal"],
                "conflict": merged[-1]["conflict"],
                "turning_point": merged[-1]["turning_point"],
                "ending_state": merged[-1]["ending_state"],
            })
        return result

    # Generic: simple distribution
    return [{"goal": f"Chapter {i+1}", "conflict": "", "turning_point": "", "ending_state": ""}
            for i in range(chapter_count)]


# ═══════════════════════════════════════════════════════════════════
# Derivation helpers
# ═══════════════════════════════════════════════════════════════════
def _derive_global_goal(brief: Brief) -> str:
    return f"完成故事: {brief.raw_idea[:100]}"


def _derive_global_conflict(brief: Brief) -> str:
    return f"核心冲突: {brief.raw_idea[:100]}"


def _derive_ending_target(brief: Brief, template: dict | None = None) -> str:
    guidance = (template or {}).get("ending_guidance", "")
    if guidance:
        return f"结局方向({guidance}): {brief.raw_idea[:80]}"
    return f"故事收束: {brief.raw_idea[:80]}"