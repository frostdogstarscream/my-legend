"""主图编排。

                          ┌──────── 立项段（串行，因为每一步都依赖前一步的结论）
                          │
  START ─▶ s0_intake ─▶ s1_topic ─▶ s2_gadget ─▶ [gate_gadget] ─┐
                                                    ▲             │ 不通过
                                                    └── repair_gadget
                                                                  │ 通过
                                                                  ▼
                              s3_cast ─▶ [gate_cast] ─┬─(不通过)─▶ repair_cast
                                                      └─(通过)──▶ s3b_behavior
                                                                        │
                              [gate_behavior] ─┬─(不通过)─▶ repair_behavior
                                               └─(通过)──▶ s4_outline ─▶ [gate_outline]
                                                                              │
                          ┌───────────────────────────────────────────────────┘
                          │  通过
                          ▼
                    s5_ledger ─▶ [gate_ledger] ─┬─(不通过)─▶ repair_ledger
                                                └─(通过)──▶ gate_bible
                                                                  │
                          ┌────────────────────────────────────────┘
                          │  并行 fan-out（每集一个独立任务，互不可见）
                          ▼
                    gen_episode × N  ──▶ s7_series_validate ──▶ s8_assemble ──▶ END

四处值得说明的编排决策：

1. **立项段必须串行。** 金手指决定角色（C 槽位是权力保护者还是护短群体，取决于金手指强度），
   角色决定行为（要为每个有声角色产行为卡），行为决定大纲（大纲要把"本集演练哪条选择"
   排到具体集数上），大纲决定账本（账本要引用具体集号）。
   依赖链是四级的，任何"并行加速立项"的尝试都会产出自相矛盾的底座。

2. **门放在能修它的那一步之后。** 金手指门紧跟金手指节点，角色门紧跟角色节点，
   人物门紧跟行为卡节点，账本门紧跟账本节点。放在末尾统一校验，
   等于把便宜的错误升级成昂贵的错误。

3. **逐集段必须并行且相互隔离。** 见 nodes/series.py 的说明 —— 隔离是为了内容的多样性。
   注意隔离的是**文本**：本集仍会拿到"必须承接的账目 + 出场角色的选择规律"这一份切片，
   但不读任何其它集的正文。事实连续，文本不连续。

4. **立项段收尾在 gate_bible。** 冻结的东西现在是五样：金手指、角色、行为卡、大纲、账本。
"""
from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .config import RuleLibrary
from .contracts import Runtime, set_runtime_factory
from .nodes import (
    dispatch_episodes,
    gate_behavior,
    gate_bible,
    gate_cast,
    gate_gadget,
    gate_ledger,
    gate_outline,
    gen_episode,
    parallel_ledger_plan,   # Phase 4.5D
    repair_behavior,
    repair_cast,
    repair_gadget,
    repair_ledger,
    repair_outline,
    route_after_behavior,
    route_after_cast,
    route_after_gadget,
    route_after_ledger,
    route_after_outline,
    s0_intake,
    s1_topic,
    s2_gadget,
    s3_cast,
    s3b_behavior,
    s4_outline,
    s5_ledger,
    s7_series_validate,
    s8_assemble,
)
from .schemas import Brief
from .state import DramaState


def build_graph(checkpointer=None, use_checkpoint: bool = True):
    """编译主图。

    checkpoint 的意义：生成一部 60 集广播剧是**分钟到小时级的长任务**。
    没有 checkpoint，进程中断就意味着从第 1 集重来。
    接入生产时应把 InMemorySaver 换成 SqliteSaver 或 PostgresSaver。
    """
    graph = StateGraph(DramaState)

    # ---- 立项段 ----
    graph.add_node("s0_intake", s0_intake)
    graph.add_node("s1_topic", s1_topic)
    graph.add_node("s2_gadget", s2_gadget)
    graph.add_node("gate_gadget", gate_gadget)
    graph.add_node("repair_gadget", repair_gadget)
    graph.add_node("s3_cast", s3_cast)
    graph.add_node("gate_cast", gate_cast)
    graph.add_node("repair_cast", repair_cast)
    graph.add_node("s3b_behavior", s3b_behavior)
    graph.add_node("gate_behavior", gate_behavior)
    graph.add_node("repair_behavior", repair_behavior)
    graph.add_node("s4_outline", s4_outline)
    graph.add_node("gate_outline", gate_outline)
    graph.add_node("repair_outline", repair_outline)
    graph.add_node("s5_ledger", s5_ledger)
    graph.add_node("parallel_ledger_plan", parallel_ledger_plan)  # Phase 4.5D
    graph.add_node("gate_ledger", gate_ledger)
    graph.add_node("repair_ledger", repair_ledger)
    graph.add_node("gate_bible", gate_bible)

    # ---- 逐集段 ----
    graph.add_node("gen_episode", gen_episode)
    graph.add_node("s7_series_validate", s7_series_validate)
    graph.add_node("s8_assemble", s8_assemble)

    # ---- 立项段连边 ----
    graph.add_edge(START, "s0_intake")
    graph.add_edge("s0_intake", "s1_topic")
    graph.add_edge("s1_topic", "s2_gadget")
    graph.add_edge("s2_gadget", "gate_gadget")
    graph.add_conditional_edges(
        "gate_gadget", route_after_gadget,
        {"repair_gadget": "repair_gadget", "s3_cast": "s3_cast"},
    )
    graph.add_edge("repair_gadget", "gate_gadget")

    graph.add_edge("s3_cast", "gate_cast")
    graph.add_conditional_edges(
        "gate_cast", route_after_cast,
        {"repair_cast": "repair_cast", "s3b_behavior": "s3b_behavior"},
    )
    graph.add_edge("repair_cast", "gate_cast")

    graph.add_edge("s3b_behavior", "gate_behavior")
    graph.add_conditional_edges(
        "gate_behavior", route_after_behavior,
        {"repair_behavior": "repair_behavior", "s4_outline": "s4_outline"},
    )
    graph.add_edge("repair_behavior", "gate_behavior")

    graph.add_edge("s4_outline", "gate_outline")
    graph.add_conditional_edges(
        "gate_outline", route_after_outline,
        {"repair_outline": "repair_outline", "s5_ledger": "s5_ledger",
         "parallel_ledger_plan": "parallel_ledger_plan"},  # Phase 4.5D
    )
    graph.add_edge("repair_outline", "gate_outline")

    # Phase 4.5D: parallel path → gate_bible (skips gate_ledger, reconciler handles validation)
    graph.add_edge("parallel_ledger_plan", "gate_bible")

    graph.add_edge("s5_ledger", "gate_ledger")
    graph.add_conditional_edges(
        "gate_ledger", route_after_ledger,
        {"repair_ledger": "repair_ledger", "gate_bible": "gate_bible"},
    )
    graph.add_edge("repair_ledger", "gate_ledger")

    # ---- 立项冻结 → 并行派发 ----
    # 条件边的返回值是 list[Send]，LangGraph 会据此创建 N 个并行任务
    graph.add_conditional_edges("gate_bible", dispatch_episodes, ["gen_episode"])

    # ---- 归约：所有 gen_episode 分支都汇入 s7 ----
    graph.add_edge("gen_episode", "s7_series_validate")
    graph.add_edge("s7_series_validate", "s8_assemble")
    graph.add_edge("s8_assemble", END)

    saver = checkpointer if checkpointer is not None else (InMemorySaver() if use_checkpoint else None)
    return graph.compile(checkpointer=saver)


def run_pipeline(
    idea: str,
    workspace: str,
    lib: RuleLibrary,
    runtime: Runtime,
    target_episodes: int = 8,
    target_duration_sec: int = 180,
    locked_assets: list[str] | None = None,
    thread_id: str = "local",
    story_mode: str | None = None,
    story_template_id: str | None = None,       # Phase 5
    story_template_version: int | None = None,   # Phase 5
    content_form: str | None = None,              # Phase 6
    characters: list | None = None,
    graph=None,
    repos=None,   # Phase 4: optional persistence Repositories
) -> dict[str, Any]:
    """命令行 / 服务层入口。

    Phase 3: 新增 characters 参数 —— 支持用户提供部分/全部角色。
    Phase 4: 新增 repos 参数 —— persistence 支持 character_id DB 加载。
    Phase 5: 新增 story_template_id/version —— Template Resolver 集成。
    characters=None → 完全走旧 Cast 生成逻辑（backward compatible）。
    """
    from .modes import ModeContext, get_mode
    from .characters import CharacterResolver, CharacterInput
    from .templates.resolver import StoryTemplateResolver, build_template_resolver
    from .templates.repository import MemoryStoryTemplateRepository
    from .templates.defaults import BUILTIN_TEMPLATES

    mode = get_mode(story_mode)
    mode_ctx = ModeContext.from_mode(mode)

    # Phase 5: Template Resolver
    template_repo = MemoryStoryTemplateRepository()
    for t in BUILTIN_TEMPLATES:
        template_repo.create(t)
    default_template_id: dict[str, str] = {
        "general": "GENERAL_THREE_ACT",
    }
    template_resolver = StoryTemplateResolver(template_repo, default_template_id)
    resolved_template = template_resolver.resolve(
        template_id=story_template_id,
        version=story_template_version,
        mode_key=mode_ctx.key,
    )

    # Phase 6: Content Form resolution
    from .content_forms.resolver import resolve_content_form
    content_form_profile = resolve_content_form(content_form)

    # Phase 4: hydrate character_id → DB CharacterTemplate (if repos available)
    char_inputs: list[CharacterInput] = []
    if characters and repos:
        from .persistence.hydration import hydrate_character_inputs
        # Convert to dict form for hydration service
        raw_list = _to_raw_character_list(characters)
        char_inputs = hydrate_character_inputs(raw_list, repos.character_template)
    elif characters:
        char_inputs = [c if isinstance(c, CharacterInput) else _dict_to_char_input(c)
                       for c in characters]
        char_inputs = [c for c in char_inputs if c is not None]

    # Phase 3: Character Resolver
    max_chars = lib.max_characters(5)
    resolver = CharacterResolver(max_characters=max_chars)
    resolved, missing_count = resolver.resolve(char_inputs)

    set_runtime_factory(lambda: runtime)
    app = graph or build_graph()
    initial = {
        "brief": Brief(
            raw_idea=idea,
            target_episodes=target_episodes,
            target_duration_sec=target_duration_sec,
            locked_assets=locked_assets or [],
        ),
        "mode_context": mode_ctx,
        "resolved_template": resolved_template.model_dump() if resolved_template else None,  # Phase 5
        "content_form": content_form_profile.model_dump(),  # Phase 6
        "character_inputs": char_inputs,
        "resolved_characters": resolved,
        "workspace": workspace,
        "episodes": [],
        "findings": [],
        "unclaimed": [],
        "trace": [],
        "budgets": {},
        "prose": None,  # Phase 6.2: 预初始化，确保 Send fan-in 接受 prose 字段
    }
    config = {"configurable": {"lib": lib, "runtime": runtime}, "recursion_limit": 120,
              "configurable_thread": thread_id}
    config["configurable"]["thread_id"] = thread_id
    result = app.invoke(initial, config)

    # Phase 6.4: 从 episode_results 投影 prose 兼容字段
    ep_results = result.get("episode_results") or []
    if ep_results:
        first = ep_results[0]
        # 如果 transport 是 dict，转换为 model
        if isinstance(first, dict):
            from .schemas import EpisodeRenderResult
            first = EpisodeRenderResult.model_validate(first)
        if first.prose_story is not None:
            result["prose"] = first.prose_story
        if first.content_form == "audio_drama" and first.audio_episode is not None:
            # 确保 episodes 字段也正确
            if not result.get("episodes"):
                result["episodes"] = [ep_results[0].audio_episode if hasattr(ep_results[0], "audio_episode") else None]

    # Phase 4C: History Persistence（非阻塞；失败不丢故事）
    if repos:
        _persist_run_result(result, repos, idea, thread_id, story_mode, runtime)

    return result


def _persist_run_result(
    result: dict,
    repos,
    idea: str,
    thread_id: str,
    story_mode: str | None,
    runtime,
) -> None:
    """Phase 4C: 非阻塞保存生成结果到持久化存储。"""
    import logging
    import uuid
    from datetime import datetime, timezone

    _log = logging.getLogger("drama_engine.persistence")
    report = result.get("final_report") or {}
    if not report:
        return

    request_id = thread_id
    story_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)

    try:
        # 1. StoryJob
        from .persistence.repository import StoryJob
        job = StoryJob(
            job_id=uuid.uuid4().hex, request_id=request_id,
            status="completed", story_mode=story_mode or "",
            input_json={"idea": idea},
        )
        repos.story_job.create(job)

        # 2. StoryRecord (含角色快照)
        from .persistence.repository import StoryRecord
        cast = report.get("cast", [])
        record = StoryRecord(
            story_id=story_id, request_id=request_id,
            job_id=job.job_id, story_mode=story_mode or "",
            title="", summary=report.get("genre_id", ""),
            content_json=_build_content_json(result),
            output_json=report,
            resolved_character_snapshot=[
                {"name": c.get("name", ""), "role_id": c.get("role_id", ""),
                 "gender": c.get("gender", ""), "identity": c.get("identity", "")}
                for c in cast
            ],
            engine_version=report.get("engine_version", ""),
            template_ids=[
                (result.get("resolved_template") or {}).get("template_id", "")
            ] if result.get("resolved_template") else [],
            content_form=(result.get("content_form") or {}).get("key", ""),
            renderer_version=(result.get("content_form") or {}).get("version", ""),
        )
        repos.story_record.create(record)

        # 3. GenerationTraces (batch)
        from .persistence.repository import GenerationTrace
        tele = getattr(runtime, "telemetry", None)
        if tele:
            usage = tele.summary() if hasattr(tele, "summary") else {}
            # Record overall trace
            repos.generation_trace.batch_create([
                GenerationTrace(
                    trace_id=uuid.uuid4().hex, request_id=request_id,
                    job_id=job.job_id, node="total",
                    latency_ms=usage.get("total_latency_ms", 0),
                )
            ])

        # 4. QualityResults
        from .persistence.repository import QualityResult
        validation = report.get("validation", {}).get("outstanding", {})
        by_rule = validation.get("by_rule", {})
        for rule_id, detail in by_rule.items():
            repos.quality_result.create(QualityResult(
                story_id=story_id, rule_id=rule_id,
                severity="error" if "error" in str(detail).lower() else "warning",
                passed="PASS" in str(detail),
                details_json={"detail": detail},
            ))

    except Exception as exc:
        _log.warning("PERSISTENCE_WRITE_FAILED: story=%s error=%s", story_id, exc)


def _build_content_json(result: dict) -> dict[str, Any]:
    """Phase 6.5: 从 pipeline result 构建 content_json 持久化 payload。"""
    payload: dict[str, Any] = {}

    # ---- episode_results (canonical) ----
    ep_results = result.get("episode_results") or []
    if ep_results:
        serialized = []
        for er in ep_results:
            if hasattr(er, "model_dump"):
                serialized.append(er.model_dump(mode="json"))
            else:
                serialized.append(er)
        payload["episode_results"] = serialized

    # ---- prose payload (convenience; single-episode) ----
    prose = result.get("prose")
    if prose is not None and hasattr(prose, "model_dump"):
        payload["prose"] = prose.model_dump(mode="json")

    # ---- content_form metadata ----
    payload["content_form"] = (result.get("content_form") or {}).get("key", "")

    # ---- template ----
    tpl = result.get("resolved_template") or {}
    payload["template_id"] = tpl.get("template_id", "")
    payload["template_version"] = tpl.get("version", "")

    return payload


# ---- Phase 4 helpers ---- #
def _to_raw_character_list(characters: list) -> list[dict]:
    """将 CharacterInput 对象或 dict 统一转换为 dict 列表。"""
    from .characters import CharacterInput
    result = []
    for c in characters:
        if isinstance(c, CharacterInput):
            result.append({
                "character_id": c.character_id or c.canon.character_id if c.canon else "",
                "canon": c.canon.model_dump() if hasattr(c.canon, "model_dump") else {},
                "runtime": c.runtime.model_dump() if hasattr(c.runtime, "model_dump") else {},
            })
        elif isinstance(c, dict):
            result.append(c)
    return result


def _dict_to_char_input(d: dict) -> "CharacterInput":
    """将 dict 转换为 CharacterInput（backward compat）。"""
    from .characters import CharacterInput, CharacterCanon, CharacterRuntimeInput
    canon = CharacterCanon(**d.get("canon", {})) if d.get("canon") else CharacterCanon(name="")
    runtime = CharacterRuntimeInput(**d.get("runtime", {})) if d.get("runtime") else CharacterRuntimeInput()
    return CharacterInput(
        character_id=d.get("character_id", ""),
        canon=canon,
        runtime=runtime,
    )
