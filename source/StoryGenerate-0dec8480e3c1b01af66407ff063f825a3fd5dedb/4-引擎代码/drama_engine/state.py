"""图状态定义。

两点设计取舍：

1. **底座与草稿分离。** `bible`（立项结论：赛道/金手指/角色/大纲）一旦通过校验就冻结，
   逐集生成阶段只读不写。这避免了"改第 40 集时顺手改了人设"这类难以追踪的漂移。

2. **并行写出的字段必须挂 reducer。** 逐集生成是 fan-out 并行执行的，
   `findings` / `episodes` / `trace` 会被多个分支同时写，没有 reducer 会直接报错。
   reducer 同时承担了"按集号归并 + 排序"的职责。
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from .modes.base import ModeContext
from .characters.models import CharacterInput, ResolvedCharacter
from .schemas import (
    BehaviorBible,
    Brief,
    CharacterCard,
    Claim,
    Episode,
    EpisodeRenderResult,
    FactLedger,
    Finding,
    GadgetSpec,
    OutlineEntry,
    ValidationReport,
)


def merge_episodes(left: list[Episode], right: list[Episode]) -> list[Episode]:
    """按集号归并，后写覆盖先写，最终按集号排序。"""
    table = {ep.episode: ep for ep in left}
    for ep in right or []:
        table[ep.episode] = ep
    return [table[k] for k in sorted(table)]


def merge_dict(left: dict, right: dict) -> dict:
    """取最新值语义。用于"当前轮次号"这类字段。"""
    out = dict(left or {})
    out.update(right or {})
    return out


def sum_dict(left: dict, right: dict) -> dict:
    """求和语义。用于计数器（token、调用次数）。

    计数器必须求和而不能覆盖：逐集生成是并行的，N 个分支各返回 1 次调用，
    用覆盖语义最终只会剩下 1 —— 这会让成本画像彻底失真。
    """
    out = dict(left or {})
    for key, value in (right or {}).items():
        out[key] = out.get(key, 0) + value
    return out


class DramaState(TypedDict, total=False):
    # ---- 输入 ----
    brief: Brief
    workspace: str
    mode_context: ModeContext
    character_inputs: list[CharacterInput]
    resolved_characters: list[ResolvedCharacter]
    # Phase 5: Story Template
    resolved_template: dict | None
    # Phase 6: Content Form profile
    content_form: dict | None

    # ---- 立项底座（冻结后只读）----
    genre_id: str
    recipe_id: str
    gadget: GadgetSpec
    cast: list[CharacterCard]
    # 人物选择规律与关系边。与 cast 分开：cast 描述听觉辨识度，behavior 描述行为。
    # 两者在 gate_bible 之后一并冻结 —— 逐集生成只读，避免"改第 40 集时顺手改了人设"。
    behavior: BehaviorBible
    outline: list[OutlineEntry]
    # 事实账本。同样冻结：账目矛盾不能靠改台词掩盖，只能回到账本层修。
    ledger: FactLedger

    # ---- 逐集产物（并行写入，按集号归并）----
    episodes: Annotated[list[Episode], merge_episodes]
    # Phase 6.3: 统一 fan-out → fan-in contract（Audio + Prose）
    episode_results: Annotated[list[EpisodeRenderResult], operator.add]

    # ---- 校验与修复 ----
    findings: Annotated[list[Finding], operator.add]
    project_report: ValidationReport
    series_report: ValidationReport
    episode_report: ValidationReport
    # ---- 最终残留违规（与上面的"历史日志"是两件事）----
    # 立项五道门各自覆盖式写入自己的槽位：同一道门在修复回路里会跑多轮，
    # 只有最后一轮才代表"冻结时的状态"；一旦用累加语义，被修好的问题会永久留在报告里。
    outstanding_gadget: list[Finding]
    outstanding_cast: list[Finding]
    outstanding_behavior: list[Finding]
    outstanding_outline: list[Finding]
    outstanding_ledger: list[Finding]
    # 选题层的咨询级告警（例如赛道音频适配分偏低）也要进入最终报告 ——
    # 它不是错误，是一个必须被看见的取舍
    outstanding_topic: list[Finding]
    # 逐集是并行的，各集互不覆盖，因此用累加
    outstanding_episodes: Annotated[list[Finding], operator.add]
    # 正文中新出现、账本未登记的事实。并行分支各自追加，由 s7 汇总去重。
    unclaimed: Annotated[list[str], operator.add]
    repair_counts: Annotated[dict[str, int], sum_dict]
    approval: dict

    # ---- 预算与重试计数（两者语义不同，必须分开）----
    # budgets：累加型计数器，并行分支各自加一份
    budgets: Annotated[dict[str, int], sum_dict]
    # attempts：当前轮次号，只有串行的立项修复链会写，取最新值
    attempts: Annotated[dict[str, int], merge_dict]

    # ---- 观测 ----
    trace: Annotated[list[str], operator.add]
    usage_summary: dict

    # ---- 终产物 ----
    final_report: dict
    # Phase 6: 散文故事产出（仅 prose_story content form）
    prose: Any  # ProseStory — 不参与 reducer（逐集串行或单集）


class EpisodeState(TypedDict, total=False):
    """逐集子图的独立状态：只保留它真正需要的字段。

    子图状态收窄是刻意的 —— 让"这一集能看见什么"成为显式契约，
    而不是把整个项目状态摊开在每次调用里。

    关于连续性的一处刻意取舍：behavior / ledger 会传进来，但只以**本集切片**的形式
    注入提示词（见 continuity.continuity_slice）。隔离的是文本，连续的是事实 ——
    这样既不丢跨集一致性，又保住了"每集看不见别的集的正文"带来的内容多样性。
    """

    brief: Brief
    workspace: str
    content_form: dict  # Phase 6: Content Form profile (key, writer, validators, ...)
    gadget: GadgetSpec
    cast: list[CharacterCard]
    behavior: BehaviorBible
    ledger: FactLedger
    outline_entry: OutlineEntry
    episode: Episode
    # 本集需要兑现的声明（大纲字段 + 账目 + 关系转折）。
    # 在派发时就构建好，子图只负责核对，避免"生成完了才想起来要核对什么"。
    claims: list[Claim]
    # 累积日志：跨轮次只增不减，用于最终报告（append reducer）
    findings: Annotated[list[Finding], operator.add]
    # 本轮结论：每轮校验覆盖写入。路由与修复提示词只看它 ——
    # 若误用累积字段，上一轮的违规会永远拦住本轮，修复回路无法退出。
    current: list[Finding]
    attempt: int
    max_attempts: int
    # 修复是否卡住：连续两轮违规指纹一致即判定无效修复，立即放行交人工
    last_sig: str
    stuck: bool
    # Phase 6.5: OutputGuard 结果（prose_validate 写入；gen_episode 读取）
    output_guard_passed: bool
    # 本集正文中新出现、账本未登记的事实（供 s7 汇总告警）
    unclaimed: Annotated[list[str], operator.add]
    # 子图内部的调用计数。必须由子图自己报上来：
    # 主图看不见子图内部的模型调用，只按"1 次生成"记账会严重低估成本。
    budgets: Annotated[dict[str, int], sum_dict]
    trace: Annotated[list[str], operator.add]
    # Phase 6: 散文故事产出（prose_story content form 写入；audio 路径忽略）
    prose: Any
