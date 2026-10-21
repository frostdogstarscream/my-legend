"""领域模型（Schema-First）。

整个引擎的地基判断：**没有强类型输出，就没有可执行的校验。**
所以每个节点的产物都必须落在这里定义的模型上，模型字段刻意与规则库一一对应：

  AssetSpec.layer        ← K17 价值层级分工
  AssetSpec.curve        ← K18 感官型衰减律
  OutlineEntry.side_effect_of  ← K06 降维循环第 5 步
  OutlineEntry.deployment      ← K18 定点投放
  Line.sfx_category      ← AV1–AV4 听觉事件替代
  Episode.threads        ← R06 单集单目标
  ChoiceRule.cost        ← CH04 没有代价的选择不构成性格
  ChoiceRule.against_self_interest ← CH05 魅力判据（可计数，因此可校验）
  RelationshipEdge.turning_points  ← RL02 关系变化必须绑定事件
  FactEntry.due_at       ← FC01 倒计时
  FactEntry.holders / forbidden_holders ← FC03 认知边界
  OutlineEntry.carried_facts       ← FC04 承接覆盖
  Claim / ClaimCheck     ← EV01–EV06 声明与正文的差（假反转/假信任/假因果/标签空转）
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Slot = Literal["A", "B", "C", "D", "E"]
ValueLayer = Literal["L1", "L2", "L3", "L4"]          # 生存 / 社会 / 权力 / 认知
TensionCurve = Literal["TC1", "TC2"]                   # 隐性复利 / 显性即时
Capacity = Literal["infinite", "limited", "depleting"]  # 无限 / 有限 / 会耗尽
Act = Literal[1, 2, 3, 4]
LoopStep = Literal["S1", "S2", "S3", "S4", "S5"]
AudioEvent = Literal["AV1", "AV2", "AV3", "AV4"]
Polarity = Literal["positive", "neutral", "negative"]
LineKind = Literal["dialogue", "narration", "sfx", "music", "silence"]
BeatEvent = Literal["hook", "beat", "release", "reversal", "cliffhanger"]
Severity = Literal["error", "warning"]

# ---- 人物与连续性层（规则库：人物与连续性规则.json）----
PressureType = Literal["P1", "P2", "P3", "P4", "P5", "P6"]   # 生死/利益/名誉/情感/时间/道义
RelAxis = Literal["trust", "debt", "fear", "rivalry", "kinship"]  # 信任/欠负/忌惮/对抗/血缘
RelDirection = Literal["toward", "away", "frozen", "volatile"]
FactKind = Literal[
    "countdown",    # 倒计时：必须带 due_at
    "resource",     # 资源余量：必须与 capacity 声明一致
    "knowledge",    # 认知：谁知道 / 谁禁知
    "promise",      # 承诺与欠负：说过的话要还
    "possession",   # 物件：某物在谁手上
    "position",     # 身份与处境：官职、伤病、被囚
]
FakePattern = Literal[
    "none", "label_only", "fake_reversal", "fake_trust", "fake_causality",
    "countdown_drift", "knowledge_leak", "resource_drift",
]


# ---------------------------------------------------------------- 立项
class Brief(BaseModel):
    """用户给的一句话创意，归一化后的形态。"""

    raw_idea: str
    genre_id: str | None = None
    target_episodes: int = 8
    target_duration_sec: int = 180
    locked_assets: list[str] = Field(default_factory=list)  # 用户指定的金手指，不可被换掉
    audio_fit_floor: int = Field(
        default=70,
        description="音频适配分下限。低于此值不拒绝命题，但会产出咨询级告警 —— "
                    "静默改写用户的命题比保留一个低分选择更糟。",
    )
    notes: str = ""


# ---------------------------------------------------------------- 金手指
class AssetSpec(BaseModel):
    id: str
    name: str
    layer: ValueLayer = Field(description="价值层级：L1 生存 / L2 社会 / L3 权力 / L4 认知")
    curve: TensionCurve = Field(description="张力曲线：TC1 隐性复利 / TC2 显性即时")
    capacity: Capacity
    activation: str = Field(description="启动条件，必须一句话讲得清（K03）")
    knowledge_track: bool = Field(description="是否携带专业知识轨（K02）")
    non_resource_constraint: str | None = Field(
        default=None, description="非资源型约束：信任/权力/时间/知识盲区（K01）"
    )
    decay_plan: str | None = Field(
        default=None, description="感官型资产的衰减安排（K18）"
    )


class GadgetSpec(BaseModel):
    """金手指总规格。双轨分工是 K17 的载体。"""

    assets: list[AssetSpec]
    # 冲突层级：金手指若抹平了题材核心冲突，必须上移（K16）
    core_conflict: str
    escalated_conflict: str | None = None
    escalation_rationale: str = ""


# ---------------------------------------------------------------- 角色
class CharacterCard(BaseModel):
    slot: Slot
    name: str
    role_label: str
    voice_label: str = Field(description="一句话性格标签")
    voice_anchor: str = Field(description="可反复识别的听觉特征")
    speech_rate: float = 1.0
    catchphrase: str | None = None
    sfx_signature: str = Field(description="角色专属音效动机")
    faction: Literal["protagonist", "ally", "antagonist"] = "ally"
    same_camp: bool = Field(default=True, description="反派是否同阵营（K12）")
    is_voiced: bool = True
    merged_into: str | None = Field(
        default=None, description="被合并时的宿主角色名（K15 的修复手段）"
    )


# ---------------------------------------------------------------- 人物行为卡
class ChoiceRule(BaseModel):
    """选择规律：在什么压力下，选择什么，代价是什么。

    这是把「人物性格」从形容词变成可执行物件的落点。
    性格 = 在特定压力下愿意放弃什么。形容词不可生成、不可检查，选择表可以。
    """

    pressure: PressureType = Field(description="压力类型 P1 生死 / P2 利益 / P3 名誉 / P4 情感 / P5 时间 / P6 道义")
    trigger: str = Field(description="具体情境，必须是可写进剧本的场景级条件")
    choice: str = Field(description="他/她会怎么做")
    cost: str = Field(description="这个选择的代价。没有代价的选择不构成性格，只是技能展示")
    against_self_interest: bool = Field(
        default=False,
        description="是否违背自身利益。全剧至少 2 条为真 —— 这是『有魅力』的硬判据，"
                    "也是可计数、因此可校验的字段",
    )
    visible_as: str = Field(description="在音频里如何被听见：台词 / 动作音 / 沉默 / 被中断的话")


class BehaviorCard(BaseModel):
    """行为卡。与 CharacterCard 的分工：

      CharacterCard → 听觉辨识度（voice_anchor / speech_rate / sfx_signature）
      BehaviorCard  → 选择规律（压力 × 选择 + 代价）

    两者不可互相替代：一个角色可以声音极好认、行为却完全空洞。
    """

    slot: Slot
    name: str
    desire: str = Field(description="本集内可被听见的具体欲求，不是人生理想")
    fear: str = Field(description="最怕失去的那样东西。决定他在 P1/P4 压力下牺牲什么")
    misbelief: str = Field(description="错误信念。成长弧的引擎，没有它角色走完全剧也不会变")
    boundary: str = Field(description="绝对不会做的事。它让背叛与屈服变成可读事件")
    tell: str = Field(description="情绪破功时的听觉泄露。广播剧唯一的微表情通道")
    arc: str = Field(description="全剧变化方向，一句话")
    choices: list[ChoiceRule] = Field(default_factory=list)


class TurningPoint(BaseModel):
    """关系转折点。必须绑定具体事件 —— 无事件的转折无法兑现，在听众那里也不成立。"""

    episode: int
    event: str = Field(description="具体事件，例如『他替他挡了那一箭』")
    to: RelDirection
    evidence_hint: str = Field(
        default="", description="该转折在正文里应该长什么样 —— 供证据评审比对声明与内容"
    )


class RelationshipEdge(BaseModel):
    """关系边。变化必须可归因到事件，而不是『时间久了就信任了』。"""

    a: str
    b: str
    axis: RelAxis
    initial: RelDirection
    target: RelDirection
    turning_points: list[TurningPoint] = Field(default_factory=list)


class BehaviorBible(BaseModel):
    """人物选择规律底座。立项段生成，gate_behavior 通过后冻结。"""

    cards: list[BehaviorCard] = Field(description="每张有声角色卡一张行为卡")
    relations: list[RelationshipEdge] = Field(description="载体角色与其余有声角色的关系边")


# ---------------------------------------------------------------- 事实账本
class FactEntry(BaseModel):
    """一笔账目。

    账本存在的理由是广播剧的硬约束：**听众无法靠脸认人，也无法靠画面补信息。**
    因此『倒计时还有几天』『药还剩几支』『谁知道这件事』必须显式记账，
    而不是指望剧本作者在第 30 集时还记得第 2 集说过什么。
    """

    id: str
    kind: FactKind
    subject: str = Field(description="账目对象，例如「青霉素余量」「三日之约」")
    statement: str = Field(description="账目内容，一句话")
    established_at: int = Field(description="首次确立的集号")
    due_at: int | None = Field(default=None, description="倒计时类：到期集号，必须存在且晚于确立集")
    holders: list[str] = Field(default_factory=list, description="知道这件事的角色")
    forbidden_holders: list[str] = Field(
        default_factory=list, description="明确不知道这件事的角色（认知边界，与 holders 不得重叠）"
    )
    expected_mentions: list[int] = Field(
        default_factory=list, description="必须被触及的集号。第三幕起每集至少被一笔账覆盖"
    )
    remaining: int | None = Field(
        default=None,
        description="资源类账目的余量（剩余份数）。None 表示未量化 —— 无限的东西不能记账，"
                    "能记账的必然是有限的。资源漂移（resource_drift）只在余量为正时判定："
                    "余量为 0 时正文写『用完了』是账目的兑现，不是矛盾",
    )


class FactLedger(BaseModel):
    """事实账本。承接倒计时、资源、认知、承诺四类跨集一致性问题。"""

    entries: list[FactEntry] = Field(default_factory=list)
    note: str = ""


# ---------------------------------------------------------------- 大纲
class OutlineEntry(BaseModel):
    episode: int
    title: str
    act: Act
    core_goal: str
    conflict_intensity: int = Field(ge=1, le=10)
    polarity: Polarity
    loop_step: LoopStep | None = Field(default=None, description="该集落在降维循环的哪一步")
    side_effect_of: int | None = Field(
        default=None, description="K06：本集的难题源于第几集的成功"
    )
    deployment: str | None = Field(
        default=None, description="K18：本集投放的感官型资产用法，None 表示不投放"
    )
    gadget_failed: bool = Field(
        default=False, description="K09：本集是否安排金手指失效，用于证明主角本人的判断力"
    )
    quantified_gain: str | None = Field(default=None, description="K11：可量化成果")
    # ---- 人物与连续性层的声明（都是"声明"，正文是否兑现由证据评审复核）----
    exercised_choice: str | None = Field(
        default=None,
        description="本集演练的角色选择，格式「角色名@压力类型：选择」。"
                    "声明了就必须在正文里被听见 —— 这是行为卡落地为剧情的接口",
    )
    relation_turn: str | None = Field(
        default=None,
        description="本集发生的关系转折，格式「甲→乙@轴」。与行为卡 relations 的转折点对齐",
    )
    carried_facts: list[str] = Field(
        default_factory=list,
        description="本集承接的账目 id。第三幕起每集至少一笔（FC04）",
    )


# ---------------------------------------------------------------- 剧本
class Line(BaseModel):
    id: str
    kind: LineKind
    speaker: str | None = None
    text: str = ""
    start_sec: float
    end_sec: float
    sfx_category: AudioEvent | None = None
    event: BeatEvent | None = None
    emotion_peak: int = Field(default=0, ge=0, le=10)
    measured_sec: float | None = Field(
        default=None, description="TTS 实测时长，回填后用于二次校验"
    )


class Beat(BaseModel):
    segment: Literal["S1", "S2", "S3", "S4", "S5", "S6"]
    label: str
    start_sec: float
    end_sec: float
    lines: list[Line] = Field(default_factory=list)


class Episode(BaseModel):
    episode: int
    title: str
    duration_sec: int
    beats: list[Beat] = Field(default_factory=list)
    cliffhanger_hook_type: str | None = None
    threads: list[str] = Field(
        default_factory=list, description="R06：本集推进的线索，必须恰好 1 条"
    )
    # 校验产物（便于前端直接渲染）
    revision: int = 0


# ---------------------------------------------------------------- Phase 1.5: Episode Plan + Writer 拆分
class EpisodeBeatPlan(BaseModel):
    """单个节拍的计划——只描述'发生什么'，不写完整台词。"""
    order: int
    function: str = Field(description="hook / setup / conflict / reversal / cliffhanger / transition")
    event: str = Field(description="本拍发生的事件（一句话）")
    new_information: str = Field(default="", description="本拍释放了什么新信息")
    character_choice: str = Field(default="", description="角色在本拍做了什么选择，代价是什么")
    consequence: str = Field(default="", description="这个选择的直接后果")
    tension: int = Field(default=3, ge=1, le=10, description="张力评分 1-10")


class EpisodePlan(BaseModel):
    """STRONG 模型产出的轻量剧本计划。
    只负责'想清楚'——不生成完整台词。Writer 节点据此展开正文。
    """
    episode: int
    title: str
    goal: str = Field(description="本集一句话核心目标")
    dramatic_question: str = Field(description="本集的戏剧问题（观众想知道答案的问题）")
    hook: str = Field(description="开场钩子（怎么写能抓住注意力）")
    beats: list[EpisodeBeatPlan] = Field(default_factory=list, min_length=1)
    reversal: str = Field(default="", description="本集的反转点")
    climax: str = Field(default="", description="本集的高潮场景")
    cliffhanger: str = Field(default="", description="结尾悬念钩子")
    continuity_notes: str = Field(default="", description="需要在本集兑现的连续性事实")


# ---------------------------------------------------------------- 校验
class Finding(BaseModel):
    """一条校验结论。

    route 字段是这套设计的关键：违规不只是"报错"，它直接决定图上的下一条边走向哪个修复节点。
    """

    rule_id: str
    severity: Severity
    tier: int = 1
    location: str = ""
    message: str = ""
    repair_hint: str = ""
    route: str = "repair_beat"
    # ---- 证据层（Tier-2 专用）----
    # 语义判定永远可能误判。把正文原文一并带回，人工复核才只需几秒钟 ——
    # 这也是"基于正文证据"这条要求的落地形态。
    evidence: str = Field(default="", description="正文原文片段。引用不出原文时判据不成立")
    pattern: str = Field(
        default="", description="伪证模式分类（EV01–EV06 / label_only / fake_* / *_drift）"
    )


class ValidationReport(BaseModel):
    scope: str
    passed: bool
    findings: list[Finding] = Field(default_factory=list)
    tier1_count: int = 0
    tier2_count: int = 0
    llm_calls: int = Field(default=0, description="本道校验实际产生的模型调用次数")
    llm_calls_saved: int = Field(default=0, description="因 Tier-1 短路而省下的裁判调用")
    claims_checked: int = Field(default=0, description="本次送进证据评审的声明数")
    unclaimed: list[str] = Field(
        default_factory=list,
        description="正文中新出现、账本未登记的事实。只提取与告警，不自动改写账本",
    )

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    def by_route(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = {}
        for f in self.findings:
            out.setdefault(f.route, []).append(f)
        return out

    def by_pattern(self) -> dict[str, int]:
        """按伪证模式归类 —— 报告里"假反转/假信任/假因果/标签空转"的分布来源。"""
        out: dict[str, int] = {}
        for f in self.findings:
            if f.pattern:
                out[f.pattern] = out.get(f.pattern, 0) + 1
        return out


# ============================================================================
# LLM 输入输出的外壳模型
# complete_structured 只接受单个模型类型，列表产物必须有外壳；
# judge 节点需要逐条裁决的固定形状。
# ============================================================================


class TopicChoice(BaseModel):
    genre_id: str = Field(description="从允许列表中选择的赛道 ID，如 G01")
    recipe_id: str = Field(description="从允许列表中选择的配方 ID，如 R4")
    rationale: str = Field(description="为什么这个赛道适配音频形态，2 句以内")


class CastDraft(BaseModel):
    cards: list[CharacterCard] = Field(description="恰好 5 张角色卡，槽位 A–E 各一")


class OutlineDraft(BaseModel):
    entries: list[OutlineEntry] = Field(description="按集号升序的分集大纲")


class JudgeVerdict(BaseModel):
    """一条裁决。

    rule_id 与 claim_id 二选一：
      - 规则裁决（K06/K08/K16/R13）：填 rule_id
      - 声明兑现裁决（证据评审）：填 claim_id，并给出 fake_pattern 分类
    """

    rule_id: str = ""
    passed: bool
    reason: str = ""
    evidence: str = Field(default="", description="剧本或大纲中的原文片段，用于人工复核")
    claim_id: str = Field(default="", description="声明兑现裁决时的声明 id")
    severity: str = Field(default="", description="error | warning | info（Phase 4.5: batch judge 可直接输出 severity）")
    scope: str = Field(default="", description="batch judge 的裁决来源 scope：gadget / outline / episode-N")
    fake_pattern: str = Field(
        default="", description="伪证模式：label_only / fake_reversal / fake_trust / "
                                "fake_causality / countdown_drift / knowledge_leak / resource_drift"
    )


class JudgeResponse(BaseModel):
    verdicts: list[JudgeVerdict]
    unclaimed: list[str] = Field(
        default_factory=list,
        description="正文里出现、但账本未登记的新事实（新伏笔）。只提取与告警，不自动改写账本 —— "
                    "判断它是伏笔还是废笔需要人的判断",
    )


# ---------------------------------------------------------------- 证据评审
class Claim(BaseModel):
    """一条待兑现的声明。

    引擎把"声明层"（大纲字段 / 账本账目 / 关系转折 / 行为卡选择）展开成应然清单，
    再与"正文层"逐一比对 —— 两者之间的差，正是假反转、假信任、假因果与标签空转的定义。
    """

    claim_id: str = Field(description="形如 ep3.side_effect_of")
    episode: int
    rule_id: str = Field(description="语义规则编号，决定修复去向")
    label: str = Field(description="声明的中文名，例如「副作用声明」「可量化成果」")
    claim: str = Field(description="声明内容（人类可读）")
    expect: str = Field(default="", description="正文里应出现什么才算兑现")
    payload: dict = Field(default_factory=dict, description="静态预筛用的结构化材料")
    route: str = "repair_beat"


class ClaimCheck(BaseModel):
    claim_id: str
    passed: bool
    fake_pattern: str = "none"
    evidence: str = Field(default="", description="必须引用正文原文；取不出原文即判为不通过")
    reason: str = ""


class EvidenceReview(BaseModel):
    checks: list[ClaimCheck] = Field(default_factory=list)
    unclaimed: list[str] = Field(default_factory=list)


class LineRewrites(BaseModel):
    """定向改写结果。

    只回传被点名的那些行，而不是整集 —— 这既是成本决策，也是质量决策：
    整集重写会顺手改坏本来已经通过校验的部分。
    """

    lines: list[Line] = Field(description="改写后的台词，id 必须与输入一致")
    note: str = ""


# ============================================================================
# Phase 6: Prose Story 输出 Schema
# ============================================================================
class ProseParagraph(BaseModel):
    """散文故事段落。"""

    text: str = Field(description="段落文本")


class ProseStory(BaseModel):
    """散文故事输出。

    这是 prose_story 内容形式的最终产出。
    plain_text 由 paragraphs 自动计算，保证段落与纯文本永远一致。
    """

    title: str = Field(description="故事标题")
    paragraphs: list[ProseParagraph] = Field(default_factory=list, description="故事段落数组")

    @property
    def plain_text(self) -> str:
        """所有段落用 \\n\\n 拼接的纯文本。"""
        return "\n\n".join(p.text for p in self.paragraphs)


# ============================================================================
# Phase 6.3: Episode Render Result — fan-out → fan-in 统一契约
# ============================================================================
class EpisodeRenderResult(BaseModel):
    """每个 episode branch 返回的不可歧义的渲染结果。

    Audio 和 Prose 使用同一 Schema：
      - audio_drama → audio_episode 非 None, prose_story 为 None
      - prose_story → prose_story 非 None, audio_episode 为 None
    """

    episode_index: int = Field(description="集号（1-based）")
    content_form: str = Field(description="audio_drama | prose_story")

    # 共享 Plan（gen_beats 产出）
    plan: dict | None = Field(default=None, description="EpisodePlan as dict")

    # Content Form 产出（二选一）
    audio_episode: Episode | None = Field(default=None, description="广播剧 Episode")
    prose_story: ProseStory | None = Field(default=None, description="散文故事 ProseStory")

    # 校验结果
    validation_passed: bool = Field(default=False)
    validation_findings: list = Field(default_factory=list)

    # OutputGuard
    output_guard_passed: bool = Field(default=False)
    output_guard_reason: str = Field(default="")

    # 元数据
    writer: str | None = Field(default=None)
    writer_version: str | None = Field(default=None)
    renderer_version: str | None = Field(default=None)
