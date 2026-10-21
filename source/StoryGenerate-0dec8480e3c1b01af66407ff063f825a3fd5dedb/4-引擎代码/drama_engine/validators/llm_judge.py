"""Tier-2 语义裁判。

只把**无法穷举判定**的内容送进来，这是成本控制的关键：
Tier-1 已经把结构、计数、时长、黑名单、字段完整性全部拦掉，
这里只剩下两类需要"读懂内容才能判断"的裁决：

  ① 规则裁决（K06 / K08 / K16 / R13）—— 这段戏好不好
  ② 声明兑现裁决（claim）—— 这段戏有没有兑现它自己声明过的东西

两类合并进**同一次调用**，因此逐集的模型调用次数与改造前完全一致，
但覆盖面从"4 条语义规则"扩展到"4 条语义规则 + 全集所有声明"。

裁判的输出形状固定（JudgeResponse），且必须给出 evidence 原文片段 ——
原因是：语义判定永远可能误判，留证据才能让人工复核只花几秒钟。

关于证据要求的一处**刻意不对称**（值得留档）：

  - 规则裁决（K 类）：证据是"帮助复核"，缺失不判负 ——
    "这段戏是不是好戏"本身难以用一段原文证明。
  - 声明裁决（claim）：证据是"判据本身"。声明的兑现是一个存在性问题，
    引用不出原文就等于无法确认。因此 `passed=True` 但 `evidence` 为空时，
    本实现按不通过处理（规则库 evidence_requirements.when_evidence_empty 的原话）。
    这条不对称是刻意的：追问"你凭什么说它兑现了"，比追问"为什么没兑现"
    更能拦住幻觉式通过 —— 那也是"标签通过但正文没实现"唯一的自动化拦截点。
"""
from __future__ import annotations

import json

from ..config import RuleLibrary
from ..contracts import LLMProvider
from ..llm.router import resolve_spec
from ..schemas import Finding, JudgeResponse

# 规则裁决的判据说明。写死在代码里而不是让模型自由发挥 ——
# 否则"裁判标准"本身会漂移，同一份剧本两次跑出不同结论。
# 声明裁决的判据不在这里，而是来自规则库的 evidence_review_rules（EV01–EV06）。
JUDGE_CRITERIA: dict[str, str] = {
    "K06": (
        "该集的核心难题是否由『之前某一次成功』所引发（即副作用），而不是一个全新的独立问题。"
        "若该集难题与前面任何一次成功都无因果关系，判为不通过。"
    ),
    "K08": (
        "首次降维是否走完『被怀疑 → 验证 → 震动』三步。"
        "只有结果、没有怀疑与验证过程的，判为不通过（爽感会退化为廉价感）。"
    ),
    "K16": (
        "主角施展金手指之后，他的处境是否发生了实质变化。"
        "若每一次都成功、且成功后处境不变，说明冲突层级定低了，判为不通过。"
    ),
    "R13": (
        "是否包含强制爱、擦边内容、或价值导向明显偏差（如鼓励犯罪、美化霸凌）。"
        "命中任意一项判为不通过。"
    ),
}

DEFAULT_ROUTE = {
    "K06": "repair_outline", "K08": "repair_beat",
    "K16": "repair_gadget", "R13": "repair_compliance",
}

# 伪证模式 → 判据规则。模型给出未知模式时回落，避免脏值进入报告。
_KNOWN_PATTERNS = {
    "none", "label_only", "fake_reversal", "fake_trust", "fake_causality",
    "countdown_drift", "knowledge_leak", "resource_drift",
}

_SYSTEM = (
    "你是一名严苛的剧作审查员，服务于广播剧生成流水线。你要做两类裁决：\n"
    "【规则裁决】依据给定判据，判断这段戏是否达标。\n"
    "【声明裁决】流水线在自己产出的大纲与账本里做过一些声明（本集有可量化成果、"
    "本集难题源于第 N 集的成功、本集某角色会做出某个选择……）。"
    "你的任务是核对**声明是否被剧本正文真正兑现**，而不是核对声明写得好不好。\n\n"
    "三条硬要求：\n"
    "1. 每条裁决都要给出剧本或大纲中的**原文片段**作为证据，不许改写、不许转述。\n"
    "2. 声明裁决里，**兑现也必须给证据** —— 引用不出原文，就按未兑现处理。\n"
    "3. 只依据给定材料判断，不发挥、不鼓励、不脑补未写出来的情节。"
)


def _criteria(lib: RuleLibrary, rule_id: str) -> str:
    if rule_id in JUDGE_CRITERIA:
        return JUDGE_CRITERIA[rule_id]
    meta = lib.rule_by_id(rule_id)
    return meta.get("details") or meta.get("title") or ""


def judge(items: list[dict], llm: LLMProvider, lib: RuleLibrary,
          scope: str, context: dict | None = None) -> tuple[list[Finding], int, list[str]]:
    """执行语义裁判。items 可同时包含规则项与声明项。

    返回 (findings, llm_calls, unclaimed)。
    items 为空时不调用模型 —— 这一点很重要：绝大多数集数在 Tier-1 就已经全绿，
    不应该为它们付一次裁判成本。

    context 是给裁判的**环境材料**（整集正文的可检索文本、情绪画像、说话人名单）。
    它只放一次，不挂在每个 claim 上 —— 声明核对要求逐条引用原文，
    把原文放在顶层比复制到每条声明里省一个数量级的 token。
    """
    claim_items = [i for i in items if i.get("kind") == "claim"]
    rule_items = [i for i in items if i.get("kind") != "claim"]
    if not items:
        return [], 0, []

    payload: dict = {"scope": scope}
    if context:
        payload["context"] = context
    if rule_items:
        payload["rule_criteria"] = {i["rule_id"]: _criteria(lib, i["rule_id"])
                                    for i in rule_items}
        payload["rule_cases"] = [
            {"rule_id": i["rule_id"], "subject": i.get("subject", ""),
             "payload": i.get("payload", {})}
            for i in rule_items
        ]
    if claim_items:
        payload["claim_cases"] = [
            {
                "claim_id": i["claim_id"],
                "rule_id": i["rule_id"],
                "subject": i.get("subject", ""),
                "claim": i.get("claim", ""),
                "expect": i.get("expect", ""),
                "declared_payload": i.get("payload", {}),
            }
            for i in claim_items
        ]
        payload["fake_pattern_vocabulary"] = {
            p: lib.fake_patterns.get(p, {}).get("description", "")
            for p in _KNOWN_PATTERNS if p != "none"
        }
        payload["evidence_requirement"] = lib.continuity.get("evidence_requirements", {})

    user = (
        "请完成下列裁决。\n\n"
        "【规则裁决】对每条规则给出：rule_id、passed、reason、evidence。\n"
        "【声明裁决】对每条声明给出：claim_id、rule_id、passed、reason、evidence，"
        "未兑现时还要给出 fake_pattern（从给定词表中选一个）。\n"
        "【未登记事实】若正文里出现了账本与声明都未记录的新事实（新人物、新承诺、"
        "新期限），列进 unclaimed；没有就留空数组。\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    )

    spec = resolve_spec("judge", extra={
        "json_mode": True,
        "judge_items": items, "claims": claim_items, "context": context or {},
    })
    response: JudgeResponse = llm.complete_structured(spec, _SYSTEM, user, JudgeResponse)

    findings: list[Finding] = []
    claim_index = {i["claim_id"]: i for i in claim_items}
    judged_claim_ids: set[str] = set()

    for verdict in response.verdicts:
        if verdict.claim_id:
            _append_claim(findings, verdict, claim_index, lib, scope)
            judged_claim_ids.add(verdict.claim_id)
        elif verdict.rule_id:
            if verdict.passed:
                continue
            findings.append(Finding(
                rule_id=verdict.rule_id,
                severity=_severity(lib, verdict.rule_id),
                tier=2,
                location=scope,
                message=verdict.reason or "语义判据未通过",
                repair_hint=_criteria(lib, verdict.rule_id),
                route=DEFAULT_ROUTE.get(verdict.rule_id, "repair_beat"),
                evidence=verdict.evidence[:lib.evidence_max_chars],
            ))

    # 未收到裁决的声明：不能静默放行。
    # 模型输出可能被截断，也可能直接漏答 —— 两种情况下"没消息"都不等于"没问题"。
    for claim_id, item in claim_index.items():
        if claim_id in judged_claim_ids:
            continue
        findings.append(Finding(
            rule_id=item["rule_id"],
            severity="warning",
            tier=2,
            location=scope,
            route="none",
            message=f"声明 {claim_id}（{item.get('subject', '')}）未收到裁决，无法确认是否兑现",
            repair_hint="通常是裁判输出被截断。收紧 evidence_claims_cap，或对单集重跑校验。",
        ))

    return findings, 1, [str(u) for u in (response.unclaimed or [])]


def _append_claim(findings: list[Finding], verdict, claim_index: dict,
                  lib: RuleLibrary, scope: str) -> None:
    item = claim_index.get(verdict.claim_id)
    if item is None:
        return
    pattern = verdict.fake_pattern if verdict.fake_pattern in _KNOWN_PATTERNS else "label_only"
    evidence = (verdict.evidence or "").strip()

    # 规则库 evidence_requirements.when_evidence_empty：取不出原文，等同于无法确认。
    if verdict.passed and not evidence:
        findings.append(Finding(
            rule_id=item["rule_id"],
            severity="warning",
            tier=2,
            location=scope,
            route="none",
            pattern="label_only",
            message=f"声明 {verdict.claim_id} 被判为已兑现，但未附原文证据，无法确认",
            repair_hint="追问『你凭什么说它兑现了』比追问『为什么没兑现』更能拦住幻觉式通过。"
                        "要么补上原文片段，要么按未兑现处理。",
        ))
        return

    if verdict.passed:
        return

    ev_rule = lib.fake_patterns.get(pattern, {}).get("rule", "EV01")
    ev_title = lib.evidence_rules.get(ev_rule, {}).get("title", "")
    findings.append(Finding(
        rule_id=item["rule_id"],
        severity=_severity(lib, item["rule_id"]),
        tier=2,
        location=scope,
        route=item.get("route", "repair_beat"),
        pattern=pattern,
        evidence=evidence[:lib.evidence_max_chars],
        message=f"[{pattern}] 声明未兑现 · {item.get('claim', '')}"
                + (f"｜裁判理由：{verdict.reason}" if verdict.reason else ""),
        repair_hint=f"正文里应出现：{item.get('expect', '')}（判据 {ev_rule} {ev_title}）",
    ))


def _severity(lib: RuleLibrary, rule_id: str) -> str:
    return lib.rule_by_id(rule_id).get("severity", "error")
