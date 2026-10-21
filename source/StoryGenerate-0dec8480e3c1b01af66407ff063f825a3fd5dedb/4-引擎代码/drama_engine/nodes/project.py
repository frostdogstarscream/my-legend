"""立项阶段节点（第 1–4 步流水线）+ 三道校验门。

这里体现本设计最重要的工程判断：**把校验放在"能修它的那一步"之后，而不是全流程末尾。**

传统做法是"生成 80 集 → 全量校验 → 发现问题 → 重来"。
本项目是"金手指定完就校验 K01/K16/K17/K18 → 角色定完就校验 K12/K15 → 大纲定完就校验 K04/K06/K09"。

代价对比（20 集项目、平均 3 轮重试）：
  - 末尾校验：一次金手指层级错了，要重生成 20 集剧本才发现 → 20 集 × 3 轮的 token 全部浪费
  - 前移校验：在第 2 个节点就拦住 → 只重出 1 次金手指设定

这就是"校验节点化"的经济学理由。位置选错，再好的规则库也是白写。
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from .. import prompts
from ..llm.router import resolve_spec
from ..schemas import (
    BehaviorBible,
    Brief,
    CastDraft,
    FactLedger,
    Finding,
    GadgetSpec,
    OutlineDraft,
    TopicChoice,
)
from ..validators import (
    validate_behavior,
    validate_cast,
    validate_gadget,
    validate_ledger,
    validate_outline,
)
from ._common import (
    MAX_ATTEMPTS,
    attempt_of,
    bump,
    deps,
    has_errors,
    next_attempt,
    only,
    should_repair,
    stuck_guard,
)


# ============================================================================
# S0 归一化输入（无 LLM）
# ============================================================================
def s0_intake(state: dict) -> dict:
    brief = state.get("brief")
    if isinstance(brief, dict):
        brief = Brief.model_validate(brief)
    if brief is None:
        raise ValueError("缺少 brief")
    if brief.target_duration_sec not in (90, 120, 150, 180, 300):
        brief = brief.model_copy(update={"target_duration_sec": 180})
    return {
        "brief": brief,
        "trace": [f"intake:idea={brief.raw_idea[:24]}... episodes={brief.target_episodes}"],
    }


# ============================================================================
# S1 选题
# ============================================================================
def s1_topic(state: dict, config: RunnableConfig) -> dict:
    """选题。

    关于白名单兜底的一处修正 —— 这里踩过一个坑，值得留档：

    初版实现是"模型选了音频适配分低于 70 的赛道时，**静默替换**为分最高的那个"。
    结果：命题『携带青霉素辅佐丞相北伐』被判为 G01 现实家庭冲突 —— 一个与命题毫不相干的赛道。
    静默改写用户的命题，比保留一个低适配分的选择要糟得多。

    现在的行为：**尊重模型基于命题的判断，但把适配风险显式记录下来。**
    低适配不是错误，是一个需要被看见的取舍。
    """
    lib, runtime = deps(config)
    brief: Brief = state["brief"]
    tele = getattr(runtime, "telemetry", None)

    # Phase 4.5B: 如果 brief 已显式指定 genre_key，跳过 LLM
    explicit_genre = getattr(brief, "explicit_genre", None) or ""
    if explicit_genre and explicit_genre in lib.genres_dict():
        genre_data = lib.genres_dict()[explicit_genre]
        choice = TopicChoice(
            genre_id=explicit_genre,
            recipe_id=genre_data.get("default_recipe", "R1"),
            rationale=f"用户显式指定赛道: {explicit_genre}",
        )
        if tele:
            tele.event("node_skipped", {"node": "topic_select", "reason": "explicit_genre"})
    else:
        system, user = prompts.topic_select(lib, brief.raw_idea)
        spec = resolve_spec("topic_select")
        choice: TopicChoice = runtime.llm.complete_structured(spec, system, user, TopicChoice)

    genre = lib.genre(choice.genre_id)
    findings: list[Finding] = []
    if genre is None:
        # 模型给出了不存在的赛道：这时才做兜底替换，并记录
        fallback = max(lib.genres, key=lambda g: g.get("audio_fit_score", 0))
        findings.append(_advisory(
            "S01", f"模型给出的赛道 {choice.genre_id} 不在规则库中，已回落至 {fallback['genre_id']}"
        ))
        genre = fallback

    score = genre.get("audio_fit_score", 0)
    trace = [f"s1_topic:{genre['genre_id']}/{choice.recipe_id} audio_fit={score}"]
    if score < brief.audio_fit_floor:
        findings.append(_advisory(
            "S02",
            f"赛道「{genre['name']}」的音频适配分仅 {score}/100，低于建议阈值 {brief.audio_fit_floor}。"
            f"该赛道依赖{'视觉奇观' if score < 50 else '音色反差或情境喜感'}，"
            f"音频化时需额外补偿（音效密度、旁白负担、角色合并）。",
        ))
        trace.append(f"s1_topic:low_audio_fit score={score} floor={brief.audio_fit_floor}")

    return {
        "genre_id": genre["genre_id"],
        "recipe_id": choice.recipe_id,
        "findings": findings,
        "outstanding_topic": findings,
        "budgets": bump("llm_calls"),
        "trace": trace,
    }


def _advisory(rule_id: str, message: str) -> Finding:
    """咨询级提示：不阻塞流程，但必须出现在报告里。"""
    return Finding(rule_id=rule_id, severity="warning", tier=1,
                   location="topic", message=message, repair_hint="", route="none")


# ============================================================================
# S2 金手指  →  门1
# ============================================================================
def s2_gadget(state: dict, config: RunnableConfig) -> dict:
    lib, runtime = deps(config)
    brief: Brief = state["brief"]
    tele = getattr(runtime, "telemetry", None)

    # Phase 5: Capability routing — mode 不支持 GADGET 时跳过
    mode_ctx = state.get("mode_context")
    if mode_ctx and not mode_ctx.supports("gadget"):
        if tele:
            tele.event("node_skipped", {
                "node": "gadget_design",
                "reason": "mode_capability_disabled",
            })
        # Phase 6.1: 返回空 GadgetSpec 而非 None，避免下游 .model_dump() 崩溃
        empty = GadgetSpec(assets=[], core_conflict="")
        return {
            "gadget": empty,
            "budgets": {"llm_calls": 0},
            "trace": ["s2_gadget:skipped (mode does not support gadget)"],
        }

    # Phase 4.5C: 如果 locked_assets 已提供 ≥2 个 gadget asset，跳过 LLM
    if brief.locked_assets and len(brief.locked_assets) >= 2:
        gadget = _gadget_from_assets(brief.locked_assets, state.get("genre_id", ""))
        if tele:
            tele.event("node_skipped", {"node": "gadget_design", "reason": "locked_assets_provided"})
        return {
            "gadget": gadget,
            "budgets": {"llm_calls": 0},
            "trace": [f"s2_gadget:assets={len(brief.locked_assets)}_locked"],
        }

    system, user = prompts.gadget_design(
        lib, brief.raw_idea, state["genre_id"], brief.locked_assets
    )
    spec = resolve_spec("gadget_design", extra={
        "attempt": attempt_of(state, "gadget_attempt"),
        "locked_assets": brief.locked_assets,
    })
    gadget: GadgetSpec = runtime.llm.complete_structured(spec, system, user, GadgetSpec)
    return {
        "gadget": gadget,
        "budgets": bump("llm_calls"),
        "trace": [f"s2_gadget:assets={[(a.name, a.layer, a.curve) for a in gadget.assets]}"],
    }


def gate_gadget(state: dict, config: RunnableConfig) -> dict:
    lib, runtime = deps(config)

    # Phase 5: mode 不支持 Gadget → 直通，不校验
    if not state.get("gadget") or not getattr(state["gadget"], "assets", []):
        return {
            "project_report": None,
            "findings": [],
            "outstanding_gadget": [],
            "budgets": {"llm_calls": 0},
            "trace": ["gate_gadget:skipped (gadget not applicable)"],
        }

    report = validate_gadget(state["gadget"], lib, runtime.llm)
    return {
        "project_report": report,
        "findings": report.findings,
        # 覆盖式写入：修复回路里这道门会跑多轮，只有最后一轮代表冻结时的状态
        "outstanding_gadget": report.findings,
        "budgets": bump("llm_calls", report.llm_calls),
        "attempts": stuck_guard(state, "gadget_attempt", report),
        "trace": [f"gate_gadget:passed={report.passed} errors={len(report.errors)} "
                  f"tier1={report.tier1_count} tier2={report.tier2_count}"],
    }


def repair_gadget(state: dict, config: RunnableConfig) -> dict:
    """立项层修复：回到金手指设定，而不是改写台词。"""
    lib, runtime = deps(config)
    brief: Brief = state["brief"]
    prior = only(state["project_report"].findings, "repair_gadget")
    system, user = prompts.gadget_design(
        lib, brief.raw_idea, state["genre_id"], brief.locked_assets, prior
    )
    nxt, attempts = next_attempt(state, "gadget_attempt")
    spec = resolve_spec("repair_gadget", extra={
        "attempt": nxt, "locked_assets": brief.locked_assets,
    })
    gadget: GadgetSpec = runtime.llm.complete_structured(spec, system, user, GadgetSpec)
    return {
        "gadget": gadget,
        "budgets": bump("llm_calls"),
        "attempts": attempts,
        "trace": [f"repair_gadget:attempt={nxt}"],
    }


# ============================================================================
# S3 角色  →  门2
# ============================================================================
def s3_cast(state: dict, config: RunnableConfig) -> dict:
    lib, runtime = deps(config)
    brief: Brief = state["brief"]
    resolved = state.get("resolved_characters") or []
    char_inputs = state.get("character_inputs") or []
    missing_count = max(0, lib.max_characters(5) - len(resolved))
    tele = getattr(runtime, "telemetry", None)

    # Phase 4.5B: 5 chars all pinned → 跳过 LLM，纯代码生成 Cast
    if missing_count == 0 and len(resolved) == lib.max_characters(5):
        return _cast_from_pinned(state, resolved, tele)
    # ...

    # Phase 3: 如果有用户角色，注入 pinned context
    pinned_text = ""
    if resolved:
        from ..characters.resolver import CharacterResolver
        resolver = CharacterResolver(max_characters=lib.max_characters(5))
        pinned_text = resolver.pin_prompt_context(resolved)

    system, user = prompts.cast_design(
        lib, brief.raw_idea, state["recipe_id"],
        state["gadget"].model_dump() if state.get("gadget") else {},
        pinned_characters=pinned_text,
        missing_count=missing_count,
    )
    spec = resolve_spec("cast_design", extra={"attempt": attempt_of(state, "cast_attempt")})
    draft: CastDraft = runtime.llm.complete_structured(spec, system, user, CastDraft)

    # Phase 3: Canon Overlay —— 确保 LLM 不改用户 Canon
    cards_dicts = [c.model_dump() for c in draft.cards]
    if resolved:
        from ..characters.overlay import reconcile_cast
        cards_dicts, conflicts = reconcile_cast(cards_dicts, resolved)
        # Re-hydrate
        from ..schemas import CharacterCard
        draft = CastDraft(cards=[CharacterCard(**c) for c in cards_dicts])
    else:
        conflicts = []

    return {
        "cast": draft.cards,
        "character_inputs": char_inputs,
        "resolved_characters": resolved,
        "budgets": bump("llm_calls"),
        "canon_conflicts": conflicts,
        "trace": [f"s3_cast:{[c.name for c in draft.cards]}"
                  f" pinned={len(resolved)} missing={missing_count}"],
    }


def gate_cast(state: dict, config: RunnableConfig) -> dict:
    lib, runtime = deps(config)
    report = validate_cast(state["cast"], lib, runtime.llm)
    return {
        "project_report": report,
        "findings": report.findings,
        "outstanding_cast": report.findings,
        "attempts": stuck_guard(state, "cast_attempt", report),
        "trace": [f"gate_cast:passed={report.passed} errors={len(report.errors)}"],
    }


def _cast_from_pinned(state: dict, resolved: list, tele=None) -> dict:
    """Phase 4.5B: 全部角色已 pinned → 纯代码构造 Cast，跳过 LLM。"""
    if tele:
        tele.event("node_skipped", {"node": "cast_design", "reason": "all_pinned"})
    from ..schemas import CharacterCard
    cards = []
    for i, r in enumerate(resolved):
        c = r.canon
        cards.append(CharacterCard(
            name=c.name or f"角色{i+1}",
            slot=f"role_{i+1:02d}",
            is_voiced=True,
            gender=c.gender or "male",
            age_range=c.identity or "--",
            personality="由用户指定",
            motivation=r.runtime.goal or c.motivation or "",
            relationship="待定",
            voice_anchor=getattr(c, "voice_anchor", "") or "",
        ))
    return {
        "cast": CastDraft(cards=cards),
        "budgets": {"llm_calls": 0},
        "trace": ["s3_cast:all_pinned"],
    }


def repair_cast(state: dict, config: RunnableConfig) -> dict:
    lib, runtime = deps(config)
    brief: Brief = state["brief"]
    prior = only(state["project_report"].findings, "repair_cast")
    system, user = prompts.cast_design(
        lib, brief.raw_idea, state["recipe_id"], state["gadget"].model_dump(), prior
    )
    user += "\n\n修复要求：优先把角色合并到已有角色，禁止通过新增角色来解决问题。"
    nxt, attempts = next_attempt(state, "cast_attempt")
    spec = resolve_spec("repair_cast", extra={"attempt": nxt})
    draft: CastDraft = runtime.llm.complete_structured(spec, system, user, CastDraft)
    return {
        "cast": draft.cards,
        "budgets": bump("llm_calls"),
        "attempts": attempts,
        "trace": [f"repair_cast:attempt={nxt}"],
    }


# ============================================================================
# S3b 人物行为卡  →  门2b
# ============================================================================
# 为什么单独成节点、单独成门，而不是并进角色节点：
#
#   1. 两种产物的失效原因不同。角色卡坏了是"听众认不出人"（听觉问题），
#      行为卡坏了是"这个人没有选择的逻辑"（剧作问题）。混在一起校验，
#      报告里分不清是哪一类缺陷，修复方向也会打架。
#   2. 行为卡依赖角色卡已定稿（要为每个有声角色产卡），因此在 gate_cast 之后。
#   3. 行为卡必须先于大纲 —— 大纲要把"本集演练哪一条选择"安排到具体集数上。
#      行为卡若在大纲之后生成，就只能事后补，人物永远进不了剧情。


def s3b_behavior(state: dict, config: RunnableConfig) -> dict:
    lib, runtime = deps(config)
    brief: Brief = state["brief"]
    resolved = state.get("resolved_characters") or []

    # Phase 3: Build runtime overrides text from user inputs
    runtime_text = _runtime_overrides_text(resolved)

    system, user = prompts.behavior_design(
        lib, brief.raw_idea, state["gadget"].model_dump(),
        [c.model_dump() for c in state["cast"]],
        runtime_overrides=runtime_text,
    )
    spec = resolve_spec("behavior_design", extra={
        "attempt": attempt_of(state, "behavior_attempt"),
        "cast": state["cast"],
        "total_episodes": brief.target_episodes,
    })
    bible: BehaviorBible = runtime.llm.complete_structured(spec, system, user, BehaviorBible)

    # Phase 3: Runtime Overlay —— 确保用户 goal/fear 不被覆盖
    if resolved:
        bible = _apply_runtime_overlay(bible, resolved)

    return {
        "behavior": bible,
        "budgets": bump("llm_calls"),
        "trace": [f"s3b_behavior:cards={len(bible.cards)} relations={len(bible.relations)}"],
    }


def _runtime_overrides_text(resolved: list) -> str:
    """为 behavior_design prompt 生成 Runtime Override 约束文本。"""
    if not resolved:
        return ""
    user_chars = [r for r in resolved if r.source == "user" and r.runtime_override_fields]
    if not user_chars:
        return ""
    lines = ["【本次故事 Runtime 约束 —— 用户已明确指定，必须保留】"]
    for r in user_chars:
        lines.append(f"\n角色 {r.name}: {r.runtime.summary()}")
    return "\n".join(lines)


def _apply_runtime_overlay(bible: "BehaviorBible", resolved: list) -> "BehaviorBible":
    """覆盖用户 Runtime Override 到 LLM 输出的 BehaviorCard 上。"""
    from ..characters.overlay import overlay_runtime
    user_chars = {r.name: r for r in resolved if r.runtime_override_fields}
    if not user_chars:
        return bible

    for card in bible.cards:
        resolved_char = user_chars.get(card.name)
        if resolved_char:
            rt_dict = {
                "goal": card.desire,
                "fear": card.fear,
            }
            fixed_rt, _ = overlay_runtime(
                rt_dict, resolved_char.runtime,
                resolved_char.runtime_override_fields, resolved_char.role_id,
            )
            # Apply back to BehaviorCard
            if "goal" in resolved_char.runtime_override_fields:
                card.desire = fixed_rt.get("goal", card.desire)
            if "fear" in resolved_char.runtime_override_fields:
                card.fear = fixed_rt.get("fear", card.fear)
    return bible


def gate_behavior(state: dict, config: RunnableConfig) -> dict:
    lib, runtime = deps(config)
    report = validate_behavior(state["behavior"], state["cast"], lib, runtime.llm)
    return {
        "project_report": report,
        "findings": report.findings,
        "outstanding_behavior": report.findings,
        "attempts": stuck_guard(state, "behavior_attempt", report),
        "trace": [f"gate_behavior:passed={report.passed} errors={len(report.errors)}"],
    }


def repair_behavior(state: dict, config: RunnableConfig) -> dict:
    """人物层修复：改的是选择规律，不是台词。

    这里最常见的错误修法是"把性格标签写得更细"，但那解决不了问题：
    判据是"在某个压力下他会放弃什么"这条规律缺不缺，不是形容词够不够多。
    """
    lib, runtime = deps(config)
    brief: Brief = state["brief"]
    prior = only(state["project_report"].findings, "repair_behavior")
    system, user = prompts.behavior_design(
        lib, brief.raw_idea, state["gadget"].model_dump(),
        [c.model_dump() for c in state["cast"]], prior,
    )
    user += ("\n\n修复要求：只补/改选择规律本身。若缺的是『违背自身利益』的选择，"
             "就补一条代价明确的选择，不要靠改措辞通过检查。")
    nxt, attempts = next_attempt(state, "behavior_attempt")
    spec = resolve_spec("repair_behavior", extra={
        "attempt": nxt, "cast": state["cast"],
        "total_episodes": brief.target_episodes,
    })
    bible: BehaviorBible = runtime.llm.complete_structured(spec, system, user, BehaviorBible)
    return {
        "behavior": bible,
        "budgets": bump("llm_calls"),
        "attempts": attempts,
        "trace": [f"repair_behavior:attempt={nxt}"],
    }


# ============================================================================
# S4 分集大纲  →  门3
# ============================================================================
def s4_outline(state: dict, config: RunnableConfig) -> dict:
    lib, runtime = deps(config)
    brief: Brief = state["brief"]
    system, user = prompts.outline(
        lib, brief.raw_idea, state["genre_id"], state["gadget"].model_dump(),
        [c.model_dump() for c in state["cast"]], brief.target_episodes,
        bible=state["behavior"].model_dump(),
    )
    spec = resolve_spec("outline", extra={
        "attempt": attempt_of(state, "outline_attempt"),
        "target_episodes": brief.target_episodes,
        "gadget": state["gadget"],
        # 行为卡必须传给大纲：大纲的职责之一就是决定"哪一集演练谁的选择规律"。
        # 不给它行为卡，这个分派就只能靠模型凭空猜，CH01（每个有声角色都要有自己的选择）
        # 也从"可满足"退化成"碰运气"。
        "behavior": state["behavior"],
    })
    draft: OutlineDraft = runtime.llm.complete_structured(spec, system, user, OutlineDraft)
    entries = sorted(draft.entries, key=lambda e: e.episode)
    return {
        "outline": entries,
        "budgets": bump("llm_calls"),
        "trace": [f"s4_outline:episodes={len(entries)}"],
    }


def gate_outline(state: dict, config: RunnableConfig) -> dict:
    lib, runtime = deps(config)
    report = validate_outline(state["outline"], lib, runtime.llm)
    return {
        "project_report": report,
        "findings": report.findings,
        "outstanding_outline": report.findings,
        "budgets": bump("llm_calls", report.llm_calls),
        "attempts": stuck_guard(state, "outline_attempt", report),
        "trace": [f"gate_outline:passed={report.passed} errors={len(report.errors)}"],
    }


def repair_outline(state: dict, config: RunnableConfig) -> dict:
    lib, runtime = deps(config)
    brief: Brief = state["brief"]
    prior = only(state["project_report"].findings, "repair_outline")
    system, user = prompts.outline(
        lib, brief.raw_idea, state["genre_id"], state["gadget"].model_dump(),
        [c.model_dump() for c in state["cast"]], brief.target_episodes, prior,
        bible=state["behavior"].model_dump(),
    )
    nxt, attempts = next_attempt(state, "outline_attempt")
    spec = resolve_spec("repair_outline", extra={
        "attempt": nxt,
        "target_episodes": brief.target_episodes,
        "gadget": state["gadget"],
        "behavior": state["behavior"],
    })
    draft: OutlineDraft = runtime.llm.complete_structured(spec, system, user, OutlineDraft)
    entries = sorted(draft.entries, key=lambda e: e.episode)
    return {
        "outline": entries,
        "budgets": bump("llm_calls"),
        "attempts": attempts,
        "trace": [f"repair_outline:attempt={nxt}"],
    }


# ============================================================================
# S5 事实账本  →  门4
# ============================================================================
# 账本在大纲之后生成，因为它要引用具体集号（到期集、承接集）。
# 依赖方向因此是：角色 → 行为 → 大纲 → 账本，四者都必须在 gate_bible 前定稿。
#
# 一处需要说明的写入：账本节点会把账本的 expected_mentions **镜像回大纲**的
# carried_facts 字段。这不是让两个源互相覆盖，而是让账本成为唯一真源、
# 大纲持有它的只读镜像 —— 好处是逐集生成只需读大纲就能知道"这集背着哪些账"，
# 同时 FC04 的交叉校验（大纲声明的账目必须真实存在）每次运行都会被执行一次。


def s5_ledger(state: dict, config: RunnableConfig) -> dict:
    lib, runtime = deps(config)
    brief: Brief = state["brief"]
    system, user = prompts.fact_ledger(
        lib, brief.raw_idea, state["gadget"].model_dump(),
        [c.model_dump() for c in state["cast"]],
        [e.model_dump() for e in state["outline"]],
        state["behavior"].model_dump(),
    )
    spec = resolve_spec("fact_ledger", extra={
        "attempt": attempt_of(state, "ledger_attempt"),
        "total_episodes": brief.target_episodes,
        "outline": [e.model_dump() for e in state["outline"]],
        "cast": state["cast"],
    })
    ledger: FactLedger = runtime.llm.complete_structured(spec, system, user, FactLedger)
    return {
        "ledger": ledger,
        "outline": _mirror_carried_facts(state["outline"], ledger),
        "budgets": bump("llm_calls"),
        "trace": [f"s5_ledger:entries={len(ledger.entries)}"],
    }


def gate_ledger(state: dict, config: RunnableConfig) -> dict:
    lib, runtime = deps(config)
    report = validate_ledger(
        state["ledger"], state["outline"], state.get("gadget"),
        state["cast"], lib, runtime.llm,
    )
    return {
        "project_report": report,
        "findings": report.findings,
        "outstanding_ledger": report.findings,
        "attempts": stuck_guard(state, "ledger_attempt", report),
        "trace": [f"gate_ledger:passed={report.passed} errors={len(report.errors)}"],
    }


def repair_ledger(state: dict, config: RunnableConfig) -> dict:
    """账目层修复：改账本，不改台词。

    这条纪律必须守住。账目矛盾最常见的错误修法是"下一集台词里解释一下"，
    但那只是把矛盾往后推 —— 听众会在第 20 集听到第 2 集说过的话被推翻。
    """
    lib, runtime = deps(config)
    brief: Brief = state["brief"]
    prior = only(state["project_report"].findings, "repair_ledger")
    system, user = prompts.fact_ledger(
        lib, brief.raw_idea, state["gadget"].model_dump(),
        [c.model_dump() for c in state["cast"]],
        [e.model_dump() for e in state["outline"]],
        state["behavior"].model_dump(), prior,
    )
    user += ("\n\n修复要求：只改账目。不要靠修改 statement 的措辞绕过检查 —— "
             "缺 due_at 就补期限，超范围就改集号，认知冲突就划清知情与禁知。")
    nxt, attempts = next_attempt(state, "ledger_attempt")
    spec = resolve_spec("repair_ledger", extra={
        "attempt": nxt, "total_episodes": brief.target_episodes,
        "outline": [e.model_dump() for e in state["outline"]],
        "cast": state["cast"],
    })
    ledger: FactLedger = runtime.llm.complete_structured(spec, system, user, FactLedger)
    return {
        "ledger": ledger,
        "outline": _mirror_carried_facts(state["outline"], ledger),
        "budgets": bump("llm_calls"),
        "attempts": attempts,
        "trace": [f"repair_ledger:attempt={nxt}"],
    }


def _mirror_carried_facts(outline, ledger: FactLedger):
    """把账本的 expected_mentions 镜像到大纲的 carried_facts。

    账本是唯一真源，大纲持有只读镜像。刻意不让两个源各自被独立编辑 ——
    同一件事有两个可写副本，就一定会有不一致的那一天。
    """
    carried: dict[int, list[str]] = {}
    for entry in ledger.entries:
        for ep in entry.expected_mentions:
            carried.setdefault(ep, []).append(entry.id)
        if entry.kind == "countdown" and entry.due_at:
            carried.setdefault(entry.due_at, [])
            if entry.id not in carried[entry.due_at]:
                carried[entry.due_at].append(entry.id)
    return [e.model_copy(update={"carried_facts": carried.get(e.episode, [])})
            for e in outline]


# ============================================================================
# 路由判定
# ============================================================================
# 五个路由函数的形状一致：有 error 且未超轮次上限且**没有卡住**时才回炉。
# "卡住"的判据是连续两轮违规指纹完全相同 —— 这时再重试一次结果通常也一样，
# 与其烧光预算，不如立即放行并标记人工。


def route_after_gadget(state: dict) -> str:
    return "repair_gadget" if should_repair(state, "gadget_attempt") else "s3_cast"


def route_after_cast(state: dict) -> str:
    return "repair_cast" if should_repair(state, "cast_attempt") else "s3b_behavior"


def route_after_behavior(state: dict) -> str:
    return "repair_behavior" if should_repair(state, "behavior_attempt") else "s4_outline"


def route_after_outline(state: dict) -> str:
    if should_repair(state, "outline_attempt"):
        return "repair_outline"
    # Phase 4.5D: 如果并行特性开启 & 多集任务，走 parallel_ledger_plan
    import os
    if os.environ.get("STORY_PARALLEL_ENABLED", "true").lower() == "true":
        target_eps = getattr(state.get("brief", {}), "target_episodes", 1) if hasattr(state.get("brief"), "target_episodes") else 1
        if target_eps > 1:
            return "parallel_ledger_plan"
    return "s5_ledger"


def route_after_ledger(state: dict) -> str:
    return "repair_ledger" if should_repair(state, "ledger_attempt") else "gate_bible"


def gate_bible(state: dict, config: RunnableConfig) -> dict:
    """人机协同闸门（预留）。

    立项阶段的结论一旦冻结，下游全部剧集都会基于它生成 —— 这是最值得人工看一眼的位置。
    冻结的东西现在是四样：金手指、角色、行为卡、大纲、账本。
    默认不阻塞；接入 UI 时把这里的直通实现换成 interrupt() 即可，图结构不用动：

        decision = interrupt({"reason": "立项冻结前确认", "bible": {...}})
        if not decision.get("approved"):
            return Command(goto="s4_outline")
    """
    trace = [
        f"gate_bible:frozen genre={state['genre_id']} cast={len(state['cast'])} "
        f"cards={len(state['behavior'].cards)} relations={len(state['behavior'].relations)} "
        f"episodes={len(state['outline'])} ledger={len(state['ledger'].entries)}"
    ]
    return {"approval": {"auto": True}, "trace": trace}
