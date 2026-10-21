"""Mode domain model: Protocol, Profile, Capability。

原则：
- Capability 用枚举，禁止散落字符串。
- StoryMode 是 Protocol，不是 ABC —— 不需要强继承。
- ModeProfile 是纯数据描述，不是配置引擎。
"""
from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


# ============================================================================
# Capability —— 使用枚举禁止散落字符串
# ============================================================================
class Capability(str, Enum):
    """故事生成引擎的能力标记。

    每个 Mode 声明自己支持哪些能力。
    当前只有 viral_drama 模式声明了全部能力。
    未来 General / Mystery 可能只声明子集。
    """
    GADGET = "gadget"                       # 金手指 / 特殊能力设计
    FIVE_SLOT_CAST = "five_slot_cast"       # 五槽位角色配置
    BEHAVIOR = "behavior"                   # Behavior Card（人物选择规律）
    FACT_LEDGER = "fact_ledger"             # 事实账本
    STRONG_HOOK = "strong_hook"             # 强 Hook 开场
    EPISODE_PLAN = "episode_plan"           # STRONG 模型规划剧本结构
    EPISODE_WRITER = "episode_writer"       # BALANCED 模型写正文
    EVIDENCE_JUDGE = "evidence_judge"       # 证据级语义裁判
    AUDIO_CONSTRAINTS = "audio_constraints" # 广播剧听觉约束
    SERIES_VALIDATION = "series_validation" # 跨集连续性校验


# ============================================================================
# ModeProfile —— 纯数据描述
# ============================================================================
class ModeProfile(BaseModel):
    """一个 Mode 的公开档案。

    Profile 不包含实现逻辑。它是给 Registry / Router / Graph
    用来在不导入 Mode 实现类的情况下做能力查询的最小数据契约。
    """
    key: str = Field(description="模式唯一标识，如 'viral_drama'")
    version: str = Field(default="1.0", description="该模式的语义版本")
    label: str = Field(default="", description="人类可读的名称")
    description: str = Field(default="", description="一句话描述")

    capabilities: set[Capability] = Field(default_factory=set)

    rule_packs: list[str] = Field(
        default_factory=list,
        description="该模式需要的规则包清单，如 ['drama_formula', 'timetravel', 'character_continuity']"
    )

    default_content_form: str | None = Field(
        default=None,
        description="默认的内容形态，如 'audio_drama'"
    )

    metadata: dict = Field(default_factory=dict, description="扩展元数据")


# ============================================================================
# ModeContext —— State 中的 Mode 信息载体
# ============================================================================
class ModeContext(BaseModel):
    """图状态中的 Mode 上下文。

    ModeContext 是 Profile 的"运行时快照"：
    - 它在 intake 阶段写入 State
    - 所有节点通过它判断当前 Mode 具备哪些能力
    - 不会在生成过程中被修改
    """
    key: str = Field(default="viral_drama")
    version: str = Field(default="1.0")
    capabilities: set[Capability] = Field(default_factory=set)
    config: dict = Field(default_factory=dict)

    def supports(self, capability: str | Capability) -> bool:
        if isinstance(capability, str):
            try:
                capability = Capability(capability)
            except ValueError:
                return False
        return capability in self.capabilities

    @classmethod
    def from_mode(cls, mode: "StoryMode") -> "ModeContext":
        profile = mode.profile()
        return cls(
            key=profile.key,
            version=profile.version,
            capabilities=profile.capabilities,
        )


# ============================================================================
# StoryMode Protocol
# ============================================================================
@runtime_checkable
class StoryMode(Protocol):
    """Mode 的最小接口协议。

    使用 Protocol 而非 ABC:
    - 不要求强继承
    - 允许 future modes 以灵活方式实现
    - Duck-typing: 只要类有 key / profile / validate_request 就算 Mode

    所有 Mode 必须实现：
      key    → 返回 str 标识
      profile() → 返回 ModeProfile
      validate_request(brief) → None 或 raise StoryEngineError
    """

    @property
    def key(self) -> str: ...

    def profile(self) -> ModeProfile: ...

    def validate_request(self, brief: object) -> None:
        """校验用户请求是否兼容本 Mode。

        如果不兼容应 raise StoryEngineError（如 MODE_NOT_SUPPORTED）。
        不返回 None（即校验通过）。
        """
        ...