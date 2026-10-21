"""Tier-1 确定性校验器。

这一层的存在理由：**大模型的自我检查不可靠，但规则的绝大部分是可以穷举判定的。**

覆盖范围与代价：
  - 覆盖 R01/R02(结构部分)/R03/R04/R05/R06/R09/R10/R11/R12 与 K01/K02/K03/K04/
    K06(可判定部分)/K07/K09/K12/K13/K14/K15/K16/K17/K18
  - 覆盖 CH01–CH06（人物选择规律）、RL01–RL05（关系变化）、FC01–FC06（事实账本）
  - 零 token 成本、毫秒级、结果可复现
  - 实测中约八成违规在这一层被拦下，真正需要送进语义裁判的只剩少数

## 把规则翻译成代码时会遇到的解释歧义（必须显式决策）

R11 原文是「每个场景至少 1 条环境音 + 1 条动作音」。但本引擎的节拍表里有 S1（0–3 秒）
这类 3 秒的刺激点，它天然只承载一个突发音效。若机械执行，每一集都会在第 3 秒被判违规。
故采取如下界定并在文档中留档：

  - 时长 ≥ 15 秒的节拍（真正的"场景"）：必须同时具备 AV4 环境音与 AV1 动作音
  - 时长 < 15 秒的刺激点：至少 1 条音效即可
  - 全集必须同时存在 AV4 与 AV1（缺任一即判失败）

## 适用域提醒

AR2 / AR3 / AR6 这几条弧线规则（每 3 集小反转、每 5 集高潮、峰值落在最后 30%）是按
60–80 集的长剧标定的。在 8 集试播体量上跑，会产出预期内的 warning 而非 error ——
这是规则适用域问题，不是剧本缺陷。引擎应按目标集数决定是否启用这几条。
"""
from __future__ import annotations

from ..config import RuleLibrary
from ..schemas import (
    BehaviorBible,
    CharacterCard,
    Episode,
    FactLedger,
    Finding,
    GadgetSpec,
    OutlineEntry,
)

# 违规类别 → 修复节点。这张表是"精准修复"而不是"整集重写"的关键。
ROUTE_MAP: dict[str, str] = {
    # 金手指层缺陷必须回到立项阶段修，不能靠改写台词糊过去
    "K01": "repair_gadget", "K02": "repair_gadget", "K03": "repair_gadget",
    "K16": "repair_gadget", "K17": "repair_gadget", "K18": "repair_gadget",
    # 角色槽位问题 → 合并/重设角色，而不是改写剧本
    "K12": "repair_cast", "K13": "repair_cast", "K14": "repair_cast",
    "K15": "repair_cast", "R09": "repair_cast",
    # 结构问题 → 改大纲
    "K04": "repair_outline", "K05": "repair_outline", "K06": "repair_outline",
    "K07": "repair_outline", "K09": "repair_outline", "K11": "repair_outline",
    "R07": "repair_outline", "R08": "repair_outline",
    # 单集节拍问题 → 只重写对应节拍
    "R01": "repair_beat", "R02": "repair_beat", "R03": "repair_beat",
    "R04": "repair_beat", "R05": "repair_beat", "R06": "repair_beat",
    # 音频化问题 → 只做听觉化改写（可确定性截断，无需模型）
    "R10": "repair_audio", "R11": "repair_audio", "R12": "repair_audio",
    # 合规 → 独立节点，永不自动放行
    "R13": "repair_compliance",
    # 人物层 → 回到行为卡重做。选择规律是设定，不能在单集里临时改
    "CH01": "repair_behavior", "CH02": "repair_behavior", "CH03": "repair_behavior",
    "CH04": "repair_behavior", "CH05": "repair_behavior", "CH06": "repair_behavior",
    # 关系层 → 同样回到行为卡（关系边与转折点都是立项级设定）
    "RL01": "repair_behavior", "RL02": "repair_behavior", "RL03": "repair_behavior",
    "RL04": "repair_behavior", "RL05": "repair_behavior",
    # 事实与连续性 → 回到账本重记。绝不能靠改台词掩盖账目矛盾
    "FC01": "repair_ledger", "FC02": "repair_ledger", "FC03": "repair_ledger",
    "FC04": "repair_ledger", "FC05": "repair_ledger", "FC06": "repair_ledger",
    # 证据层 → 按"缺什么补什么"回落：声明类改剧本，账目类改账本
    "EV01": "repair_beat", "EV02": "repair_beat", "EV03": "repair_beat",
    "EV04": "repair_outline", "EV05": "repair_ledger", "EV06": "repair_beat",
}

PROTECTED_ACT4_MIN_EPISODES = 4  # 集数少于该值时，不启用长剧弧线规则


def _severity(lib: RuleLibrary, rule_id: str) -> str:
    """严重级别一律从规则库取（三库统一查询）。

    早期实现写的是 "先查 validation_rules 再查 k_rules" 的两个分支 ——
    新增 CH/RL/FC/EV 四个规则族之后，那种写法会静默回落到默认值。
    """
    return lib.rule_by_id(rule_id).get("severity", "error")


def _finding(lib: RuleLibrary, rule_id: str, scope: str, message: str,
             hint: str = "", loc: str = "", pattern: str = "") -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=_severity(lib, rule_id),
        tier=1,
        location=loc or scope,
        message=message,
        repair_hint=hint,
        route=ROUTE_MAP.get(rule_id, "repair_beat"),
        pattern=pattern,
    )


def _cjk_len(text: str) -> int:
    return len("".join(ch for ch in text if not ch.isspace()))


def _cliche_hits(text: str, lib: RuleLibrary) -> list[str]:
    """同时检查两类无效取值，但用两种匹配方式。

    套话（cliche_blacklist）：子串命中 —— 『代价是消耗体力』里含『消耗体力』即算命中。
    占位符（placeholder_values）：整字段命中 —— 『没有』只有在整个字段就是『没有』时才命中。

    这个区分不是洁癖。把『没有』放进子串匹配，会让『从此没有回头路』被判为敷衍，
    同时让『还没有想好』这种真正的敷衍漏网 —— 正好把两类都判错。
    """
    stripped = (text or "").strip().strip("。.，,；;：: 　")
    hits = [w for w in lib.choice_cliche_blacklist if w in (text or "")]
    placeholders = lib.choice_placeholder_values
    if stripped and stripped in placeholders:
        hits.append(stripped)
    return hits


def offending_lines(episode: Episode, lib: RuleLibrary) -> list[str]:
    """重算命中 R10 / R12 的行 id。

    修复节点用它来确定"到底改哪几行"。刻意不去解析 Finding.message 里的字符串 ——
    那种做法在规则文案调整后会静默失效，是典型的脆弱耦合。
    """
    ids: list[str] = []
    for beat in episode.beats:
        for ln in beat.lines:
            if any(w in ln.text for w in lib.visual_blacklist):
                ids.append(ln.id)
            elif ln.kind in ("dialogue", "narration") and _cjk_len(ln.text) > 20:
                ids.append(ln.id)
    return ids


def beats_missing_sfx(episode: Episode, lib: RuleLibrary) -> list[tuple[str, list[str]]]:
    """返回 (节拍段, 缺失的音效类别) —— 供修复节点确定性地补音效锚点。"""
    out: list[tuple[str, list[str]]] = []
    for beat in episode.beats:
        span = beat.end_sec - beat.start_sec
        sfx_lines = [ln for ln in beat.lines if ln.kind == "sfx"]
        cats = {ln.sfx_category for ln in sfx_lines}
        if span >= 15.0:
            lacking = [c for c in ("AV4", "AV1") if c not in cats]
            if lacking:
                out.append((beat.segment, lacking))
        elif not sfx_lines:
            out.append((beat.segment, ["AV4"]))
    return out


def _speakers_of(episode: Episode) -> set[str]:
    return {
        ln.speaker
        for beat in episode.beats
        for ln in beat.lines
        if ln.kind == "dialogue" and ln.speaker
    }


# ============================================================================
# 金手指层
# ============================================================================
def check_gadget(gadget: GadgetSpec, lib: RuleLibrary) -> list[Finding]:
    out: list[Finding] = []
    assets = gadget.assets

    # ---- K17：多轨必须分工到不同价值层级（"一个治伤、一个管饭"就是这条的反例）
    layers = [a.layer for a in assets]
    if len(assets) >= 2 and len(set(layers)) < len(layers):
        dup = [ly for ly in set(layers) if layers.count(ly) > 1]
        out.append(_finding(
            lib, "K17", "gadget",
            f"多项金手指落在同一价值层级：{dup}（共 {len(assets)} 项）",
            "把其中一项重新分配到社会层（L2）或认知层（L4）。判据：删除任意一项，剧情是否仍不受影响？",
        ))

    # ---- K01：无限金手指必须配非资源型约束（显性感官型自带新鲜度约束，可豁免）
    for a in assets:
        if a.capacity == "infinite" and not a.non_resource_constraint:
            if a.curve == "TC2" and a.decay_plan:
                continue  # v1.2 豁免条款
            out.append(_finding(
                lib, "K01", "gadget",
                f"资产「{a.name}」为无限容量，但未定义非资源型约束",
                "从信任 / 权力 / 时间 / 知识盲区 中择一。知识盲区性价比最高。",
                loc=a.id,
            ))

    # ---- K02：必须有物资轨 + 专业知识轨
    if not any(a.knowledge_track for a in assets):
        out.append(_finding(
            lib, "K02", "gadget",
            "金手指缺少专业知识轨，只有物资轨，主角会退化为搬运工",
            "为 A 槽位角色绑定一个现代专业身份（护士/调度/会计/农技员…），并让其判断力影响结果。",
        ))

    # ---- K03：启动条件一句话讲得清
    forbidden = ("解封", "升级", "做任务", "完成任务", "签到", "累积到", "收集齐")
    for a in assets:
        if not a.activation.strip():
            out.append(_finding(lib, "K03", "gadget", f"资产「{a.name}」未定义启动条件", "", loc=a.id))
            continue
        hit = [w for w in forbidden if w in a.activation]
        if hit or _cjk_len(a.activation) > 40:
            out.append(_finding(
                lib, "K03", "gadget",
                f"资产「{a.name}」的启动条件不满足『一句话讲清』：{hit or '描述过长'}",
                "删掉一切解锁/成长机制，改成一句观众 3 秒内能懂的话。",
                loc=a.id,
            ))

    # ---- K16：金手指覆盖核心冲突时，冲突层级必须上移
    if gadget.core_conflict.strip() and not (gadget.escalated_conflict or "").strip():
        out.append(_finding(
            lib, "K16", "gadget",
            f"已声明核心冲突「{gadget.core_conflict}」，但未给出上移后的冲突层级",
            "判据：若主角每次施展金手指都成功、且成功后处境不变，则冲突层级定错了。把冲突整体上移一级。",
        ))

    # ---- K18：显性即时型资产必须安排定点投放与衰减
    for a in assets:
        if a.curve == "TC2" and not a.decay_plan:
            out.append(_finding(
                lib, "K18", "gadget",
                f"感官型资产「{a.name}」未安排衰减与定点投放",
                "每次投放引入新变量（新对象/新用途/新使用者/新代价），禁止同一用法重复两次以上。",
                loc=a.id,
            ))
    return out


# ============================================================================
# 角色层
# ============================================================================
def check_cast(cast: list[CharacterCard], lib: RuleLibrary) -> list[Finding]:
    out: list[Finding] = []
    limit = lib.max_voiced_characters

    # ---- R09 / K15：有声角色上限 + 听觉锚点齐备
    voiced = [c for c in cast if c.is_voiced and not c.merged_into]
    if len(voiced) > limit:
        out.append(_finding(
            lib, "K15", "cast",
            f"有声角色 {len(voiced)} 个，超出广播剧上限 {limit}：{[c.name for c in voiced]}",
            f"合并到 {limit} 个以内。优先把对照组合并为旁白提及，或让两个反派共用一条声线家族。",
        ))
    missing = [c.name for c in voiced if not (c.voice_anchor or "").strip()]
    if missing:
        out.append(_finding(
            lib, "R09", "cast",
            f"以下有声角色缺少 voice_anchor（可反复识别的听觉特征）：{missing}",
            "为每人补一条听觉特征：口癖、语速、气声、习惯性停顿或专属音效动机。",
        ))

    # ---- 槽位完整性
    slots = {c.slot for c in cast}
    for need, rule, desc in (
        ("A", "K02", "载体角色"),
        ("C", "K13", "权力保护者"),
        ("D", "K12", "私人化反派"),
        ("E", "K14", "降维见证者"),
    ):
        if need not in slots:
            out.append(_finding(lib, rule, "cast", f"缺少 {need} 槽位（{desc}）", "补齐该槽位角色。"))

    # ---- K12：反派必须同阵营
    for c in cast:
        if c.faction == "antagonist" and not c.same_camp:
            out.append(_finding(
                lib, "K12", "cast",
                f"反派「{c.name}」被标记为非同阵营",
                "敌国/异族只作背景。反派必须是自己人 —— 他吃着主角带来的饭，转头写弹劾主角的奏章。",
                loc=c.name,
            ))
    return out


# ============================================================================
# 人物行为层（规则库：人物与连续性规则.json · CH01–CH06 / RL01–RL05）
# ============================================================================
# 这一层的设计前提：**"性格"这个形容词不可执行，但"选择规律"可执行。**
# 性格 = 在特定压力下愿意放弃什么。因此每一条规则都落在可计数的字段上
# （覆盖了哪些压力类型、有几条选择声明了违背自身利益、代价字段是否为空）。


def check_behavior(bible: BehaviorBible, cast: list[CharacterCard],
                   lib: RuleLibrary) -> list[Finding]:
    out: list[Finding] = []
    by_name = {c.name for c in cast if c.is_voiced and not c.merged_into}
    cards = {c.name: c for c in bible.cards}
    pressures = set(lib.pressure_types)
    axes = set(lib.relationship_axes)

    # ---- CH01：覆盖性 + 必备字段 ----
    missing = sorted(by_name - set(cards))
    if missing:
        out.append(_finding(
            lib, "CH01", "behavior",
            f"以下有声角色缺少行为卡：{missing}",
            "角色卡（CharacterCard）描述听觉辨识度，行为卡描述选择规律，两者不可相互替代 —— "
            "一个角色可以声音极好认、行为却完全空洞。",
        ))
    for card in bible.cards:
        blank = [k for k in ("desire", "fear", "misbelief", "boundary", "tell", "arc")
                 if not getattr(card, k, "").strip()]
        if blank:
            out.append(_finding(
                lib, "CH01", "behavior",
                f"行为卡「{card.name}」缺少字段：{blank}",
                "六个字段各有职能：desire 驱动行动、fear 决定牺牲顺序、misbelief 驱动错误选择、"
                "boundary 让背叛可读、tell 是广播剧唯一的微表情通道、arc 是变化的兑现目标。",
                loc=card.name,
            ))
        if _cliche_hits(card.boundary, lib):
            out.append(_finding(
                lib, "CH01", "behavior",
                f"行为卡「{card.name}」的 boundary 是套话或占位符：{card.boundary}",
                "写出他真正不会做的那件具体的事。",
                loc=card.name,
            ))

    # ---- CH02 / CH03：数量与压力类型覆盖 ----
    min_choices = int(lib.threshold("choice_min", 3))
    min_pressures = int(lib.threshold("pressure_min", 2))
    for card in bible.cards:
        if len(card.choices) < min_choices:
            out.append(_finding(
                lib, "CH02", "behavior",
                f"行为卡「{card.name}」只有 {len(card.choices)} 条选择规律"
                f"（至少需 {min_choices} 条）",
                "一个只在一个压力下做过选择的人，是道具不是角色。",
                loc=card.name,
            ))
        used = {c.pressure for c in card.choices}
        if len(used) < min_pressures:
            out.append(_finding(
                lib, "CH03", "behavior",
                f"行为卡「{card.name}」的选择只覆盖 {sorted(used)}，"
                f"未达到 {min_pressures} 种压力类型",
                "性格差异只有在不同压力类型之间才暴露：他在利益上让利，却在道义上寸步不让。",
                loc=card.name,
            ))
        unknown = used - pressures
        if unknown:
            out.append(_finding(
                lib, "CH03", "behavior",
                f"行为卡「{card.name}」使用了未定义的压力类型：{sorted(unknown)}",
                f"允许值：{sorted(pressures)}",
                loc=card.name,
            ))

    # ---- CH04：代价必填且非套话 ----
    for card in bible.cards:
        for idx, ch in enumerate(card.choices):
            cost = (ch.cost or "").strip()
            hit = _cliche_hits(cost, lib)
            if not cost or hit or len(cost) < 4:
                out.append(_finding(
                    lib, "CH04", "behavior",
                    f"行为卡「{card.name}」第 {idx + 1} 条选择的代价不合格：{cost or '（空）'}"
                    + (f"（命中套话/占位符 {hit}）" if hit else ""),
                    "代价必须是这个角色独有的、具体可听的损失。"
                    "『代价是消耗体力』这类表述等于没写。",
                    loc=card.name,
                ))

    # ---- CH05：魅力判据（可计数，因此可校验）----
    # 这是本层最重要的一条：观众不会喜欢一个永远做最优解的人 —— 那是算法。
    against = [(c.name, ch) for c in bible.cards for ch in c.choices
               if ch.against_self_interest]
    min_against = int(lib.threshold("against_self_interest_min", 2))
    if len(against) < min_against:
        out.append(_finding(
            lib, "CH05", "behavior",
            f"全剧仅 {len(against)} 条『违背自身利益』的选择（至少需 {min_against} 条）",
            "人物魅力来自观众看着他在明知吃亏时依然这么选。把决定性的那一次写成代价明确的选择。",
        ))
    hero = next((c for c in bible.cards if c.slot == "A"), None)
    if hero is not None and not any(ch.against_self_interest for ch in hero.choices):
        out.append(_finding(
            lib, "CH05", "behavior",
            f"载体角色「{hero.name}」没有任何违背自身利益的选择",
            "主角若从不吃亏，他的胜利只是金手指的胜利，不是人物魅力的胜利。",
            loc=hero.name,
        ))

    # ---- CH06：错误信念 ----
    for card in bible.cards:
        mis = (card.misbelief or "").strip()
        if len(mis) < 6 or _cliche_hits(mis, lib):
            out.append(_finding(
                lib, "CH06", "behavior",
                f"行为卡「{card.name}」的错误信念不合格：{mis or '（空）'}",
                "错误信念是成长弧的引擎：角色按它行动、付出代价、才可能修正。"
                "写出他坚信但其实是错的那一句话。",
                loc=card.name,
            ))

    # ---- RL01：关系覆盖性 ----
    hero = next((c for c in cast if c.slot == "A"), None)
    if hero is not None:
        covered = {e.b for e in bible.relations if e.a == hero.name} | \
                  {e.a for e in bible.relations if e.b == hero.name}
        gap = sorted(by_name - covered - {hero.name})
        if gap:
            out.append(_finding(
                lib, "RL01", "behavior",
                f"以下角色与载体角色「{hero.name}」之间没有定义关系状态：{gap}",
                "有声角色不超过 5 个，这个规模下不允许有『关系未定义』的对话者 —— "
                "两人同场却没有关系状态，对话就只能是功能性的信息交换。",
            ))

    # ---- RL02 / RL03 / RL04：转折点 ----
    for edge in bible.relations:
        label = f"{edge.a}→{edge.b}@{edge.axis}"
        if edge.axis not in axes:
            out.append(_finding(
                lib, "RL02", "behavior", f"关系「{label}」使用了未定义的关系轴",
                f"允许值：{sorted(axes)}", loc=label,
            ))
        if not edge.turning_points:
            out.append(_finding(
                lib, "RL02", "behavior",
                f"关系「{label}」没有任何转折点",
                "关系变化必须可归因到一件事（『他替他挡了那一箭』），"
                "不能写成『时间久了就信任了』。",
                loc=label,
            ))
            continue

        blank = [t.episode for t in edge.turning_points if not (t.event or "").strip()]
        if blank:
            out.append(_finding(
                lib, "RL02", "behavior",
                f"关系「{label}」第 {blank} 个转折点没有绑定具体事件",
                "无事件的转折在自动化下无法兑现，在听众那里也不成立。",
                loc=label,
            ))

        eps = [t.episode for t in edge.turning_points]
        if eps != sorted(eps):
            out.append(_finding(
                lib, "RL03", "behavior",
                f"关系「{label}」的转折点集号不是升序：{eps}",
                "同一对关系的转折点必须按集号排列。",
                loc=label,
            ))

        # RL04：慢变量的方向反转必须有过程
        reversed_ = {edge.initial, edge.target} == {"toward", "away"}
        need_turns = int(lib.threshold("turning_points_for_reversal_min", 2))
        if reversed_ and edge.axis in lib.slow_axes and len(edge.turning_points) < need_turns:
            out.append(_finding(
                lib, "RL04", "behavior",
                f"慢变量关系「{label}」发生方向反转（{edge.initial}→{edge.target}），"
                f"但只有 {len(edge.turning_points)} 个转折点（至少需 {need_turns} 个）",
                "信任与欠负的反转是全剧最贵的一次关系变更。单点反转即『假信任 / 无债背叛』——"
                "听众认不出过程，只会觉得人物反复。至少拆成两次：先动摇，再倒向。",
                loc=label,
            ))

    # ---- RL05：关系轴多样性 ----
    if bible.relations and len({e.axis for e in bible.relations}) < 2:
        out.append(_finding(
            lib, "RL05", "behavior",
            f"全部关系边都落在同一种轴上：{sorted({e.axis for e in bible.relations})}",
            "全部关系都是对抗（或全部都是信任）的剧，人际关系是同质的，"
            "第 10 集与第 1 集在关系层面没有差别。",
        ))
    return out


# ============================================================================
# 大纲层
# ============================================================================
def check_outline(outline: list[OutlineEntry], lib: RuleLibrary) -> list[Finding]:
    out: list[Finding] = []
    total = len(outline)
    if total == 0:
        return [_finding(lib, "K04", "outline", "大纲为空", "")]

    act1 = [e for e in outline if e.act == 1]
    act3 = [e for e in outline if e.act == 3]
    act4 = [e for e in outline if e.act == 4]

    # ---- K04：现代段不得超过 15%
    # 适用域修正：15% 是按 60–80 集长剧标定的。6 集体量下 1 集就占 16.7%，
    # 机械执行会把"最小可行粒度"判成违规。故设下限：至少允许 1 集。
    allowed_act1 = max(1, int(total * 0.15 + 0.999)) if total else 1
    if total and len(act1) > allowed_act1:
        out.append(_finding(
            lib, "K04", "outline",
            f"现代段占 {len(act1)}/{total} 集（{len(act1) / total:.0%}），"
            f"超过上限（{allowed_act1} 集 / {allowed_act1 / total:.0%}）",
            "现代段唯一任务是给金手指一个合法来源，它不是主线。压缩到允许集数以内。",
        ))

    # ---- K05：现代段必须完成一次情绪释放
    if not act1:
        out.append(_finding(lib, "K05", "outline", "现代段为空，无法完成开场情绪释放", "至少保留 1 集现代段。"))
    elif not any(e.quantified_gain or e.polarity == "positive" for e in act1):
        out.append(_finding(
            lib, "K05", "outline",
            "现代段缺少一次情绪释放（对现代的告别或对背叛者的反击）",
            "给第 1 集一个可兑现的小爽点，否则观众在第一波就流失。",
        ))

    # ---- K06：降维循环第 5 步必须是"上一次成功的副作用"
    # 落地段及其后一集没有"前一次成功"，属正当豁免
    landing_last = max([x.episode for x in outline if x.act <= 2] or [1])
    missing_side = [e.episode for e in outline
                    if e.act == 3 and not e.side_effect_of and e.episode > landing_last + 1]
    if missing_side:
        out.append(_finding(
            lib, "K06", "outline",
            f"以下集数的难题未标注源于哪一次成功的副作用：第 {missing_side} 集",
            "每条循环的第 5 步必须写成『上一次成功的副作用』。只写前四步的剧本，"
            "观众会在第 20 集前后集体流失。",
        ))

    # ---- K07：结局必须完成升维
    if not act4:
        out.append(_finding(
            lib, "K07", "outline", "缺少第四幕（升维段）",
            "结局必须从『解决个人问题』升级到『改变历史进程』。格局不够是穿越剧最常见的差评来源。",
        ))

    # ---- K09：每 5 次循环至少 1 次金手指失效
    loops = len(act3)
    required = max(1, loops // 5) if loops else 0
    failures = [e.episode for e in act3 if e.gadget_failed]
    if required and len(failures) < required:
        out.append(_finding(
            lib, "K09", "outline",
            f"降维循环 {loops} 次，金手指失效仅 {len(failures)} 次（至少需 {required} 次）",
            "安排一次金手指失效来确立主角本人的价值 —— 一个从不失手的人不值得信任，只值得依赖。",
        ))

    # ---- K11：地位提升必须伴随可量化成果
    vague = [e.episode for e in outline if e.act in (3, 4) and not e.quantified_gain]
    if len(vague) > len(outline) * 0.6:
        out.append(_finding(
            lib, "K11", "outline",
            f"{len(vague)}/{total} 集缺少可量化成果（救活几人、推进几里、多打几石粮）",
            "模糊的『威望提升』没有说服力。",
        ))

    # ---- AR2 / AR3 / AR6：长剧弧线（仅在集数足够时启用，避免适用域错配）
    if total >= PROTECTED_ACT4_MIN_EPISODES * 5:
        for start in range(0, total, 3):
            block = outline[start:start + 3]
            if len(block) == 3 and not any(e.loop_step in ("S3", "S4", "S5") for e in block):
                out.append(_finding(
                    lib, "R08", "outline",
                    f"第 {block[0].episode}–{block[-1].episode} 集无中等冲突或小反转",
                    "每 3 集必须有一次中等冲突/小反转。",
                ))
        peak = max(outline, key=lambda e: e.conflict_intensity)
        if total and peak.episode / total < 0.7:
            out.append(_finding(
                lib, "R08", "outline",
                f"情绪峰值出现在第 {peak.episode} 集（全剧 {total} 集），未落在最后 30%",
                "全剧情绪峰值应落在临近结局的区间。",
            ))
    return out


# ============================================================================
# 事实与连续性层（规则库：人物与连续性规则.json · FC01–FC06）
# ============================================================================
# 这一层存在的理由是广播剧的硬约束：**听众无法靠脸认人，也无法靠画面补信息。**
# 因此"倒计时还有几天""药还剩几支""谁知道这件事"必须显式记账，
# 而不是指望剧本作者在第 30 集时还记得第 2 集说过什么。
#
# 账本与金手指之间有一处必须交叉校验：capacity=infinite 的资产不得登记消耗 ——
# "无限的东西"与"快用完了"不能同时成立，这是穿越剧最常见的自相矛盾。

DEPLETION_MARKERS = ("用完", "用尽", "见底", "耗尽", "不够", "只剩", "余量不多", "快要没有")


def check_ledger(ledger: FactLedger, outline: list[OutlineEntry],
                 gadget: GadgetSpec | None, cast: list[CharacterCard],
                 lib: RuleLibrary) -> list[Finding]:
    out: list[Finding] = []
    total = len(outline) or 1
    ep_numbers = {e.episode for e in outline}
    names = {c.name for c in cast}

    # ---- FC06：账本规模下限（放在最前面：账本过薄时后面的规则没有意义）----
    floor = max(3, (total + 1) // 2)
    if len(ledger.entries) < floor:
        out.append(_finding(
            lib, "FC06", "ledger",
            f"账本仅 {len(ledger.entries)} 条，低于下限 {floor}（每两集至少一笔账）",
            "账本过薄说明剧情里没有可承接的实体，本质上是流水账的结构信号。"
            "把倒计时、资源余量、承诺、物件归属都记进来。",
        ))

    covered: set[int] = set()
    for entry in ledger.entries:
        loc = f"{entry.id}:{entry.subject}"

        # ---- FC01：倒计时完整性 ----
        if entry.kind == "countdown":
            if entry.due_at is None:
                out.append(_finding(
                    lib, "FC01", "ledger",
                    f"账目「{entry.subject}」标记为倒计时，但没有到期集（due_at）",
                    "缺到期集的倒计时不是倒计时，是气氛。补上具体期限。",
                    loc=loc,
                ))
            elif entry.due_at <= entry.established_at:
                out.append(_finding(
                    lib, "FC01", "ledger",
                    f"账目「{entry.subject}」的到期集（{entry.due_at}）不晚于确立集（{entry.established_at}）",
                    "期限必须指向未来。",
                    loc=loc,
                ))

        # ---- FC03：认知边界 ----
        if entry.kind == "knowledge":
            overlap = sorted(set(entry.holders) & set(entry.forbidden_holders))
            if overlap:
                out.append(_finding(
                    lib, "FC03", "ledger",
                    f"账目「{entry.subject}」的知情者与禁知者重叠：{overlap}",
                    "同一个人不能既知道又不知道。这是账本录入错误，"
                    "放任它会让后续所有认知越界检查失效。",
                    loc=loc,
                ))
            ghost = sorted((set(entry.holders) | set(entry.forbidden_holders)) - names)
            if ghost:
                out.append(_finding(
                    lib, "FC03", "ledger",
                    f"账目「{entry.subject}」引用了不存在的角色：{ghost}",
                    f"角色名单：{sorted(names)}",
                    loc=loc,
                ))

        # ---- FC04 / FC05：承接覆盖 ----
        for ep in entry.expected_mentions:
            if ep not in ep_numbers:
                out.append(_finding(
                    lib, "FC04", "ledger",
                    f"账目「{entry.subject}」声明在第 {ep} 集被触及，但大纲中不存在该集",
                    f"大纲集号范围：1–{max(ep_numbers) if ep_numbers else 0}",
                    loc=loc,
                ))
            else:
                covered.add(ep)
        if entry.kind == "countdown" and entry.due_at and entry.due_at in ep_numbers:
            if entry.due_at not in entry.expected_mentions:
                out.append(_finding(
                    lib, "FC05", "ledger",
                    f"倒计时「{entry.subject}」到期于第 {entry.due_at} 集，"
                    f"但该集不在它的承接范围内",
                    "到期集必须自己负责兑现这件事，否则倒计时会静默消失 —— "
                    "听众记得那个期限，剧情却不记得。",
                    loc=loc,
                ))
            covered.add(entry.due_at)

    # ---- FC04：第三幕起每集至少被一笔账覆盖 ----
    late_uncovered = sorted(
        e.episode for e in outline if e.act >= 3 and e.episode not in covered
    )
    if late_uncovered:
        out.append(_finding(
            lib, "FC04", "ledger",
            f"以下集数没有任何账目与之关联（第三幕起每集至少一笔）：{late_uncovered}",
            "没有承接的集，是可以整段删除的注水集。把某笔旧账安排到这些集上兑现。",
        ))

    # ---- 交叉校验：资源账目 vs 金手指容量声明 ----
    if gadget is not None:
        for asset in gadget.assets:
            probes = _probes(asset.name)
            hit = [e for e in ledger.entries
                   if e.kind == "resource" and any(p in e.subject for p in probes)]
            if asset.capacity == "infinite":
                bad = [e for e in hit if any(m in e.statement for m in DEPLETION_MARKERS)]
                if bad:
                    out.append(_finding(
                        lib, "FC02", "ledger",
                        f"金手指「{asset.name}」声明为无限容量，"
                        f"但账目「{bad[0].subject}」记载了消耗或告罄",
                        "『无限的东西』与『快用完了』不能同时成立。"
                        "要么改容量声明，要么让这份账目指向一个确实有限的资源（例如容器、体力、时机）。",
                        loc=bad[0].id,
                    ))
            elif asset.capacity in ("limited", "depleting") and not hit:
                out.append(_finding(
                    lib, "FC02", "ledger",
                    f"金手指「{asset.name}」声明为{asset.capacity}（有限/会耗尽），但账本中没有任何余量账目",
                    "有限资源必须记账：初始量、每次消耗、剩余量。"
                    "不记账的有限资源，写到第 20 集一定会前后矛盾。",
                ))

    # ---- 交叉校验：大纲声明承接的账目必须真实存在 ----
    ids = {e.id for e in ledger.entries}
    for entry in outline:
        ghost = sorted(set(entry.carried_facts) - ids)
        if ghost:
            out.append(_finding(
                lib, "FC04", "ledger",
                f"第 {entry.episode} 集声明承接不存在的账目：{ghost}",
                f"账本现有条目：{sorted(ids)}",
                loc=f"episode-{entry.episode}",
            ))
    return out


def _probes(name: str) -> list[str]:
    """从资产名生成检索片段。两字以上取前两字，避免「药」这类单字过度命中。"""
    name = (name or "").strip()
    return [name[:2]] if len(name) >= 2 else ([name] if name else [])


# ============================================================================
# 单集层
# ============================================================================
def check_episode(episode: Episode, lib: RuleLibrary, cast: list[CharacterCard] | None = None) -> list[Finding]:
    out: list[Finding] = []
    beats = {b.segment: b for b in episode.beats}
    all_lines = [ln for b in episode.beats for ln in b.lines]

    # ---- R01：开场 3 秒内是突发型听觉事件，且不含背景介绍
    if "S1" not in beats:
        out.append(_finding(lib, "R01", "episode", "缺少 S1 黄金钩子节拍", "", "S1"))
    else:
        first = beats["S1"].lines[0] if beats["S1"].lines else None
        if first is None:
            out.append(_finding(lib, "R01", "episode", "开场 3 秒内没有任何声音事件", "", "S1"))
        elif not (first.kind in ("sfx", "silence") and first.sfx_category == "AV1"):
            out.append(_finding(
                lib, "R01", "episode",
                f"开场首个声音不是突发型听觉事件（当前为 {first.kind}/{first.sfx_category}）",
                "首个声音必须是 AV1 类：脆响、突发静音、金属落地、玻璃碎裂。",
                loc=first.id,
            ))
        opening_narration = [
            ln.id for ln in beats["S1"].lines
            if ln.kind == "narration" and ln.start_sec < 3.0
        ]
        if opening_narration:
            out.append(_finding(
                lib, "R01", "episode",
                f"开场 3 秒内出现旁白背景交代：{opening_narration}",
                "黄金钩子禁止介绍背景、交代身份。",
            ))

    # ---- R02（结构部分）：3–10 秒内完成四要素的口播承载
    if "S2" not in beats:
        out.append(_finding(lib, "R02", "episode", "缺少 S2 节拍，10 秒内无法交代矛盾四要素", "", "S2"))
    else:
        s2_dialogue = [ln for ln in beats["S2"].lines if ln.kind == "dialogue"]
        s2_speakers = {ln.speaker for ln in s2_dialogue if ln.speaker}
        # 阈值来自节拍表本身：S2 只有 7 秒，按中文广播剧约 2.3 秒/句计，3 句是物理上限附近
        if len(s2_dialogue) < 3 or len(s2_speakers) < 2:
            out.append(_finding(
                lib, "R02", "episode",
                f"S2 节拍不足以承载四要素：台词 {len(s2_dialogue)} 句、说话人 {len(s2_speakers)} 个",
                "至少 3 句台词、2 个说话人，才能让听众听出『谁 / 在哪 / 什么麻烦 / 想做什么』。",
                loc="S2",
            ))

    # ---- R03：30 秒内完成第一次情绪释放
    s3 = beats.get("S3")
    if s3 is None or not any(ln.event == "release" for ln in s3.lines):
        out.append(_finding(
            lib, "R03", "episode", "30 秒内没有出现第一次情绪释放（压→爆）",
            "必须在 S3 内完成一次压抑 → 爆发。禁止持续压抑。",
            loc="S3",
        ))
    else:
        peak = max((ln.emotion_peak for ln in s3.lines), default=0)
        if peak < 7:
            out.append(_finding(
                lib, "R03", "episode",
                f"S3 释放点强度仅为 {peak}/10，不足以构成第一次爽点",
                "把爆发点的情绪峰值提到 7 以上。",
                loc="S3",
            ))

    # ---- R04：情绪节点间隔 <= 30 秒
    nodes = sorted(ln.start_sec for ln in all_lines if ln.event)
    marks = [0.0] + nodes + [float(episode.duration_sec)]
    gap = max((b - a for a, b in zip(marks, marks[1:])), default=0.0)
    if gap > 30.0:
        worst = max(zip(marks, marks[1:]), key=lambda p: p[1] - p[0])
        out.append(_finding(
            lib, "R04", "episode",
            f"最长情绪空档 {gap:.1f} 秒（{worst[0]:.0f}s → {worst[1]:.0f}s），超过 30 秒上限",
            "每 20–30 秒必须落一个情绪节点，否则听众会走神。",
        ))

    # ---- R05：结尾钩子强度必须大于本集所有冲突
    if episode.cliffhanger_hook_type not in lib.hook_ids:
        out.append(_finding(
            lib, "R05", "episode",
            f"结尾钩子类型非法或缺失：{episode.cliffhanger_hook_type}",
            f"必须取六类钩子之一：{lib.hook_ids}。广播剧最优的是反转钩 H6 与危机钩 H5。",
        ))
    s6 = beats.get("S6")
    if s6 is None:
        out.append(_finding(lib, "R05", "episode", "缺少 S6 强钩子节拍", "", "S6"))
    else:
        s6_peak = max((ln.emotion_peak for ln in s6.lines), default=0)
        other_peak = max(
            (ln.emotion_peak for b in episode.beats if b.segment != "S6" for ln in b.lines),
            default=0,
        )
        if s6_peak < other_peak:
            out.append(_finding(
                lib, "R05", "episode",
                f"结尾钩子强度 {s6_peak} 低于本集最高冲突 {other_peak}",
                "结尾强度必须大于本集所有冲突，卡在情绪最高点戛然而止。",
                loc="S6",
            ))

    # ---- R06：单集只解决一个核心目标
    if len(episode.threads) != 1:
        out.append(_finding(
            lib, "R06", "episode",
            f"本集推进了 {len(episode.threads)} 条线索：{episode.threads}",
            "单集只解决一个核心目标，多线并进会让听众失去焦点。",
        ))

    # ---- R10：正文不得存在纯视觉描写
    hits: list[tuple[str, str]] = []
    for ln in all_lines:
        for word in lib.visual_blacklist:
            if word in ln.text:
                hits.append((ln.id, word))
    if hits:
        detail = "、".join(f"{lid}:{w}" for lid, w in hits[:6])
        out.append(_finding(
            lib, "R10", "episode",
            f"命中纯视觉描写黑名单 {len(hits)} 处（{detail}）",
            "改写为四类听觉事件之一：突兀的声音 / 被中断的台词 / 音乐情绪切换 / 环境音场改变。",
        ))

    # ---- R11：场景音效锚点（见文件头对 R11 的可执行化界定）
    episodes_cats = {ln.sfx_category for ln in all_lines if ln.sfx_category}
    for need, name in (("AV4", "环境音场"), ("AV1", "动作音")):
        if need not in episodes_cats:
            out.append(_finding(
                lib, "R11", "episode", f"全集缺少 {name}（{need}）",
                "音效承担『镜头』功能，全集至少要有 1 条环境音与 1 条动作音。",
            ))
    for beat in episode.beats:
        span = beat.end_sec - beat.start_sec
        sfx_lines = [ln for ln in beat.lines if ln.kind == "sfx"]
        cats = {ln.sfx_category for ln in sfx_lines}
        if span >= 15.0:
            lacking = [n for c, n in (("AV4", "环境音"), ("AV1", "动作音")) if c not in cats]
            if lacking:
                out.append(_finding(
                    lib, "R11", "episode",
                    f"节拍 {beat.segment}（{span:.0f}s）缺少 {'、'.join(lacking)}",
                    "超过 15 秒的场景必须同时有环境音与动作音。",
                    loc=beat.segment,
                ))
        elif not sfx_lines:
            out.append(_finding(
                lib, "R11", "episode",
                f"节拍 {beat.segment} 没有任何音效",
                "短刺激点也至少要有一个音效落点。",
                loc=beat.segment,
            ))

    # ---- R12：台词长度上限
    over = [
        (ln.id, _cjk_len(ln.text))
        for ln in all_lines
        if ln.kind in ("dialogue", "narration") and _cjk_len(ln.text) > 20
    ]
    if over:
        detail = "、".join(f"{lid}({n}字)" for lid, n in over[:6])
        out.append(_finding(
            lib, "R12", "episode",
            f"{len(over)} 句台词超过 20 字：{detail}",
            "拆成两句或删掉修饰语。单句过长会造成听觉疲劳。",
        ))
    return out


# ============================================================================
# 全剧层
# ============================================================================
def check_series(episodes: list[Episode], outline: list[OutlineEntry],
                 cast: list[CharacterCard], lib: RuleLibrary) -> list[Finding]:
    out: list[Finding] = []
    if not episodes:
        return [_finding(lib, "R07", "series", "没有生成任何剧集", "")]

    # ---- R07：连续负面情绪不超过 2 集
    seq = sorted(outline, key=lambda e: e.episode) if outline else []
    run, worst_end, worst_len = 0, 0, 0
    for entry in seq:
        if entry.polarity == "negative":
            run += 1
            if run > worst_len:
                worst_len, worst_end = run, entry.episode
        else:
            run = 0
    if worst_len > 2:
        out.append(_finding(
            lib, "R07", "series",
            f"第 {worst_end - worst_len + 1}–{worst_end} 集连续 {worst_len} 集负面情绪",
            "连续负面情绪不超过 2 集，且必须给出反击或反转缓冲。这是最常见的低完播死因。",
        ))

    # ---- R09：实际开口的有声角色数量
    voiced_names = set()
    for ep in episodes:
        voiced_names |= _speakers_of(ep)
    limit = lib.max_voiced_characters
    if len(voiced_names) > limit:
        out.append(_finding(
            lib, "R09", "series",
            f"实际开口的角色有 {len(voiced_names)} 个，超过上限 {limit}：{sorted(voiced_names)}",
            "合并声线或降级为旁白提及。听众无法靠脸认人。",
        ))

    # ---- K13：权力保护者必须在第 3 集前建立信任关系
    protector = next((c for c in cast if c.slot == "C"), None)
    if protector:
        early = [ep for ep in episodes if ep.episode <= 3]
        spoke = any(protector.name in _speakers_of(ep) for ep in early)
        if not spoke:
            out.append(_finding(
                lib, "K13", "series",
                f"权力保护者「{protector.name}」在第 3 集前没有建立信任关系（未出场说话）",
                "否则主角在整个落地段都缺乏政治庇护，寸步难行。",
            ))

    # ---- K14：降维见证者必须真的出现
    witness = next((c for c in cast if c.slot == "E"), None)
    if witness:
        spoke = any(witness.name in _speakers_of(ep) for ep in episodes)
        if not spoke:
            out.append(_finding(
                lib, "K14", "series",
                f"降维见证者「{witness.name}」从未出场，形同虚设",
                "他的从排斥到折服，就是听众信任主角的过程。必须给他至少一场戏。",
            ))

    # ---- K06（跨集校验）：副作用必须指向真实存在的更早成功
    ep_numbers = {ep.episode for ep in episodes}
    for entry in seq:
        if entry.side_effect_of is not None:
            if entry.side_effect_of >= entry.episode:
                out.append(_finding(
                    lib, "K06", "series",
                    f"第 {entry.episode} 集的副作用指向第 {entry.side_effect_of} 集，不构成『前一次成功』",
                    "副作用必须来自更早集数的成功。",
                ))
            elif entry.side_effect_of not in ep_numbers:
                out.append(_finding(
                    lib, "K06", "series",
                    f"第 {entry.episode} 集引用的前序成功（第 {entry.side_effect_of} 集）不存在",
                    "修正引用，或补上该集。",
                ))
    return out
