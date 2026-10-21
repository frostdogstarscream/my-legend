"""Phase 5: Builtin default templates for General Mode.

仅用于 bootstrap。用户新增 Template 应通过 Repository，不修改此文件。
"""
from __future__ import annotations

from .models import StoryTemplateSpec, TemplateBeat


# ============================================================================
# GENERAL_THREE_ACT — 通用三幕结构
# ============================================================================
GENERAL_THREE_ACT = StoryTemplateSpec(
    template_id="GENERAL_THREE_ACT",
    version=1,
    name="三幕结构",
    supported_modes=["general"],
    beats=[
        TemplateBeat(
            key="opening",
            purpose="建立场景、核心人物、日常状态和隐含的问题",
            guidance="通过具体的动作或对话引入，不要叙述性旁白开场",
        ),
        TemplateBeat(
            key="complication",
            purpose="引入打破日常的事件，呈现冲突的起点",
            guidance="事件必须是人物可感知、可反应的，不是抽象概念",
        ),
        TemplateBeat(
            key="escalation",
            purpose="冲突升级，角色必须做出一个艰难的选择",
            guidance="选择的结果应直接改变局面，不能只是情绪波动",
        ),
        TemplateBeat(
            key="turning_point",
            purpose="剧情转折点：新信息或行动让局势不可逆转",
            guidance="转折应来自角色之前的行动结果，不是外部巧合",
        ),
        TemplateBeat(
            key="climax",
            purpose="情感与行动的高潮：角色面对最大障碍",
            guidance="高潮必须有明确的行动、对白和情感释放",
        ),
        TemplateBeat(
            key="resolution",
            purpose="结局：展示改变后的状态，为故事收束",
            guidance="不要过度解释，让听众自己感受变化",
        ),
    ],
    constraints=[
        "剧情推进必须以人物行动为驱动，不能依赖旁白解释",
        "每个节拍之间必须有因果链：前一个节拍的事件导致后一个节拍的发生",
    ],
    recommended_characters=4,
    tone_hints=["自然", "真实", "克制"],
    ending_guidance="closed",
    defaults={"target_duration_sec": 180},
)


# ============================================================================
# GENERAL_RELATIONSHIP_TURN — 关系转折结构
# ============================================================================
GENERAL_RELATIONSHIP_TURN = StoryTemplateSpec(
    template_id="GENERAL_RELATIONSHIP_TURN",
    version=1,
    name="关系转折结构",
    supported_modes=["general"],
    beats=[
        TemplateBeat(
            key="setup",
            purpose="展示两个人物的关系现状及其隐含矛盾",
            guidance="通过互动细节展示关系，不只靠台词说明",
        ),
        TemplateBeat(
            key="trigger",
            purpose="触发事件：迫使其中一方或双方重新审视关系",
            guidance="触发事件应与之前的隐含矛盾直接相关",
        ),
        TemplateBeat(
            key="separation",
            purpose="分离或冲突阶段：双方采取对立的行动",
            guidance="双方各自的选择必须符合其行为规律",
        ),
        TemplateBeat(
            key="recognition",
            purpose="认知转变：其中一方意识到新的真相",
            guidance="转变应来自具体事件或他人的揭示，不是突然顿悟",
        ),
        TemplateBeat(
            key="reunion",
            purpose="重逢或和解：双方在新理解的基础上重新接触",
            guidance="和解不需要完美，保留真实的不完美感",
        ),
        TemplateBeat(
            key="new_normal",
            purpose="新常态：展示关系的新平衡状态",
            guidance="用具体细节暗示未来，不要书面总结",
        ),
    ],
    constraints=[
        "关系变化必须来自角色的主动选择",
        "不能使用'从此幸福地生活在一起'式收尾",
    ],
    recommended_characters=3,
    tone_hints=["细腻", "情感", "真实"],
    ending_guidance="closed_with_hope",
    defaults={"target_duration_sec": 180, "default_recipe": "R5"},
)


# ============================================================================
# Registry
# ============================================================================
BUILTIN_TEMPLATES: list[StoryTemplateSpec] = [
    GENERAL_THREE_ACT,
    GENERAL_RELATIONSHIP_TURN,
]