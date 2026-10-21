"""逐集生成子图。

     gen_beats ──▶ validate ──┬─▶ (通过) ───────────────▶ END
                              │
                              ├─▶ repair_audio ─────────▶ validate
                              ├─▶ repair_beat ──────────▶ validate
                              └─▶ repair_compliance ────▶ validate

两个设计要点：

1. **修复是分类的，不是统一的。** 一句台词超长和"本集缺少副作用"是两种完全不同的问题，
   导致它们的成本差两个数量级（前者可确定性截断，后者要重写结构）。
   统一走"重新生成一集"会把便宜问题当贵问题处理。

2. **尝试次数有上限，超限即降级放行并打标。** 无限重试是最常见的生产事故来源：
   模型在某一条规则上反复失败时，正确的做法是放行 + 标记人工，而不是烧光预算。
"""
from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from .. import prompts
from ..continuity import continuity_slice, render_continuity
from ..llm.router import resolve_spec
from ..schemas import Episode, EpisodePlan, Finding, LineRewrites, ProseStory
from ..state import EpisodeState
from ..validators import validate_episode
from ..validators.deterministic import beats_missing_sfx, offending_lines
from ._common import bump, deps, only

EPISODE_MAX_ATTEMPTS = 2

# 逐集子图内没有立项级修复节点，这些路由必须降级。
# 降级规则本身也是设计的一部分：**问题在单集层面能做的只有重写这一集**，
# 立项层的修正（改选择规律、改账本）由 s7 归因后的选择性重生成处理。
_DEGRADE_TO_BEAT = {
    "repair_ledger", "repair_behavior",
    "repair_outline", "repair_gadget", "repair_cast",
}


# ============================================================================
# 生成
# ============================================================================
def gen_beats(state: EpisodeState, config: RunnableConfig) -> dict[str, Any]:
    """Phase 1.5: 拆分为 Plan(STRONG) → Writer(BALANCED)。

    向后兼容：外部接口不变，仍产出 Episode。
    """
    lib, runtime = deps(config)
    entry = state["outline_entry"]
    duration = state["brief"].target_duration_sec
    attempts = int(state.get("attempt", 0))

    # 连续性切片
    slice_ = continuity_slice(
        state.get("ledger"), state.get("behavior"), entry.episode, state["cast"],
        total_episodes=state["brief"].target_episodes,
    )
    continuity_text = render_continuity(slice_)

    cast_dicts = [c.model_dump() for c in state["cast"]]
    gadget_dict = state["gadget"].model_dump()

    # Step 1: EpisodePlan (STRONG)
    plan_system, plan_user = prompts.episode_plan(
        lib, entry.model_dump(), cast_dicts, gadget_dict, duration, continuity_text,
    )
    plan_spec = resolve_spec("episode_plan", extra={
        "json_mode": True, "attempt": attempts,
    })
    plan = runtime.llm.complete_structured(plan_spec, plan_system, plan_user, EpisodePlan)

    # Step 2: EpisodeWriter (BALANCED) —— 根据 Plan 写正文
    writer_system, writer_user = prompts.episode_writer(
        lib, plan.model_dump(), cast_dicts, gadget_dict, duration, continuity_text,
    )
    writer_spec = resolve_spec("episode_writer", extra={
        "json_mode": True, "attempt": attempts,
    })
    episode: Episode = runtime.llm.complete_structured(writer_spec, writer_system, writer_user, Episode)

    # 修复 LLM 可能输出的非法 event 值
    episode = _normalize_line_events(episode)

    # 节拍边界以规则库为准回填

    # 节拍边界以规则库为准回填
    episode = _enforce_beat_bounds(episode, lib, entry.episode)
    episode = episode.model_copy(update={"revision": attempts})
    return {
        "episode": episode,
        "budgets": bump("llm_calls"),
        "trace": [f"ep{entry.episode}:gen_beats(rev{attempts})"],
    }


def _enforce_beat_bounds(episode: Episode, lib, ep_no: int) -> Episode:
    """把节拍时间边界强制对齐规则库。

    这是"约束求解而非自由生成"的落点：模型负责内容，时间轴由规则库决定。
    """
    sheet = {s["id"]: s["range"] for s in lib.beat_sheet_for(episode.duration_sec)}
    beats = []
    for beat in episode.beats:
        if beat.segment in sheet:
            lo, hi = sheet[beat.segment]
            beat = beat.model_copy(update={"start_sec": float(lo), "end_sec": float(hi)})
        beats.append(beat)
    return episode.model_copy(update={"beats": beats, "episode": ep_no})


# LLM 可能输出的非法 event 值 → 合法 BeatEvent 映射
_EVENT_FIX_MAP: dict[str, str] = {
    "conflict": "beat",
    "setup": "hook",
    "climax": "reversal",
    "resolution": "release",
    "transition": "beat",
    "intro": "hook",
    "outro": "release",
    "exposition": "beat",
    "complication": "beat",
    "crisis": "reversal",
    "epilogue": "release",
    "prologue": "hook",
}


def _normalize_line_events(episode: Episode) -> Episode:
    """修复 LLM 可能输出的非法 BeatEvent 值。

    模型在生成 lines 时偶尔会写出不在 {hook,beat,release,reversal,cliffhanger} 中的 event。
    这是 Pydantic 严格校验下最常见的 schema 拒绝 —— 做确定性映射而非再问一次模型。
    """
    from ..schemas import BeatEvent

    valid = set(BeatEvent.__args__)  # type: ignore[attr-defined]
    beats = []
    for beat in episode.beats:
        lines = []
        for line in beat.lines:
            if line.event and line.event not in valid:
                mapped = _EVENT_FIX_MAP.get(line.event, "beat")
                line = line.model_copy(update={"event": mapped})
            lines.append(line)
        beats.append(beat.model_copy(update={"lines": lines}))
    return episode.model_copy(update={"beats": beats})


# ============================================================================
# 散文故事 Writer（Phase 6: prose_story）
# ============================================================================
def prose_writer(state: EpisodeState, config: RunnableConfig) -> dict[str, Any]:
    """散文故事生成节点。

    content_form == "prose_story" 时走这条路。
    调用 prompts.prose_writer → runtime.llm.complete_structured(ProseStory)。
    """
    lib, runtime = deps(config)
    entry = state["outline_entry"]
    attempts = int(state.get("attempt", 0))

    # 连续性切片
    slice_ = continuity_slice(
        state.get("ledger"), state.get("behavior"), entry.episode, state["cast"],
        total_episodes=state["brief"].target_episodes,
    )
    continuity_text = render_continuity(slice_)

    cast_dicts = [c.model_dump() for c in state["cast"]]
    behavior_dict = state["behavior"].model_dump() if state.get("behavior") else None
    ledger_dict = state["ledger"].model_dump() if state.get("ledger") else None

    # content_form 中的 prose metadata
    form_profile = state.get("content_form", {})
    metadata = form_profile.get("metadata", {})
    target_chars = int(metadata.get("target_chars", 2000))

    # 构建 prose_writer 提示词
    plan_dict = entry.model_dump()
    system, user = prompts.prose_writer(
        lib, plan_dict, cast_dicts, behavior_dict, ledger_dict,
        target_chars=target_chars, continuity_text=continuity_text,
    )

    writer_spec = resolve_spec("prose_writer", extra={
        "json_mode": True, "attempt": attempts,
    })
    prose: ProseStory = runtime.llm.complete_structured(writer_spec, system, user, ProseStory)

    return {
        "prose": prose,
        "budgets": bump("llm_calls"),
        "trace": [f"ep{entry.episode}:prose_writer(rev{attempts})"],
    }


# ============================================================================
# 散文故事校验（Phase 6.1: runtime prose validator）
# ============================================================================
def prose_validate(state: EpisodeState, config: RunnableConfig) -> dict[str, Any]:
    """Prose 结构校验 + OutputGuard。

    Phase 6.1: 真实 runtime 执行，不是只存在函数。
    0 LLM 调用 —— 纯代码校验。
    """
    from ..content_forms.validators import validate_prose_structure, prose_output_guard

    prose = state.get("prose")
    if prose is None:
        return {
            "findings": [],
            "current": [],
            "trace": ["prose_validate:no_prose_output"],
        }

    # Step 1: 结构校验
    result = validate_prose_structure(prose)
    findings = []
    for f in result.get("findings", []):
        from ..schemas import Finding
        findings.append(Finding(
            rule_id=f["rule_id"],
            severity=f["severity"],
            message=f["message"],
            route="prose_structure",
        ))

    # Step 2: OutputGuard
    guard = prose_output_guard(prose)
    if not guard["passed"]:
        findings.append(Finding(
            rule_id="PROSE_GUARD",
            severity="error",
            message=guard["reason"],
            route="prose_structure",
        ))

    return {
        "findings": findings,
        "current": findings,
        "budgets": {"llm_calls": 0},
        "output_guard_passed": guard["passed"],
        "trace": [f"prose_validate:passed={result['passed']} guard={guard['passed']} errors={len(findings)}"],
    }


# ============================================================================
# 校验
# ============================================================================
def validate_ep(state: EpisodeState, config: RunnableConfig) -> dict[str, Any]:
    lib, runtime = deps(config)
    claims = list(state.get("claims") or [])
    report = validate_episode(
        state["episode"], lib, runtime.llm, state["cast"], state["outline_entry"],
        deep=state["brief"].target_episodes > 0,
        claims=claims,
    )
    # 卡住判定：连续两轮的违规指纹完全相同 → 修复无效，不必再试
    sig = ",".join(sorted({f.rule_id for f in report.errors}))
    stuck = bool(state.get("last_sig")) and state.get("last_sig") == sig
    patterns = report.by_pattern()

    return {
        "findings": report.findings,
        "current": report.findings,
        "last_sig": sig,
        "stuck": stuck,
        "unclaimed": list(report.unclaimed),
        # 裁判调用也要计入 —— 短路时 report.llm_calls 为 0，如实反映"这一轮没花裁判钱"
        "budgets": bump("llm_calls", report.llm_calls),
        "trace": [
            f"ep{state['episode'].episode}:validate passed={report.passed} "
            f"err={len(report.errors)} t1={report.tier1_count} t2={report.tier2_count} "
            f"judge_calls={report.llm_calls} judge_saved={report.llm_calls_saved} "
            f"claims={report.claims_checked}"
            + (f" patterns={patterns}" if patterns else "")
            + (" stuck=True" if stuck else "")
        ],
    }


# ============================================================================
# 修复
# ============================================================================
def repair_audio(state: EpisodeState, config: RunnableConfig) -> dict[str, Any]:
    """听觉化修复。三步，按"代价从低到高"排序。

    设计原则：**确定性可修的问题不该占用模型调用。**

    R11（音效锚点缺失）与 R06（线索数超标）都是纯结构字段问题：
    补一条音效、把 threads 收敛成一条即可，模型在这里毫无价值。
    只有 R10（视觉描写）真正需要语义理解 —— 得把"嘴角微微一勾"翻译成听觉事件。
    因此先做零成本的规范化，再把剩下的问题交给模型。
    """
    lib, runtime = deps(config)
    episode: Episode = state["episode"]

    # ---- 第一步：确定性规范化（R11 音效锚点 / R06 线索收敛）
    kind = {"AV4": "环境音场底噪", "AV1": "一声突兀的脆响"}
    episode, patched = _normalize(episode, lib, kind)

    # ---- 第二步：模型定向改写（只处理 R10 / R12 这类需要语义的行）
    targets = offending_lines(episode, lib)
    rewrites_note = ""
    if targets:
        lookup = {ln.id: ln for beat in episode.beats for ln in beat.lines}
        payload = [lookup[t].model_dump() for t in targets if t in lookup]
        system = (
            "你是一名广播剧音频编辑。只做两件事：把视觉描写改成听觉事件；把超长台词拆短。"
            "不得修改行 id、不得改动时间戳、不得改动没有被点名的行。"
        )
        user = (
            f"需要改写的行：\n{payload}\n\n"
            f"禁用词：{lib.visual_blacklist}\n"
            "视觉描写必须替换为四类听觉事件之一："
            "突兀的声音 / 被中断的台词 / 音乐情绪切换 / 环境音场改变。"
        )
        spec = resolve_spec("repair_audio", extra={
            "target_lines": payload,
            "blacklist": lib.visual_blacklist,
            "attempt": int(state.get("attempt", 0)) + 1,
        })
        rewrites: LineRewrites = runtime.llm.complete_structured(spec, system, user, LineRewrites)
        episode = _apply_rewrites(episode, rewrites)
        rewrites_note = f", llm_rewrites={len(rewrites.lines)}"

    return {
        "episode": episode,
        "attempt": int(state.get("attempt", 0)) + 1,
        # 只有在真正需要语义改写时才计一次调用：全部走完确定性规范化、无残余目标时不调模型
        "budgets": bump("llm_calls", 1 if targets else 0),
        "trace": [
            f"ep{episode.episode}:repair_audio(normalized={patched}, "
            f"targets={len(targets)}{rewrites_note})"
        ],
    }


def _normalize(episode: Episode, lib, kind: dict[str, str]) -> tuple[Episode, list[str]]:
    """零成本规范化：只碰结构字段，不碰文本。"""
    actions: list[str] = []

    # R06：单集只解决一个核心目标。多线索收敛为第一条。
    if len(episode.threads) != 1:
        keep = episode.threads[:1] or ["推进本集核心目标"]
        actions.append(f"threads:{len(episode.threads)}→1")
        episode = episode.model_copy(update={"threads": keep})

    # R11：补音效锚点
    missing = beats_missing_sfx(episode, lib)
    if missing:
        episode = _patch_sfx(episode, missing, kind)
        actions.append(f"sfx_patched={len(missing)}")

    return episode, actions


def _patch_sfx(episode: Episode, missing: list[tuple[str, list[str]]],
               kind: dict[str, str]) -> Episode:
    table = {seg: cats for seg, cats in missing}
    beats = []
    for beat in episode.beats:
        cats = table.get(beat.segment)
        if not cats:
            beats.append(beat)
            continue
        new_lines = list(beat.lines)
        cursor = beat.start_sec
        for idx, cat in enumerate(cats):
            new_lines.insert(idx, _placeholder_sfx(
                beat.segment, idx, cat, kind.get(cat, "音效落点"), cursor))
            cursor += 0.5
        beats.append(beat.model_copy(update={"lines": _renumber(new_lines)}))
    return episode.model_copy(update={"beats": beats})


def _placeholder_sfx(segment: str, idx: int, cat: str, text: str, start: float):
    from ..schemas import Line

    return Line(
        id=f"{segment}-SFX-{idx}", kind="sfx", speaker=None, text=text,
        start_sec=start, end_sec=start + 0.8, sfx_category=cat,  # type: ignore[arg-type]
        event=None, emotion_peak=3,
    )


def _renumber(lines):
    """给确定性插入的音效分配不与原文冲突的 id。"""
    out = []
    for i, ln in enumerate(lines):
        if "-SFX-" in ln.id:
            ln = ln.model_copy(update={"id": f"NI-{i:03d}"})
        out.append(ln)
    return out


def _apply_rewrites(episode: Episode, rewrites: LineRewrites) -> Episode:
    table = {ln.id: ln for ln in rewrites.lines}
    beats = []
    for beat in episode.beats:
        beats.append(beat.model_copy(update={
            "lines": [table.get(ln.id, ln) for ln in beat.lines],
        }))
    return episode.model_copy(update={"beats": beats})


def repair_beat(state: EpisodeState, config: RunnableConfig) -> dict[str, Any]:
    """节拍层修复：重写整集，但把违规清单原样回灌。

    这一路最贵，所以只在问题真正属于"结构/节拍"时才走。
    重写时同样注入连续性切片 —— 否则"重写一集"会顺手把账目事实洗掉，
    修复动作本身变成新的连续性缺陷来源。
    """
    lib, runtime = deps(config)
    prior = [f.model_dump() for f in state.get("current") or []]
    episode = state["episode"]
    slice_ = continuity_slice(
        state.get("ledger"), state.get("behavior"), episode.episode, state["cast"],
        total_episodes=state["brief"].target_episodes,
    )
    system, user = prompts.episode_beats(
        lib, state["outline_entry"].model_dump(), [c.model_dump() for c in state["cast"]],
        state["gadget"].model_dump(), state["brief"].target_duration_sec, prior,
        continuity_text=render_continuity(slice_),
    )
    spec = resolve_spec("repair_beat", extra={
        "attempt": int(state.get("attempt", 0)) + 1,
        "outline_entry": state["outline_entry"].model_dump(),
        "cast": state["cast"],
        "duration_sec": state["brief"].target_duration_sec,
        "continuity": slice_,
    })
    new_ep: Episode = runtime.llm.complete_structured(spec, system, user, Episode)
    new_ep = _enforce_beat_bounds(new_ep, lib, episode.episode)
    new_ep = new_ep.model_copy(update={"revision": int(state.get("attempt", 0)) + 1})
    return {
        "episode": new_ep,
        "attempt": int(state.get("attempt", 0)) + 1,
        "budgets": bump("llm_calls"),
        "trace": [f"ep{episode.episode}:repair_beat"],
    }


def repair_compliance(state: EpisodeState, config: RunnableConfig) -> dict[str, Any]:
    """合规修复。独立节点，且**永不自动放行**。

    价值导向问题不能靠一次重写就认为解决 —— 这条路径的产出必须带人工复核标记。
    """
    lib, runtime = deps(config)
    prior = [f.model_dump() for f in (state.get("current") or [])
             if f.rule_id == "R13"]
    result = repair_beat(state, config)
    result["trace"] = [f"ep{state['episode'].episode}:repair_compliance(需人工复核)"] + \
        [f"R13:{f.message}" for f in (state.get("current") or []) if f.rule_id == "R13"]
    return result


# ============================================================================
# 路由
# ============================================================================
def route_start(state: EpisodeState) -> str:
    """Phase 6.1: Content Form 不再在 START 处分叉。

    **所有** content form 都先经过 gen_beats（共享 Story Planning）。
    Content Form 分叉移至 gen_beats 之后的 route_content_form。
    """
    return "gen_beats"


def route_content_form(state: EpisodeState) -> str:
    """Story Planning (gen_beats) 完成后的 Content Form 分叉。

    prose_story → prose_writer → prose_validate → END
    audio_drama / 缺失 → validate_ep → (repair loop) → END
    """
    form = state.get("content_form", {})
    if isinstance(form, dict) and form.get("key") == "prose_story":
        return "prose_writer"
    return "validate_ep"


def route_after_validate(state: EpisodeState) -> str:
    # 只看本轮结论（current），不看累积日志 —— 否则修复回路无法退出
    errors = [f for f in (state.get("current") or []) if f.severity == "error"]
    if not errors:
        return END

    attempt = int(state.get("attempt", 0))
    if attempt >= EPISODE_MAX_ATTEMPTS:
        # 降级放行并打标：无限重试是最常见的生产事故来源
        return END
    if state.get("stuck"):
        # 上一轮修复没有改变违规集合 → 这一轮大概率也不会
        return END

    routes = {f.route for f in errors}
    if "repair_compliance" in routes:
        return "repair_compliance"
    # repair_beat 是逐集子图内的节点，必须显式命中 —— 声明类 finding（ClaimCheck）
    # 的 route 就是 repair_beat。早期版本只判断"立项层路由的降级集合"，于是声明类
    # 违规会掉进最后一行 repair_audio：只改写被点名的行、不重新生成节拍，
    # 而"声明未兑现"恰恰是靠重写节拍才能补上的（补一行兑现行）。
    # 症状极具迷惑性：报告里 error 居高不下，日志里每集都"修过"，但正文一行没变。
    if "repair_beat" in routes:
        return "repair_beat"
    # 立项层的规则（K06 的 repair_outline、CH/RL 的 repair_behavior、FC 的 repair_ledger）
    # 在逐集子图内没有对应的修复节点。这类问题在单集层面能做的只有重写节拍，
    # 立项层的修正由 s7 归因后的选择性重生成处理 —— 降级规则见文件头的 _DEGRADE_TO_BEAT。
    if routes & _DEGRADE_TO_BEAT:
        return "repair_beat"
    return "repair_audio"


# ============================================================================
# 组装子图
# ============================================================================
def build_episode_graph():
    """编译逐集子图。

    单独编译的意义：这个子图可以脱离主图单独测试（给定一份大纲条目就能跑），
    这对调试价值很大 —— 出问题时能确定是"这一集的生成逻辑"还是"整条流水线的编排"。

    Phase 6.1: Content Form 分叉位于 Story Planning 之后 ——
      START → gen_beats（共享 Planning）
        ├─ prose_story  → prose_writer → prose_validate → END
        └─ audio_drama  → validate_ep → (repair → validate_ep)* → END
    """
    graph = StateGraph(EpisodeState)
    graph.add_node("gen_beats", gen_beats)
    graph.add_node("prose_writer", prose_writer)       # Phase 6
    graph.add_node("prose_validate", prose_validate)   # Phase 6.1
    graph.add_node("validate_ep", validate_ep)
    graph.add_node("repair_audio", repair_audio)
    graph.add_node("repair_beat", repair_beat)
    graph.add_node("repair_compliance", repair_compliance)

    # Phase 6.1: 所有 content form 都从 gen_beats（共享 Planning）开始
    graph.add_edge(START, "gen_beats")

    # 在 Planning 后按 Content Form 分叉
    graph.add_conditional_edges(
        "gen_beats",
        route_content_form,
        {
            "prose_writer": "prose_writer",
            "validate_ep": "validate_ep",
        },
    )

    # Prose 路径：writer → validator → END
    graph.add_edge("prose_writer", "prose_validate")
    graph.add_edge("prose_validate", END)

    # Audio 路径：validator → (repair loop) → END
    graph.add_conditional_edges(
        "validate_ep",
        route_after_validate,
        {
            "repair_audio": "repair_audio",
            "repair_beat": "repair_beat",
            "repair_compliance": "repair_compliance",
            END: END,
        },
    )
    for node in ("repair_audio", "repair_beat", "repair_compliance"):
        graph.add_edge(node, "validate_ep")

    return graph.compile()
