"""全剧层节点：fan-out 派发 → 并行逐集生成 → 归约 → 终产物装配。

并行化的价值不在"跑得快"，而在**逐集之间没有共享可变状态**。
如果 20 集是串行生成、每集都能看到前面所有集的文本，模型会不自觉地复述前面的桥段 ——
这正是 K18 想拦的"感官资产衰减"。
把每集隔离开、各自只拿到"大纲 + 角色 + 金手指"，反而更容易得到不重复的内容。

代价是跨集一致性需要额外的归约校验来兜底 —— 这是 s7 存在的理由。
"""
from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Send

from ..continuity import build_claims
from .. import __version__
from ..core.output_guard import validate_script
from ..schemas import Episode, EpisodeRenderResult, Finding, ValidationReport
from ..state import DramaState
from ..validators import validate_series
from ._common import bump, deps
from .episode import build_episode_graph

_EPISODE_GRAPH = None


def _episode_graph():
    global _EPISODE_GRAPH
    if _EPISODE_GRAPH is None:
        _EPISODE_GRAPH = build_episode_graph()
    return _EPISODE_GRAPH


# ============================================================================
# fan-out 派发
# ============================================================================
def dispatch_episodes(state: DramaState, config: RunnableConfig) -> list[Send]:
    """把大纲的每一条派发成一个独立的并行任务。

    Send 的 payload 精确等于子图的输入契约 —— 这比"把整个项目状态摊给每一集"更好：
    它能显式回答"这一集的生成过程允许看见什么"，而这个问题恰恰决定了内容的多样性。

    声明（claims）在派发时构建，而不是等生成完再想核对什么：
    这样"这一集必须兑现什么"和"这一集允许看见什么"一样，成为一份前置契约。
    """
    lib, runtime = deps(config)
    brief = state["brief"]
    behavior = state.get("behavior")
    ledger = state.get("ledger")
    cast = state["cast"]

    payloads = []
    for entry in state["outline"]:
        claims = build_claims(entry, lib, ledger, behavior, cast)
        payloads.append({
            "brief": brief,
            "workspace": state.get("workspace", ""),
            "content_form": state.get("content_form", {}),  # Phase 6
            "gadget": state["gadget"],
            "cast": cast,
            # 冻结后的底座：子图只读，并以"本集切片"的形式注入提示词
            "behavior": behavior,
            "ledger": ledger,
            "outline_entry": entry,
            "claims": claims,
            "prose": None,       # Phase 6.4: 预初始化确保子图 invoke 返回 prose 字段
            "attempt": 0,
            "max_attempts": 2,
            "findings": [],
            "unclaimed": [],
            "trace": [],
        })
    return [Send("gen_episode", p) for p in payloads]


def gen_episode(state: dict, config: RunnableConfig) -> dict[str, Any]:
    """逐集工作者：调用已编译的子图，并把结果映射回主图状态。

    Phase 6.3: 统一 EpisodeRenderResult contract。
    所有 content_form（audio/prose）都返回 episode_results 列表，
    由 operator.add reducer 聚合到父图。
    """
    result = _episode_graph().invoke(state, config)
    calls = int((result.get("budgets") or {}).get("llm_calls", 1))
    form = (state.get("content_form") or {}).get("key", "audio_drama")
    entry = state.get("outline_entry")
    ep_index = entry.episode if hasattr(entry, "episode") else 0

    # ---- 构建统一的 EpisodeRenderResult ----
    ep_result = EpisodeRenderResult(
        episode_index=ep_index,
        content_form=form,
        writer=form,  # placeholder; writer registry can override
        writer_version="1.0",
        renderer_version="1.0",
    )

    # Prose 路径
    prose_output = result.get("prose")
    if prose_output is not None:
        ep_result.prose_story = prose_output
        ep_result.validation_passed = len(result.get("current") or []) == 0
        ep_result.output_guard_passed = result.get("output_guard_passed", False)
        return {
            "episode_results": [ep_result],
            "trace": list(result.get("trace") or []),
            "budgets": {"llm_calls": calls},
            "findings": list(result.get("findings") or []),
        }

    # Audio 路径
    episode: Episode = result["episode"]
    ep_result.audio_episode = episode
    ep_result.validation_passed = len(result.get("current") or []) == 0
    return {
        "episode_results": [ep_result],
        "episodes": [episode],
        "findings": list(result.get("findings") or []),
        "outstanding_episodes": list(result.get("current") or []),
        "unclaimed": list(result.get("unclaimed") or []),
        "trace": list(result.get("trace") or []),
        "budgets": {"llm_calls": calls},
    }


# ============================================================================
# 归约：跨集校验
# ============================================================================
def s7_series_validate(state: DramaState, config: RunnableConfig) -> dict[str, Any]:
    """跨集一致性校验。这是并行化的必要代价。

    逐集校验看不见的问题只有在这里才会暴露：连续负面情绪超限、保护者始终没出场、
    副作用指向了一个不存在的集数、实际开口的角色总数超标，
    以及本次新增的三类：账目从没被任何一集触及、关系终局没有在结尾兑现、
    某个有声角色全剧没有做过一次选择。

    处理策略：**不自动回炉。** 全剧级缺陷的自动修复意味着重生成全部剧集，
    成本与收益严重不成比例。这里只做归因（算出受影响的集号范围），把决定权交给人。
    """
    lib, runtime = deps(config)
    episodes = state.get("episodes") or []
    unclaimed = sorted({u for u in (state.get("unclaimed") or []) if u})
    report = validate_series(
        episodes, state["outline"], state["cast"], lib, runtime.llm,
        ledger=state.get("ledger"), bible=state.get("behavior"), unclaimed=unclaimed,
    )

    affected = _affected_episodes(report, state["outline"])
    return {
        "series_report": report,
        "findings": report.findings,
        "final_report": {
            **(state.get("final_report") or {}),
            "series_validation": {
                "passed": report.passed,
                "errors": [f.model_dump() for f in report.errors],
                "affected_episode_plan": affected,
                "unclaimed_facts": unclaimed,
            },
        },
        "trace": [
            f"s7_series:passed={report.passed} errors={len(report.errors)} "
            f"patterns={report.by_pattern() or '{}'} unclaimed={len(unclaimed)} "
            f"affected={affected}"
        ],
    }


def _affected_episodes(report: ValidationReport, outline) -> dict[str, list[int]]:
    """把全剧级违规翻译成"需要重生成的集号范围"。

    这个映射就是后续做选择性重生成的接口 —— 有了它，"改了第 2 集的成功设定"
    才能只重跑第 4–6 集，而不是重跑全部 20 集。

    新增三类的归因方式与原有的一致：**能算出集号的一律给集号**，
    算不出的给"立项层"标记，因为它们的修复点在立项而不是某一集。
    """
    plan: dict[str, list[int]] = {}
    for f in report.findings:
        if f.rule_id == "R07":
            plan["polarity_rewrite"] = [e.episode for e in outline if e.polarity == "negative"]
        elif f.rule_id == "K06":
            plan.setdefault("side_effect_chain", [])
            plan["side_effect_chain"] = sorted(
                {e.episode for e in outline if e.act == 3} |
                {e.side_effect_of for e in outline if e.side_effect_of}
            )
        elif f.rule_id in ("K13", "K14", "R09"):
            plan["cast_presence"] = [1, 2, 3]
        elif f.rule_id in ("FC01", "FC04", "FC05"):
            # 账目未被兑现 → 需要重生成的正是"声明应该触及账目"的那些集
            plan.setdefault("ledger_delivery", [])
            plan["ledger_delivery"] = sorted(
                {ep for e in outline for ep in e.carried_facts}
            ) or [-1]  # -1 表示"立项层改账本"，不指向具体集
        elif f.rule_id in ("CH05", "CH01", "RL02"):
            # 人物选择未落地的复现集号只能靠大纲推断，算不出就给立项层标记
            plan.setdefault("behavior_delivery", [])
            plan["behavior_delivery"] = sorted(
                {e.episode for e in outline if e.exercised_choice or e.relation_turn}
            ) or [-1]
        elif f.rule_id == "EV05":
            plan["unclaimed_facts"] = [-1]  # -1 表示"立项层登记"，不指向具体集
    return plan


# ============================================================================
# 装配终产物
# ============================================================================
def _apply_output_guard(report: dict, episodes: list) -> None:
    """Phase 1.5: 最终交付前校验剧本完整性。"""
    guard = validate_script(report.get("script") or [], min_episodes=0)
    if not guard.ok():
        report["output_guard"] = {"passed": False, "issues": guard.issues}
    else:
        report["output_guard"] = {"passed": True, "issues": []}


def s8_assemble(state: DramaState, config: RunnableConfig) -> dict[str, Any]:
    """装配终产物。

    这里把"过程"与"结论"分开统计 —— 一个踩过的坑，值得留档：

    状态里的 findings 是**跨轮次的累积日志**，包含所有被修复掉的中间违规。
    第一版直接拿它当"校验结论"，结果报告显示 error=14 —— 而实际上那些问题
    在生成过程中就已被修复回路解决。用累积日志冒充交付质量，是这类流水线最典型的误报。

    所以最终报告分两块：
      - history    ：违规历史。用于成本与质量分析（哪条规则最常触发、平均修复几轮）
      - outstanding：各道校验门**最后一轮**的结论 + 各集最后一轮结论。这才是交付质量
    """
    lib, runtime = deps(config)
    episodes = sorted(state.get("episodes") or [], key=lambda e: e.episode)
    history: list[Finding] = list(state.get("findings") or [])

    outstanding: list[Finding] = []
    outstanding += list(state.get("outstanding_topic") or [])
    outstanding += list(state.get("outstanding_gadget") or [])
    outstanding += list(state.get("outstanding_cast") or [])
    outstanding += list(state.get("outstanding_behavior") or [])
    outstanding += list(state.get("outstanding_outline") or [])
    outstanding += list(state.get("outstanding_ledger") or [])
    outstanding += list(state.get("outstanding_episodes") or [])
    series_report = state.get("series_report")
    if series_report is not None:
        outstanding += list(series_report.findings)

    usage = {}
    if hasattr(runtime.telemetry, "summary"):
        usage = runtime.telemetry.summary()  # type: ignore[attr-defined]

    report = {
        "engine_version": __version__,
        "rule_library": lib.manifest(),
        "brief": state["brief"].model_dump(),
        "genre_id": state.get("genre_id"),
        "recipe_id": state.get("recipe_id"),
        "gadget": state["gadget"].model_dump() if state.get("gadget") else None,
        "cast": [c.model_dump() for c in state.get("cast") or []],
        "behavior": _behavior_summary(state.get("behavior")),
        "outline": [e.model_dump() for e in state.get("outline") or []],
        "ledger": state["ledger"].model_dump() if state.get("ledger") else None,
        "episode_count": len(episodes),
        "script": [_episode_to_plain(ep) for ep in episodes],
        "validation": {
            "outstanding": _summarize(outstanding),
            # 伪证模式分布：假反转 / 假信任 / 假因果 / 标签空转 各出现几次，落在哪一集。
            # 这是"人物是否有魅力、剧情是否接得住"最直接的一张体检表。
            "fake_patterns": _pattern_index(outstanding),
            "history": _summarize(history, with_details=False),
        },
        "continuity": _continuity_audit(state, episodes),
        "budgets": state.get("budgets") or {},
        "attempts": _clean_attempts(state.get("attempts") or {}),
        "usage": usage,
        "trace": state.get("trace") or [],
    }

    # ---- Phase 1.5: OutputGuard 最终交付校验 ----
    _apply_output_guard(report, episodes)

    return {
        "final_report": report,
        "usage_summary": usage,
        "trace": [
            f"s8_assemble:episodes={len(episodes)} "
            f"outstanding={len(outstanding)} history={len(history)} "
            f"patterns={list(_pattern_index(outstanding)) or '无'}"
        ],
    }


def _summarize(findings: list[Finding], with_details: bool = True) -> dict:
    by_rule: dict[str, int] = {}
    for f in findings:
        by_rule[f.rule_id] = by_rule.get(f.rule_id, 0) + 1
    out = {
        "total": len(findings),
        "errors": len([f for f in findings if f.severity == "error"]),
        "warnings": len([f for f in findings if f.severity == "warning"]),
        "tier1": len([f for f in findings if f.tier == 1]),
        "tier2": len([f for f in findings if f.tier == 2]),
        "by_rule": by_rule,
        "by_location": _by_location(findings),
    }
    if with_details:
        out["details"] = [f.model_dump() for f in findings]
    return out


def _clean_attempts(attempts: dict[str, Any]) -> dict[str, Any]:
    """滤掉卡住判定用的内部指纹字段，只留面向使用者的信息。

    指纹原样保留没有价值（它只是一串规则编号），但"卡住过"这个事实必须留下 ——
    它意味着有缺陷被放行给人工了。
    """
    out: dict[str, Any] = {}
    for key, value in attempts.items():
        if key.endswith("_sig"):
            continue
        if key.endswith("_stuck") and not value:
            continue
        out[key] = value
    return out


def _by_location(findings: list[Finding]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        key = f.location or "project"
        out[key] = out.get(key, 0) + 1
    return out


def _pattern_index(findings: list[Finding]) -> dict[str, dict]:
    """伪证模式分布。这是"人物是否有魅力、剧情是否接得住"最直接的一张体检表。

    只统计 final 残留（outstanding），不统计历史 —— 被修好的假因果不是交付缺陷。
    """
    out: dict[str, dict] = {}
    for f in findings:
        if not f.pattern:
            continue
        slot = out.setdefault(f.pattern, {"count": 0, "rules": {}, "locations": []})
        slot["count"] += 1
        slot["rules"][f.rule_id] = slot["rules"].get(f.rule_id, 0) + 1
        if f.location and f.location not in slot["locations"]:
            slot["locations"].append(f.location)
    return out


def _behavior_summary(bible) -> dict | None:
    """人物层的可读摘要。

    刻意把"违背自身利益的选择有几条、落在哪些角色身上"提到最显眼的位置 ——
    这一个数字比整份行为卡更能说明"这部剧的人物有没有魅力"。
    """
    if bible is None:
        return None
    cards = []
    against_total = 0
    for card in bible.cards:
        against = [c for c in card.choices if c.against_self_interest]
        against_total += len(against)
        cards.append({
            "slot": card.slot,
            "name": card.name,
            "misbelief": card.misbelief,
            "arc": card.arc,
            "choices": len(card.choices),
            "pressure_types": sorted({c.pressure for c in card.choices}),
            "against_self_interest": len(against),
        })
    return {
        "hero_misbelief": next((c.misbelief for c in bible.cards if c.slot == "A"), None),
        "against_self_interest_total": against_total,
        "cards": cards,
        "relations": [
            {
                "pair": f"{e.a}→{e.b}",
                "axis": e.axis,
                "path": f"{e.initial}→{e.target}",
                "turning_points": [t.episode for t in e.turning_points],
            }
            for e in bible.relations
        ],
    }


def _continuity_audit(state: dict, episodes: list[Episode]) -> dict:
    """连续性体检：账目、认知边界、未登记伏笔。

    这一块刻意做成"可被人快速读完"的形态，因为它的使用者是编剧而不是程序：
    账本里每一笔账有没有被兑现、谁知道什么、还有哪些伏笔没登记，
    这三件事目前仍需要人的判断，引擎只负责把它们摊平在一张表上。
    """
    ledger = state.get("ledger")
    outline = state.get("outline") or []
    return {
        "ledger_entries": len(ledger.entries) if ledger else 0,
        "carried_per_episode": {
            str(e.episode): list(e.carried_facts) for e in outline if e.carried_facts
        },
        "exercised_choices": {
            str(e.episode): e.exercised_choice for e in outline if e.exercised_choice
        },
        "relation_turns": {
            str(e.episode): e.relation_turn for e in outline if e.relation_turn
        },
        "unclaimed_facts": sorted({u for u in (state.get("unclaimed") or []) if u}),
        "episodes_generated": len(episodes),
    }


def _episode_to_plain(ep: Episode) -> dict:
    """装配成"可直接交给 TTS"的扁平脚本。

    每一行都带 speaker / kind / sfx_category —— 下游 TTS 与音效库只需要这些字段，
    不需要理解节拍结构。这是引擎与音频渲染之间的稳定契约。
    """
    lines = []
    for beat in ep.beats:
        for ln in beat.lines:
            lines.append({
                "id": ln.id,
                "segment": beat.segment,
                "kind": ln.kind,
                "speaker": ln.speaker,
                "text": ln.text,
                "start_sec": ln.start_sec,
                "end_sec": ln.end_sec,
                "sfx_category": ln.sfx_category,
                "event": ln.event,
                "emotion_peak": ln.emotion_peak,
            })
    return {
        "episode": ep.episode,
        "title": ep.title,
        "duration_sec": ep.duration_sec,
        "hook_type": ep.cliffhanger_hook_type,
        "revision": ep.revision,
        "line_count": len(lines),
        "lines": lines,
    }
