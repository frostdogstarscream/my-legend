"""基于正文证据的质量评审。

## 要解决的问题

结构规则能拦住"开场 3 秒没有听觉钩子"，但拦不住下面这一类更隐蔽的缺陷：

  - **假反转**：大纲标了 reversal，正文情绪却是平的
  - **假信任**：关系账目声明信任建立，正文里没有怀疑与验证
  - **假因果**：声明"本集难题源于第 3 集的成功"，正文里没人提过那件事
  - **标签空转**：字段填了"救回 21 名伤兵"，正文里一个数字都没有

它们的共同点是：**声明与正文之间出现了差**。所以这一层的做法不是"再写一条规则去猜"，
而是把声明层展开成应然清单（见 continuity.build_claims），再与正文逐一比对。

## 两级分工

    Tier-1 静态预筛（零成本）
        ├─ 可判定为"不适用"的声明 → 直接丢弃（禁知者本集没开口 / 资源本集没用到）
        ├─ 无歧义可判定的"未兑现" → 直接报错（声称有数字却全文无数字 /
        │   账目说资源还有、正文却说用完了）
        └─ 其余 → 升级到 Tier-2

    Tier-2 声明兑现复核（与既有语义裁判合并为同一次调用）
        └─ 固定形状 + **必须引用正文原文**。兑现了也要给证据 ——
           追问"你凭什么说它兑现了"比追问"为什么没兑现"更能拦住幻觉式通过。

合并调用是刻意的成本设计：逐集校验原本就有一次语义裁判调用（K06/K08/R13），
把声明核对并进去之后，**逐集的模型调用次数不变**，但校验覆盖面显著扩大。

## 静态层为什么不做更多

静态层只能证明"不存在"，不能证明"存在"。"他用了那东西"里的指代、
"七名"与"7 名"的写法差异，都会让简单的关键词匹配误判。
因此静态层只保留两类动作：丢弃不适用项、报告无歧义项。其余一律交给语义裁判 ——
宁可多花一次本来就要花的调用，也不制造假阳性去消耗修复预算。
"""
from __future__ import annotations

import re

from ..config import RuleLibrary
from ..schemas import (
    BehaviorBible,
    CharacterCard,
    Claim,
    Episode,
    FactLedger,
    Finding,
    OutlineEntry,
)

# 数字（阿拉伯或中文）—— 判定"可量化成果"是否兑现的最小无歧义证据
_DIGIT_RE = re.compile(r"[0-9０-９]|[一二三四五六七八九十百千万两半]")

# 失败语义标记 —— 判定"金手指失效"是否兑现
FAIL_MARKERS = (
    "不管用", "没用", "不行", "无效", "失灵", "失手", "失败", "没救回来", "救不回来",
    "缺", "不够", "用完了", "见底", "来不及", "错过", "晚了", "没成", "空手",
)

# 中文字符提取（用于把 subject 切成 bigram，抵御"三日之约 / 三天到了"这类同义改写）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

# 量词/状态后缀。裁掉它们之后再取 bigram，命中率更高：
# 「青霉素余量」→「青霉素」→ 青霉/霉素，而正文里通常写「青霉素」或「药」
_SUBJECT_SUFFIXES = (
    "余量", "存量", "数量", "余额", "余数", "剩余",
    "之约", "期限", "时限", "状态", "情况", "问题",
    "来源", "记录", "档案", "消息", "真相",
)


# ============================================================================
# 文本工具
# ============================================================================
def episode_text(episode: Episode, max_chars: int = 3000) -> str:
    """把整集正文压成可检索文本（含说话人与节拍标记）。

    Phase 4.5C: max_chars 截断，避免向 judge 发送完整长文本。
    """
    parts: list[str] = []
    total = 0
    for beat in episode.beats:
        header = f"[{beat.segment} {beat.label}]"
        parts.append(header)
        total += len(header)
        for ln in beat.lines:
            who = ln.speaker or ln.kind
            line = f"{who}: {ln.text}"
            parts.append(line)
            total += len(line)
        if total > max_chars:
            break
    return "\n".join(parts)


def speakers_of(episode: Episode) -> set[str]:
    return {
        ln.speaker for beat in episode.beats for ln in beat.lines
        if ln.kind == "dialogue" and ln.speaker
    }


def subject_tokens(subject: str) -> list[str]:
    """subject → 用于存在性检索的 2 字窗口。空列表表示无法检索（退回语义裁判）。"""
    stem = subject
    for suffix in _SUBJECT_SUFFIXES:
        if stem.endswith(suffix) and len(stem) > len(suffix) + 1:
            stem = stem[: -len(suffix)]
            break
    runs = _CJK_RE.findall(stem)
    stem = max(runs, key=len) if runs else ""
    if len(stem) < 2:
        return []
    return [stem[i:i + 2] for i in range(len(stem) - 1)]


def mentioned(text: str, subject: str) -> bool:
    """subject 是否在正文中被触及。无法检索时返回 True（交给语义裁判，不在此处判否）。"""
    tokens = subject_tokens(subject)
    if not tokens:
        return True
    return any(t in text for t in tokens)


# ============================================================================
# Tier-1：静态预筛
# ============================================================================
def _resource_drift(claim: Claim, text: str, lib: RuleLibrary) -> Finding | None:
    """资源漂移的确定性判定：账目余量为正，正文却说它耗尽了。

    为什么这一条可以放在静态层："余量 3 套"与"全用完了"是两个可直接比对的声明，
    不需要语义理解。判定材料缺一不可：
      - remaining 为 None（未量化）→ 无从比对，放行给语义裁判；
      - remaining == 0 → 正文写『用完了』恰是账目的兑现，不是矛盾；
      - 耗尽标记必须出现在**触及该资源的行**上 —— 整集范围内找"用完了"
        会把"别的东西用完了"误判到这条账目头上。

    这一条补齐了 resource_drift 的触发路径：此前它只停留在伪证词表里，
    schema 声明了它、裁判词表收录了它，但没有任何代码能产出它。
    """
    remaining = claim.payload.get("remaining")
    if remaining is None or remaining <= 0:
        return None
    markers = lib.depletion_markers
    if not markers:
        return None
    tokens = subject_tokens(claim.payload.get("subject", ""))
    if not tokens:  # 无法检索 → 交给语义裁判，不在静态层判否
        return None
    hit_line = next((ln for ln in text.splitlines()
                     if any(t in ln for t in tokens) and any(m in ln for m in markers)), None)
    if hit_line is None:
        return None
    return _finding(
        lib, claim, "resource_drift",
        f"账目声明「{claim.payload.get('subject')}」余量 {remaining}，"
        f"但正文写道『{hit_line.strip()}』—— 余量与账目矛盾",
        "要么按账目写实际剩余量，要么让角色在本集先消耗资源并同步账本 —— "
        "账目说还有，正文说没了，听众会在下一集发现两边对不上。",
        evidence=hit_line.strip(),
    )


def check_claims_static(claims: list[Claim], episode: Episode,
                        lib: RuleLibrary) -> tuple[list[Finding], list[Claim]]:
    """返回 (无歧义未兑现的 findings, 需要语义复核的 claims)。

    只见证两个方向：能证明"不适用"就丢弃，能证明"没兑现"就报错。
    其余一律放行到 Tier-2 —— 静态层多报一个假阳性，代价是一次白跑的修复调用。
    """
    text = episode_text(episode)
    speakers = speakers_of(episode)
    failures: list[Finding] = []
    escalate: list[Claim] = []

    for claim in claims:
        kind = claim.claim_id.split(".", 1)[-1]

        # ---- 可证明"不适用"→ 丢弃 ----
        if kind.startswith("knowledge"):
            leaked = claim.payload.get("forbidden_holders") or []
            if not (set(leaked) & speakers):
                continue  # 禁知者本集没有开口，认知边界不可能被突破
        if kind.startswith("resource"):
            if not mentioned(text, claim.payload.get("subject", "")):
                continue  # 本集没用到该资源，账目不可能被违背
            drift = _resource_drift(claim, text, lib)
            if drift:
                failures.append(drift)
                continue

        # ---- 可证明"没兑现"→ 直接报错 ----
        if kind.startswith("quantified_gain"):
            declared = claim.payload.get("quantified_gain", "")
            if declared and not _DIGIT_RE.search(text):
                failures.append(_finding(
                    lib, claim, "label_only",
                    f"本集声明可量化成果「{declared}」，但正文中没有任何数量",
                    "把成果写成听众能听见的数字：救回几人、推进几里、多打几石粮。",
                ))
                continue

        if kind.startswith("gadget_failed"):
            if not any(m in text for m in FAIL_MARKERS):
                failures.append(_finding(
                    lib, claim, "label_only",
                    "本集声明金手指失效，但正文中没有任何失败语义",
                    "必须让听众听出这次不是靠金手指过关的 —— 药不够、来不及、救不回来。",
                ))
                continue

        # 假反转的确定性判定：模型自己声明了每行的情绪峰值，
        # 那么"反转行的峰值够不够高"就是一个可判定命题。
        # 让模型声明、让引擎核对声明，比让第二个模型去感受"够不够反转"更便宜也更稳定。
        if kind.startswith("reversal"):
            floor = lib.threshold("reversal_peak_min", 7)
            peaks = [ln.emotion_peak for beat in episode.beats
                     for ln in beat.lines if ln.event == "reversal"]
            if not peaks:
                failures.append(_finding(
                    lib, claim, "fake_reversal",
                    "本集声明含反转，但正文中没有任何一条 event=reversal 的台词",
                    "反转必须有落点：一条被标记为 reversal 的高强度台词或音效。",
                ))
                continue
            if max(peaks) < floor:
                failures.append(_finding(
                    lib, claim, "fake_reversal",
                    f"本集声明含反转，但全部 reversal 行的情绪峰值仅 {max(peaks)}，"
                    f"低于阈值 {floor:.0f} —— 情绪曲线是平的",
                    "仅靠台词说『形势变了』不构成反转。把反转落在一个峰值足够高的节点上，"
                    "并让听众听到新信息。",
                ))
                continue

        escalate.append(claim)

    return failures, escalate


# ============================================================================
# Tier-2：声明兑现复核的请求组装
# ============================================================================
# 注意：本模块**不自己发起模型调用**。
# claim 项会被并进 validate_episode 的既有语义裁判请求（同一次调用同时裁决规则与声明），
# 因此逐集的模型调用次数不变，而校验覆盖面显著扩大 —— 这是刻意的成本设计。
def claims_to_items(claims: list[Claim]) -> list[dict]:
    """把 claim 转成裁判请求里的 item（与规则裁决共用同一个请求体）。"""
    return [
        {
            "kind": "claim",
            "claim_id": c.claim_id,
            "rule_id": c.rule_id,
            "subject": f"第 {c.episode} 集 · {c.label}",
            "claim": c.claim,
            "expect": c.expect,
            "payload": c.payload,
            "route": c.route,
        }
        for c in claims
    ]


# ============================================================================
# 跨集连续性校验（全剧层，确定性、零 token）
# ============================================================================
def check_continuity_series(episodes: list[Episode], ledger: FactLedger | None,
                            bible: BehaviorBible | None, outline: list[OutlineEntry],
                            cast: list[CharacterCard], lib: RuleLibrary,
                            unclaimed: list[str] | None = None) -> list[Finding]:
    """跨集视角下才能发现的问题。

    单集校验看不见它们：某一集里"资源没被提及"是正常的一集，
    但整部剧里某笔账从来没有被任何一集触及，就是一条断掉的线。
    """
    out: list[Finding] = []
    if not episodes:
        return out

    by_ep = {ep.episode: ep for ep in episodes}
    texts = {n: episode_text(ep) for n, ep in by_ep.items()}
    last_ep = max(by_ep)

    # ---- FC：账目从未兑现 / 记账不写 ----
    if ledger is not None:
        for entry in ledger.entries:
            touched = [n for n, t in texts.items() if mentioned(t, entry.subject)]
            if entry.expected_mentions and not (set(touched) & set(entry.expected_mentions)):
                out.append(Finding(
                    rule_id="FC04", severity=_sev(lib, "FC04"), tier=1,
                    location=f"episode-{entry.established_at}", route="repair_ledger",
                    pattern="label_only",
                    message=f"账目「{entry.subject}」声明应在第 {entry.expected_mentions} 集被触及，"
                            f"但全剧正文中从未出现",
                    repair_hint="要么补上兑现的戏，要么把这笔账从账本里删掉 —— "
                                "挂在账本上却从不出现的账目，会让后续所有承接判断失真。",
                ))
            if entry.kind == "countdown" and entry.due_at:
                window = range(entry.established_at, entry.due_at + 1)
                hits = sum(1 for n in window if n in texts and mentioned(texts[n], entry.subject))
                if hits < 2:
                    out.append(Finding(
                        rule_id="FC01", severity="warning", tier=1, route="none",
                        location=f"episode-{entry.established_at}", pattern="countdown_drift",
                        message=f"倒计时「{entry.subject}」在 {entry.established_at}–{entry.due_at} 集"
                                f"区间内仅被提及 {hits} 次（建议 ≥2）",
                        repair_hint="倒计时只在终点出现、中途不提，听众在到期时会想不起这件事。"
                                    "至少在中途给一次提示（台词、音效或旁白）。",
                    ))

    # ---- RL：关系终局未兑现 ----
    if bible is not None:
        tail = range(max(1, last_ep - 2), last_ep + 1)
        tail_text = "\n".join(texts[n] for n in tail if n in texts)
        for edge in bible.relations:
            if edge.initial == edge.target or edge.axis == "volatile":
                continue
            hint = " ".join(t.evidence_hint for t in edge.turning_points if t.evidence_hint)
            probes = subject_tokens(hint) or subject_tokens(f"{edge.a}{edge.b}")
            if probes and not any(p in tail_text for p in probes):
                out.append(Finding(
                    rule_id="RL02", severity="warning", tier=1, route="none",
                    location=f"{edge.a}-{edge.b}",
                    pattern="fake_trust",
                    message=f"关系「{edge.a}→{edge.b}（{edge.axis}）」声明终局为 {edge.target}，"
                            f"但最后三集的正文中找不到支撑痕迹",
                    repair_hint="关系终局需要在结尾附近有一次可听见的确认，"
                                "否则整条关系线等于没有兑现。",
                ))

        # ---- CH：魅力时刻是否只停留在设定里 ----
        declared = [e for e in outline if e.exercised_choice]
        if not declared:
            out.append(Finding(
                rule_id="CH05", severity="warning", tier=1, route="none",
                location="outline", pattern="label_only",
                message="大纲中没有任何一集声明演练角色选择（exercised_choice），"
                        "行为卡只停留在设定层，没有进入剧情",
                repair_hint="至少挑选 CH05 标记的『违背自身利益』的选择，安排到具体集数上兑现。"
                            "人物魅力不是设定写得多好，而是观众看着他在明知吃亏时依然这么选。",
            ))
        else:
            # 逐个有声角色检查：他的行为卡是否真的落到某一集上。
            # 一个从头到尾没有做过一次选择的角色，无论 voice_anchor 写得多细，都是背景板。
            exercised_names = {
                (e.exercised_choice or "").split("@")[0].strip() for e in declared
            }
            voiceless = [
                c.name for c in cast
                if c.is_voiced and not c.merged_into and c.name not in exercised_names
            ]
            if voiceless:
                out.append(Finding(
                    rule_id="CH01", severity="warning", tier=1, route="none",
                    location="outline", pattern="label_only",
                    message=f"以下有声角色全剧没有一次选择被演练：{voiceless}",
                    repair_hint="要么给他一集属于自己的选择（哪怕只有一句台词），"
                                "要么把他合并掉 —— 有声角色是稀缺资源，不该用来当背景板。",
                ))

    # ---- EV：未登记伏笔 ----
    if unclaimed:
        merged = sorted({u for u in unclaimed if u})
        out.append(Finding(
            rule_id="EV05", severity="warning", tier=1, route="none",
            location="series", pattern="label_only",
            message=f"正文中新出现、账本未登记的事实 {len(merged)} 条：{merged[:6]}",
            repair_hint="判断它们是伏笔还是废笔需要人的判断，因此本版只提取与告警。"
                        "确认为伏笔的，登记进账本并在后续集数安排兑现。",
        ))

    return out


# ============================================================================
# 工具
# ============================================================================
def _sev(lib: RuleLibrary, rule_id: str) -> str:
    return lib.rule_by_id(rule_id).get("severity", "error")


def _finding(lib: RuleLibrary, claim: Claim, pattern: str,
             message: str, hint: str, evidence: str = "") -> Finding:
    """构造一条证据类违规。

    两个字段各有分工，不要合并：
      rule_id —— 语义规则编号（K11/K09/…），决定修复去向与统计口径
      pattern —— 伪证模式分类（EV01–EV06），决定报告里如何被归类展示
    """
    ev_rule = lib.fake_patterns.get(pattern, {}).get("rule", "EV01")
    ev_title = lib.evidence_rules.get(ev_rule, {}).get("title", "")
    return Finding(
        rule_id=claim.rule_id,
        severity=_sev(lib, claim.rule_id),
        tier=1,
        location=f"episode-{claim.episode}",
        route=claim.route,
        pattern=pattern,
        evidence=evidence[:lib.evidence_max_chars],
        message=f"[{pattern}] {claim.label}：{message}",
        repair_hint=f"{hint}（判据 {ev_rule} {ev_title}）",
    )
