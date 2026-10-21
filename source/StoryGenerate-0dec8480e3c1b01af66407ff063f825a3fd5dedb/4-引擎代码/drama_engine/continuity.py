"""本集切片与声明构建（continuity / accountability 层）。

这个模块解决两个问题，它们分别是"剧情接得住"与"人物有魅力"的落点：

一、**本集必须承接什么。**
   逐集生成是并行且相互隔离的 —— 这是为了内容多样性（见 nodes/series.py 的论证）。
   但隔离不能以"事实失忆"为代价：倒计时、资源余量、认知边界、承诺，都是结构约束，
   不是内容。所以把它们切成**本集相关的切片**注入，而不是把前面所有集的正文摊开。
   隔离的是文本，连续的是事实。

二、**本集的声明该如何被核对。**
   引擎手里已经有一层完整的"声明"：大纲字段（副作用来源 / 量化成果 / 定点投放 /
   金手指失效 / 演练的选择 / 关系转折）、账本账目、行为卡选择规律。
   把声明展开成一份**应然清单**（claim），再与正文逐一比对 ——
   两者之间的差，就是假反转、假信任、假因果与标签空转的定义。
   不在声明层与正文层之间做这个比对，就只能靠人读，规则也永远无法迭代。

注意一个刻意的取舍：claim 的 rule_id 取的是**"若未兑现则违反了哪条规则"**，
而不是新建一套编号。这样修复路由可以直接复用既有的 ROUTE_MAP，
报告里的违规分布也仍然按语义规则聚合，不需要为"声明"再开一套统计口径。
"""
from __future__ import annotations

from .config import RuleLibrary
from .schemas import (
    BehaviorBible,
    CharacterCard,
    Claim,
    FactLedger,
    OutlineEntry,
)

# 单集送进证据评审的声明数上限（兜底值；正式值从规则库 evidence_claims_cap 读取）。
# 上限存在的理由：声明清单会随账本变厚而线性增长，而语义裁判的输入是整集正文，
# 声明过多会稀释裁判注意力并推高成本。超出时按优先级截断（声明类优先于账目类）。
DEFAULT_MAX_CLAIMS = 12

# 声明优先级：数字越小越先保留。
# 排序依据是"漏检的代价" —— 填了字段却正文空转，是最难被人眼发现的缺陷。
_CLAIM_PRIORITY = {
    "quantified_gain": 0,
    "exercised_choice": 1,
    "side_effect_of": 2,
    "reversal": 2,
    "relation_turn": 3,
    "deployment": 4,
    "gadget_failed": 5,
    "knowledge": 6,
    "countdown": 7,
    "resource": 8,
    "carried_fact": 9,
}


# ============================================================================
# 一、本集切片
# ============================================================================
def ledger_slice(ledger: FactLedger | None, ep_no: int) -> dict[str, list[dict]]:
    """切出本集相关的账目。

    三类，语义不同，不能混成一个列表：
      - due        ：本集到期，必须兑现（倒计时）
      - must_mention：本集必须触及（承接）
      - active     ：本集仍然有效（背景约束，例如"药还够用"）
    """
    if ledger is None:
        return {"due": [], "must_mention": [], "active": []}

    due, mention, active = [], [], []
    for e in ledger.entries:
        d = e.model_dump()
        if e.due_at == ep_no:
            due.append(d)
        if ep_no in e.expected_mentions:
            mention.append(d)
        if e.established_at <= ep_no and (e.due_at is None or e.due_at >= ep_no):
            active.append(d)
    return {"due": due, "must_mention": mention, "active": active}


def behavior_slice(bible: BehaviorBible | None, names: list[str]) -> list[dict]:
    """切出指定角色的行为卡（选择规律）。

    只给本集相关的角色，不给全部 —— 给全部会让每一集都试图照顾所有角色的弧光，
    反而每集都不聚焦。
    """
    if bible is None:
        return []
    wanted = set(names)
    return [c.model_dump() for c in bible.cards if c.name in wanted]


def relations_slice(bible: BehaviorBible | None, ep_no: int,
                    total_episodes: int | None = None,
                    tail_span: int = 3) -> dict[str, list[dict]]:
    """切出本集相关的关系材料。分两类，语义不同：

      turns   ：本集**发生**关系转折（行为卡里 turning_points.episode == ep_no）
      landings：本集在**收束区**（尾段 tail_span 集内），关系终局必须被听见

    为什么要有 landings 这一类：转折点和大结局通常不在同一集。
    "第 3 集两人关系好转"不等于"第 6 集听众还记得他们关系好"——
    关系是一条线，转折是线上的节点，收束是线的落点。只喂转折点，
    结果就是每集都写得很热闹，但结尾时这条关系线在听众印象里是断的。
    """
    if bible is None:
        return {"turns": [], "landings": []}

    turns: list[dict] = []
    for edge in bible.relations:
        hits = [t.model_dump() for t in edge.turning_points if t.episode == ep_no]
        if hits:
            turns.append({
                "a": edge.a, "b": edge.b, "axis": edge.axis,
                "initial": edge.initial, "target": edge.target,
                "turns": hits,
            })

    landings: list[dict] = []
    if total_episodes:
        in_tail = ep_no > max(0, total_episodes - tail_span)
        if in_tail:
            for edge in bible.relations:
                if edge.initial == edge.target:
                    continue  # 终局与初值相同的关系不需要收束戏
                hint = next((t.evidence_hint for t in reversed(edge.turning_points)
                             if t.evidence_hint), "")
                landings.append({
                    "a": edge.a, "b": edge.b, "axis": edge.axis,
                    "initial": edge.initial, "target": edge.target,
                    "landing_hint": hint,
                })
    return {"turns": turns, "landings": landings}


def continuity_slice(ledger: FactLedger | None, bible: BehaviorBible | None,
                     ep_no: int, cast: list[CharacterCard],
                     total_episodes: int | None = None) -> dict:
    """逐集生成要注入的全部连续性材料。这是 Send payload 的一个字段。"""
    names = [c.name for c in cast if c.is_voiced and not c.merged_into]
    return {
        "episode": ep_no,
        "facts": ledger_slice(ledger, ep_no),
        "behaviors": behavior_slice(bible, names),
        "relations": relations_slice(bible, ep_no, total_episodes),
    }


# ============================================================================
# 二、声明构建
# ============================================================================
def build_claims(entry: OutlineEntry, lib: RuleLibrary,
                 ledger: FactLedger | None = None,
                 bible: BehaviorBible | None = None,
                 cast: list[CharacterCard] | None = None) -> list[Claim]:
    """把"声明层"展开成本集的应然清单。

    纯确定性，零成本。这是证据评审能做到"每集只多花很少的裁判注意力"的前提：
    引擎已经知道自己声明过什么，只需要问一句"正文里兑现了没有"。
    """
    claims: list[Claim] = []

    # ---- 大纲字段类声明 ----
    if entry.side_effect_of:
        claims.append(Claim(
            claim_id=f"ep{entry.episode}.side_effect_of",
            episode=entry.episode, rule_id="K06", label="副作用声明",
            claim=f"本集的核心难题源于第 {entry.side_effect_of} 集的某一次成功",
            expect="正文里必须有人指认那一次成功（台词、旁白或音效动机），"
                   "让听众听得出这一集的麻烦是上一集赢来的",
            payload={"side_effect_of": entry.side_effect_of,
                     "core_goal": entry.core_goal},
            route="repair_outline",
        ))

    if entry.quantified_gain:
        claims.append(Claim(
            claim_id=f"ep{entry.episode}.quantified_gain",
            episode=entry.episode, rule_id="K11", label="可量化成果",
            claim=f"本集取得可量化成果：{entry.quantified_gain}",
            expect="正文里必须真的出现一个数量（几人也行、几石粮也行），"
                   "只写『威望提升』『众人叹服』不算兑现",
            payload={"quantified_gain": entry.quantified_gain},
            route="repair_beat",
        ))

    if entry.deployment:
        claims.append(Claim(
            claim_id=f"ep{entry.episode}.deployment",
            episode=entry.episode, rule_id="K18", label="定点投放",
            claim=f"本集定点投放感官型资产：{entry.deployment}",
            expect="正文里必须能指认出该资产（名字或明确的指代），且用法与前次不同",
            payload={"deployment": entry.deployment},
            route="repair_beat",
        ))

    if entry.gadget_failed:
        claims.append(Claim(
            claim_id=f"ep{entry.episode}.gadget_failed",
            episode=entry.episode, rule_id="K09", label="金手指失效",
            claim="本集安排金手指失效，用于确立主角本人的判断力",
            expect="正文里必须出现失败语义（不管用 / 不行 / 没救回来 / 缺了某样东西），"
                   "让听众意识到这次不是靠金手指过关的",
            payload={},
            route="repair_beat",
        ))

    # ---- 人物选择类声明 ----
    if entry.exercised_choice:
        rule = _choice_rule_id(entry.exercised_choice, bible)
        claims.append(Claim(
            claim_id=f"ep{entry.episode}.exercised_choice",
            episode=entry.episode, rule_id=rule, label="角色选择演练",
            claim=f"本集演练的角色选择：{entry.exercised_choice}",
            expect="正文里必须让听众听出这个选择被执行了 —— 台词、动作音或刻意的沉默；"
                   "只有情绪宣称而没有具体动作不算兑现",
            payload={"exercised_choice": entry.exercised_choice},
            route="repair_beat",
        ))

    if entry.relation_turn:
        claims.append(Claim(
            claim_id=f"ep{entry.episode}.relation_turn",
            episode=entry.episode, rule_id="RL02", label="关系转折",
            claim=f"本集发生关系转折：{entry.relation_turn}",
            expect="正文里必须有一个可指认的事件改变了这两人的相处方式，"
                   "不能只是台词里说了一句『我们不一样了』",
            payload={"relation_turn": entry.relation_turn},
            route="repair_beat",
        ))

    # 反转声明：降维段（act>=3）的循环结构里含反转点，因此每一集都被声明为"应有一次反转"。
    # 这条声明的核对分成两层，是本次改造里最省钱的一个判断：
    #   Tier-1 —— 模型自己声明了每行的情绪峰值，那么"反转行的峰值是否够高"是可判定的，
    #             不需要再问一个模型"你觉不觉得这算反转"
    #   Tier-2 —— 只有"反转是否引入新信息"这一件事必须读懂内容
    # 把可判定的部分留在 Tier-1，是这套设计一贯的做法：让模型声明，让引擎核对声明。
    if entry.act >= 3:
        claims.append(Claim(
            claim_id=f"ep{entry.episode}.reversal",
            episode=entry.episode, rule_id="EV02", label="反转声明",
            claim=f"本集属{('降维段' if entry.act == 3 else '升维段')}，"
                  f"按循环结构（{entry.loop_step or '未标注'}）应含一次情绪反转",
            expect="正文中 event=reversal 的行必须真的构成反转：情绪峰值不低于阈值，"
                   "且反转要引入新信息（新证据、新对手、新代价），"
                   "不能只是把已知情况说重一遍",
            payload={"act": entry.act, "loop_step": entry.loop_step},
            route="repair_beat",
        ))

    # ---- 账本类声明 ----
    if ledger is not None:
        claims.extend(_ledger_claims(entry, ledger, cast or []))

    # ---- 排序与截断 ----
    claims.sort(key=lambda c: (
        _CLAIM_PRIORITY.get(_claim_kind(c.claim_id), 99), c.claim_id
    ))
    cap = int(lib.continuity.get("evidence_claims_cap", DEFAULT_MAX_CLAIMS))
    return claims[:cap]


def _claim_kind(claim_id: str) -> str:
    tail = claim_id.split(".", 1)[-1]
    for kind in _CLAIM_PRIORITY:
        if tail.startswith(kind):
            return kind
    return tail


def _choice_rule_id(exercised: str, bible: BehaviorBible | None) -> str:
    """演练"违背自身利益"的选择时归到 CH05，否则归到 CH02。

    这样做的收益：报告里可以直接看出一部剧里"魅力时刻"出现在哪几集 ——
    因为 CH05 的违规分布就是魅力时刻的分布。
    """
    if bible is None:
        return "CH02"
    name, _, rest = exercised.partition("@")
    for card in bible.cards:
        if card.name != name.strip():
            continue
        for choice in card.choices:
            if choice.pressure in rest and choice.against_self_interest:
                return "CH05"
    return "CH02"


def _ledger_claims(entry: OutlineEntry, ledger: FactLedger,
                   cast: list[CharacterCard]) -> list[Claim]:
    ep = entry.episode
    names = {c.name for c in cast}
    voiced = {c.name for c in cast if c.is_voiced and not c.merged_into}
    out: list[Claim] = []

    for e in ledger.entries:
        if e.kind == "countdown" and e.due_at == ep:
            out.append(Claim(
                claim_id=f"ep{ep}.countdown.{e.id}",
                episode=ep, rule_id="FC01", label="倒计时到期",
                claim=f"账目「{e.subject}」本集到期：{e.statement}",
                expect="正文里必须出现到期带来的具体后果（不是提一句『时间到了』），"
                       "并给出一个数字或期限指认",
                payload={"subject": e.subject, "statement": e.statement,
                         "established_at": e.established_at, "due_at": e.due_at},
                route="repair_beat",
            ))

        if e.kind == "knowledge":
            leaked = [h for h in e.forbidden_holders if h in voiced]
            if leaked:
                out.append(Claim(
                    claim_id=f"ep{ep}.knowledge.{e.id}",
                    episode=ep, rule_id="FC03", label="认知边界",
                    claim=f"以下角色不应该知道「{e.subject}」：{leaked}",
                    expect=f"{leaked} 在本集中不得说出或使用关于「{e.subject}」的信息。"
                           "若他们本集没有开口，本条自然成立",
                    payload={"subject": e.subject, "forbidden_holders": leaked,
                             "holders": [h for h in e.holders if h in names]},
                    route="repair_beat",
                ))

        if e.kind == "resource" and e.established_at <= ep and (e.due_at is None or e.due_at >= ep):
            remaining = "" if e.remaining is None else f"（余量 {e.remaining}）"
            out.append(Claim(
                claim_id=f"ep{ep}.resource.{e.id}",
                episode=ep, rule_id="FC02", label="资源余量",
                claim=f"账目「{e.subject}」仍然有效{remaining}：{e.statement}",
                expect="若本集用到了该资源，用量必须与账目记载一致："
                       "账目余量为正时，正文不得写该资源已耗尽；"
                       "若本集没用到，本条自然成立",
                payload={"subject": e.subject, "statement": e.statement,
                         "remaining": e.remaining},
                route="repair_beat",
            ))

        if ep in e.expected_mentions and e.kind not in ("countdown", "knowledge", "resource"):
            out.append(Claim(
                claim_id=f"ep{ep}.carried_fact.{e.id}",
                episode=ep, rule_id="FC04", label="承接账目",
                claim=f"本集承接账目「{e.subject}」：{e.statement}",
                expect="正文里必须触及该账目（提及对象、偿还承诺或推进它），"
                       "否则这一集与前面的剧情没有接缝",
                payload={"subject": e.subject, "statement": e.statement,
                         "established_at": e.established_at},
                route="repair_beat",
            ))
    return out


# ============================================================================
# 三、面向提示词的文本组装
# ============================================================================
def render_continuity(slice_: dict) -> str:
    """把切片渲染成提示词段落。纯文本，便于模型直接引用。"""
    if not slice_:
        return "（本集无连续性约束）"
    facts = slice_.get("facts") or {}
    lines: list[str] = []

    due = facts.get("due") or []
    if due:
        lines.append("【本集必须兑现（到期）】")
        lines += [f"- [{e['id']}] {e['subject']}：{e['statement']}" for e in due]

    mention = facts.get("must_mention") or []
    if mention:
        lines.append("【本集必须承接】")
        lines += [f"- [{e['id']}] {e['subject']}：{e['statement']}" for e in mention]

    # 资源账目单独列出：它不是"必须提及"，而是"用了就必须对得上"。
    # 不渲染它，生成端就不知道余量是多少 —— 资源漂移（余量与账目矛盾）会从
    # "模型的失误"变成"引擎没给材料"：声明层核对着一个生成端从未见过的数字。
    resources = [e for e in (facts.get("active") or []) if e.get("kind") == "resource"]
    if resources:
        lines.append("【本集资源账目】")
        for e in resources:
            remaining = e.get("remaining")
            tail = f"余量 {remaining}" if remaining is not None else "余量未量化"
            lines.append(f"- [{e['id']}] {e['subject']}（{tail}）：{e['statement']}")

    if due or mention or resources:
        lines.insert(0, "账目是硬约束：数字、余量、谁知道，一律以账目为准，不得自行更改。")

    behaviors = slice_.get("behaviors") or []
    if behaviors:
        lines.append("【本集角色的选择规律】")
        for card in behaviors:
            lines.append(f"- {card['name']}（{card['slot']}）：想 {card['desire']}；"
                         f"怕 {card['fear']}；错误信念「{card['misbelief']}」；"
                         f"绝不 {card['boundary']}；破功时 {card['tell']}")
            for ch in card.get("choices", []):
                flag = " ★违背自身利益" if ch.get("against_self_interest") else ""
                lines.append(
                    f"    · {ch['pressure']} 压力下：{ch['trigger']} → "
                    f"{ch['choice']}（代价：{ch['cost']}）{flag}"
                )

    relations = slice_.get("relations") or {}
    turns = relations.get("turns") or []
    if turns:
        lines.append("【本集发生的关系转折】")
        for rel in turns:
            for turn in rel.get("turns", []):
                lines.append(f"- {rel['a']} → {rel['b']}（{rel['axis']}）："
                             f"{turn['event']}，方向转为 {turn['to']}")
                if turn.get("evidence_hint"):
                    lines.append(f"    正文里应表现为：{turn['evidence_hint']}")

    landings = relations.get("landings") or []
    if landings:
        lines.append("【本集必须给出的关系收束】")
        lines.append("这几条关系已在本剧前段埋下转折，本集处于收束区 —— "
                     "必须让听众听见它们现在处于什么状态，否则整条关系线在结尾处等于没有兑现。")
        for rel in landings:
            lines.append(f"- {rel['a']} → {rel['b']}（{rel['axis']}）："
                         f"终局应为 {rel['target']}（起点 {rel['initial']}）")
            if rel.get("landing_hint"):
                lines.append(f"    收束时的听觉落点：{rel['landing_hint']}")
    return "\n".join(lines)
