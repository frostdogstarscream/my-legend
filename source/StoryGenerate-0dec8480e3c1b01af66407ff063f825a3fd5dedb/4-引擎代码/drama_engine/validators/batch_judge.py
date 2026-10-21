"""Phase 4.5A: Batch Semantic Judge。

将原本分散在多处的 judge() 调用合并为一次批量调用：
    - 一次 LLM 调用覆盖所有 semantic rules + all claims
    - 保留逐条 rule_id / passed / severity / evidence 输出
    - QualityResult 粒度不丢失

与旧 judge() 的区别：
    旧：gadget_judge() + outline_judge() + episode_judge() = 3~4 LLM calls
    新：batch_judge(all_items) = 1 LLM call

使用方式：
    from .batch_judge import batch_judge
    findings, llm_calls, unclaimed = batch_judge(all_judge_items, llm, lib)
"""
from __future__ import annotations

import json
from typing import Any

from ..config import RuleLibrary
from ..contracts import LLMProvider
from ..llm.router import resolve_spec
from ..schemas import Finding, JudgeResponse

# 从 llm_judge.py 复用判断标准
from .llm_judge import (
    _KNOWN_PATTERNS,
    _SYSTEM,
    _criteria,
    _severity,
    _append_claim,
    DEFAULT_ROUTE,
)

# Batch Judge 专用 system prompt —— 覆盖更广泛的语义场景
_BATCH_SYSTEM = (
    "你是一名严苛的剧作审查员，服务于广播剧生成流水线。你需要对多个语义规则和多个声明"
    "进行一次批量裁决。\n\n"
    "每条裁决必须包含：\n"
    "  - rule_id: 规则编号\n"
    "  - passed: true/false\n"
    "  - severity: error | warning | info\n"
    "  - reason: 判定理由（中文，简明扼要）\n"
    "  - evidence: 剧本原文片段（不可转述、不可脑补）\n"
    "  - claim_id: 如果是声明裁决，填写声明 ID\n"
    "  - fake_pattern: 如果是声明未兑现，从给定词表选最接近的模式\n\n"
    "三条硬要求：\n"
    "1. evidence 必须是原文片段，不许改写、不许转述。\n"
    "2. 声明裁决里，兑现也必须给 evidence —— 引用不出原文，就按未兑现处理。\n"
    "3. 只依据给定材料判断，不发挥、不鼓励、不脑补未写出来的情节。\n"
    "4. 一次给出所有裁决结果，不要分批、不要省略。"
)


def batch_judge(
    all_items: list[dict],
    llm: LLMProvider,
    lib: RuleLibrary,
) -> tuple[list[Finding], int, list[str]]:
    """批量语义裁判 —— 一次 LLM 调用覆盖所有规则与声明。

    all_items: 合并来自所有 scope 的 judge items（gadget + outline + episodes）
    返回: (findings, llm_calls, unclaimed)
    """
    if not all_items:
        return [], 0, []

    claim_items = [i for i in all_items if i.get("kind") == "claim"]
    rule_items = [i for i in all_items if i.get("kind") != "claim"]

    # 构建批量 payload
    payload: dict[str, Any] = {}
    payload["scope"] = "batch"

    if rule_items:
        # 去重规则 criteria（多个 scope 可能有重复 rule_id）
        seen_rules: dict[str, str] = {}
        for i in rule_items:
            rid = i.get("rule_id", "")
            if rid and rid not in seen_rules:
                seen_rules[rid] = _criteria(lib, rid)

        payload["rule_criteria"] = seen_rules
        payload["rule_cases"] = [
            {
                "rule_id": i["rule_id"],
                "subject": i.get("subject", ""),
                "scope": i.get("scope", ""),
                "payload": i.get("payload", {}),
            }
            for i in rule_items
        ]

    if claim_items:
        payload["claim_cases"] = [
            {
                "claim_id": i["claim_id"],
                "rule_id": i["rule_id"],
                "subject": i.get("subject", ""),
                "scope": i.get("scope", ""),
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
        "请完成下列批量裁决。提交所有结果，不要遗漏。\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    )

    spec = resolve_spec("judge", extra={"json_mode": True, "batch": True})
    response: JudgeResponse = llm.complete_structured(spec, _BATCH_SYSTEM, user, JudgeResponse)

    # 解析结果
    findings: list[Finding] = []
    claim_index = {i["claim_id"]: i for i in claim_items}
    judged_claim_ids: set[str] = set()

    for verdict in response.verdicts:
        scope = verdict.scope or "batch"
        if verdict.claim_id:
            _append_claim(findings, verdict, claim_index, lib, scope)
            judged_claim_ids.add(verdict.claim_id)
        elif verdict.rule_id:
            if verdict.passed:
                continue
            findings.append(Finding(
                rule_id=verdict.rule_id,
                severity=verdict.severity or _severity(lib, verdict.rule_id),
                tier=2,
                location=scope,
                message=verdict.reason or "语义判据未通过",
                repair_hint=_criteria(lib, verdict.rule_id),
                route=DEFAULT_ROUTE.get(verdict.rule_id, "repair_beat"),
                evidence=(verdict.evidence or [])[:lib.evidence_max_chars] if verdict.evidence else [],
            ))

    # 未收到裁决的声明 = 不通过
    for claim_id, item in claim_index.items():
        if claim_id in judged_claim_ids:
            continue
        findings.append(Finding(
            rule_id=item["rule_id"],
            severity="warning",
            tier=2,
            location=item.get("scope", "batch"),
            route="none",
            message=f"声明 {claim_id} 未收到裁决，无法确认是否兑现",
            repair_hint="模型输出可能被截断，对单集重跑校验。",
        ))

    return findings, 1, [str(u) for u in (response.unclaimed or [])]