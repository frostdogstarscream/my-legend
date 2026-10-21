"""校验阶梯编排。

    输入 ──▶ Tier-1 确定性扫描（零成本、毫秒级）
                 │
                 ├─ 有 error ──▶ 直接短路，不调用语义裁判
                 │               （在这一层就修，不为一集有硬伤的剧本付裁判费）
                 │
                 └─ 无 error ──▶ 证据静态预筛（丢弃不适用项 / 报无歧义项）
                                     │
                                     └─▶ Tier-2 语义裁判（规则裁决 + 声明兑现裁决，同一次调用）
                                             │
                                             └─▶ 合并结论 ──▶ ValidationReport

三处成本设计，都是"省钱但不省校验"：

1. **硬伤短路。** 一集剧本如果开场 3 秒没有听觉钩子，
   "它的第 5 步是不是副作用"这个问题根本不必问 —— 先修硬伤。

2. **静态预筛只做两件事。** 丢弃可证明不适用的声明（禁知者本集没开口、
   资源本集没用到），报告无歧义未兑现的声明（声称有数字却全文无数字）。
   其余一律升级到语义裁判 —— 静态层多报一个假阳性，代价是一次白跑的修复调用。

3. **声明核对并进既有裁判调用。** 逐集原本就有一次语义裁判调用（K06/K08/R13），
   把全集声明并进去之后，**逐集调用次数不变**，但覆盖面从 4 条语义规则
   扩展到"4 条语义规则 + 全集所有声明"。这是本次改造最重要的成本判断。
"""
from __future__ import annotations

from ..config import RuleLibrary
from ..contracts import LLMProvider
from ..schemas import (
    BehaviorBible,
    CharacterCard,
    Claim,
    Episode,
    FactLedger,
    Finding,
    GadgetSpec,
    OutlineEntry,
    ValidationReport,
)
from . import deterministic as det
from . import evidence as ev
from .llm_judge import judge


def _report(scope: str, findings: list[Finding], tier2_calls: int,
            tier1_checked: int, claims_checked: int = 0,
            unclaimed: list[str] | None = None) -> ValidationReport:
    errors = [f for f in findings if f.severity == "error"]
    return ValidationReport(
        scope=scope,
        passed=not errors,
        findings=findings,
        tier1_count=sum(1 for f in findings if f.tier == 1),
        tier2_count=sum(1 for f in findings if f.tier == 2),
        llm_calls=tier2_calls,
        llm_calls_saved=tier1_checked if errors else 0,
        claims_checked=claims_checked,
        unclaimed=unclaimed or [],
    )


# ---------------------------------------------------------------- 立项：金手指
def validate_gadget(gadget: GadgetSpec, lib: RuleLibrary, llm: LLMProvider) -> ValidationReport:
    findings = det.check_gadget(gadget, lib)
    if any(f.severity == "error" for f in findings):
        return _report("gadget", findings, 0, tier1_checked=1)

    items = [{
        "rule_id": "K16",
        "subject": f"核心冲突：{gadget.core_conflict}",
        "payload": {"gadget": gadget.model_dump()},
    }]
    judged, calls, _ = judge(items, llm, lib, "gadget")
    return _report("gadget", findings + judged, calls, tier1_checked=1)


# ---------------------------------------------------------------- 立项：角色
def validate_cast(cast: list[CharacterCard], lib: RuleLibrary, llm: LLMProvider) -> ValidationReport:
    findings = det.check_cast(cast, lib)
    if any(f.severity == "error" for f in findings):
        return _report("cast", findings, 0, tier1_checked=1)
    # 角色层无需语义裁判：槽位、阵营、音色锚点都是声明式字段，确定性检查已穷尽
    return _report("cast", findings, 0, tier1_checked=1)


# ---------------------------------------------------------------- 立项：人物行为
def validate_behavior(bible: BehaviorBible, cast: list[CharacterCard],
                      lib: RuleLibrary, llm: LLMProvider) -> ValidationReport:
    """人物层校验。

    这一层**刻意不做语义裁判**：判断"选择规律是否合理"需要读懂全剧，
    成本极高且判据难以固定。可执行的做法是把"性格"拆成可计数的字段 ——
    压力类型覆盖了几种、有几条声明了违背自身利益、代价是否为空 ——
    确定性检查足以穷尽，而真正的"是否好看"由逐集的证据评审在正文层面回答。
    """
    findings = det.check_behavior(bible, cast, lib)
    return _report("behavior", findings, 0, tier1_checked=1)


# ---------------------------------------------------------------- 立项：事实账本
def validate_ledger(ledger: FactLedger, outline: list[OutlineEntry],
                    gadget: GadgetSpec | None, cast: list[CharacterCard],
                    lib: RuleLibrary, llm: LLMProvider) -> ValidationReport:
    """账本层校验。全部可确定性判定：集号、期限、角色名、余量语义都是声明式字段。"""
    findings = det.check_ledger(ledger, outline, gadget, cast, lib)
    return _report("ledger", findings, 0, tier1_checked=1)


# ---------------------------------------------------------------- 立项：大纲
def validate_outline(outline: list[OutlineEntry], lib: RuleLibrary,
                     llm: LLMProvider) -> ValidationReport:
    findings = det.check_outline(outline, lib)
    if any(f.severity == "error" for f in findings):
        return _report("outline", findings, 0, tier1_checked=1)

    # 大纲层的 K06 已由确定性检查穷尽（字段是否存在、引用是否成立），
    # 语义裁判在这里只负责前置合规 —— 不为一件事付两次钱。
    items = [{
        "rule_id": "R13",
        "subject": f"大纲共 {len(outline)} 集",
        "payload": {"outline": [e.model_dump() for e in outline]},
    }]
    judged, calls, _ = judge(items, llm, lib, "outline")
    return _report("outline", findings + judged, calls, tier1_checked=1)


# ---------------------------------------------------------------- 单集
def validate_episode(episode: Episode, lib: RuleLibrary, llm: LLMProvider,
                     cast: list[CharacterCard], outline_entry: OutlineEntry,
                     deep: bool = True, claims: list[Claim] | None = None) -> ValidationReport:
    findings = det.check_episode(episode, lib, cast)
    if any(f.severity == "error" for f in findings):
        # 硬伤短路：不为一集有结构性缺陷的剧本付语义裁判费。
        # 声明核对也一并跳过 —— 结构都错了，"它有没有兑现声明"无从谈起。
        return _report("episode", findings, 0, tier1_checked=1)

    items: list[dict] = []
    static_failures: list[Finding] = []
    escalate: list[Claim] = []

    if deep:
        # 证据静态预筛 → 升级项并入同一次裁判调用
        if claims:
            static_failures, escalate = ev.check_claims_static(claims, episode, lib)
            items.extend(ev.claims_to_items(escalate))
        # K06 的语义核对由声明 ep{N}.side_effect_of 承担（更严：它要求引用原文证据）。
        # 只有在本集没有对应声明时才退回旧的规则裁决形态，避免同一件事被裁决两次。
        has_side_effect_claim = any(c.claim_id.endswith("side_effect_of") for c in (claims or []))
        if not has_side_effect_claim:
            items.append({
                "rule_id": "K06",
                "subject": f"第 {episode.episode} 集：{episode.title}",
                "payload": {
                    "outline_entry": outline_entry.model_dump(),
                    "episode": episode.model_dump(),
                },
            })
        if episode.episode <= 2:
            items.append({
                "rule_id": "K08",
                "subject": f"第 {episode.episode} 集：首次降维三步",
                "payload": {"outline_entry": outline_entry.model_dump(),
                            "episode": episode.model_dump()},
            })
        items.append({
            "rule_id": "R13",
            "subject": f"第 {episode.episode} 集：合规",
            "payload": {"episode": episode.model_dump()},
        })

    # 裁判的环境材料：整集正文的可检索文本 + 情绪画像 + 说话人名单。
    # 只放一次，不挂在每条声明上 —— 声明核对要求逐条引用原文，
    # 把原文放在顶层比复制到每条声明里省一个数量级的 token。
    peaks = [ln.emotion_peak for beat in episode.beats for ln in beat.lines]
    context = {
        "episode_text": ev.episode_text(episode),
        "emotion_peak_span": (max(peaks) - min(peaks)) if peaks else 0,
        "speakers": sorted(ev.speakers_of(episode)),
        "reversal_lines": [
            ln.text for beat in episode.beats for ln in beat.lines if ln.event == "reversal"
        ],
        # 资源漂移的判定词表随环境材料下发：静态层用同一份做确定性判定，
        # 语义裁判用同一份做兜底 —— 两层共用规则库里的一个来源。
        "depletion_markers": lib.depletion_markers,
    }
    judged, calls, unclaimed = judge(items, llm, lib, f"episode-{episode.episode}",
                                     context=context)
    all_findings = findings + static_failures + judged
    return _report("episode", all_findings, calls, tier1_checked=1,
                   claims_checked=len(claims or []), unclaimed=unclaimed)


# ---------------------------------------------------------------- 全剧
def validate_series(episodes: list[Episode], outline: list[OutlineEntry],
                    cast: list[CharacterCard], lib: RuleLibrary,
                    llm: LLMProvider,
                    ledger: FactLedger | None = None,
                    bible: BehaviorBible | None = None,
                    unclaimed: list[str] | None = None) -> ValidationReport:
    findings = det.check_series(episodes, outline, cast, lib)
    findings += ev.check_continuity_series(
        episodes, ledger, bible, outline, cast, lib, unclaimed
    )
    return _report("series", findings, 0, tier1_checked=1)
