"""Phase 3: Character Canon + Runtime 角色半固定机制 —— Domain Schemas。

核心两层模型：
  CharacterCanon      —— 角色“是谁”（固定身份，字段级锁定）
  CharacterRuntimeInput —— 本次故事“怎么表现”（动态演绎，不固化为 Canon）

ResolvedCharacter 是内部统一快照，同时持有 Canon + Runtime + 来源 Trace。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ============================================================================
# CharacterAppearance
# ============================================================================
class CharacterAppearance(BaseModel):
    """不要拆成几十个视觉字段——保持简单。"""
    description: str | None = None
    signature_elements: list[str] = Field(default_factory=list)


# ============================================================================
# CharacterCanon —— 固定角色身份
# ============================================================================
class CharacterCanon(BaseModel):
    """角色 Canon：提供就锁定，没提供就不假装知道。

    唯一强制字段：name。
    其他字段 non-null → 属于 canon_locked_fields。
    """

    character_id: str | None = Field(default=None, description="长期角色ID（opaque）")

    name: str = Field(description="角色名（强制）")
    gender: str | None = None
    age: int | None = None
    age_band: str | None = None

    species: str | None = None
    character_type: str | None = None

    identity: str | None = None
    world_id: str | None = None

    appearance: CharacterAppearance | None = None

    abilities: list[str] = Field(default_factory=list)
    immutable_facts: list[str] = Field(default_factory=list)

    def locked_fields(self) -> list[str]:
        """返回非 null 的 Canon 字段路径列表。"""
        locked: list[str] = []
        # name is always locked
        locked.append("name")
        for field_name in type(self).model_fields:
            if field_name in ("name", "character_id"):
                continue
            value = getattr(self, field_name)
            if value is None:
                continue
            if field_name == "appearance" and isinstance(value, CharacterAppearance):
                if value.description is not None:
                    locked.append("appearance.description")
                if value.signature_elements:
                    locked.append("appearance.signature_elements")
            elif field_name in ("abilities", "immutable_facts"):
                if value:
                    locked.append(field_name)
            else:
                if value:
                    locked.append(field_name)
        return locked

    def summary(self) -> str:
        """紧凑人类可读摘要（用于 Prompt 注入）。"""
        parts = [f"【{self.name}】"]
        if self.gender:
            parts.append(f"性别：{self.gender}")
        if self.identity:
            parts.append(f"身份：{self.identity}")
        if self.appearance and self.appearance.description:
            parts.append(f"外貌：{self.appearance.description}")
        if self.abilities:
            parts.append(f"能力：{'、'.join(self.abilities)}")
        if self.immutable_facts:
            parts.append(f"固定事实：{'、'.join(self.immutable_facts)}")
        return " | ".join(parts)


# ============================================================================
# CharacterRuntimeInput —— 本次故事动态演绎
# ============================================================================
class CharacterRuntimeInput(BaseModel):
    """本次 Story 的动态角色参数。

    用户提供 → 本次故事必须保留（runtime_override_fields）。
    用户未提供 → behavior_design 可自由生成。

    下次 Story 不自动继承 —— 这就是“半固定”的核心。
    """

    personality: list[str] | None = Field(default=None, description="性格标签")
    goal: str | None = Field(default=None, description="当前目标/欲望")
    motivation: str | None = Field(default=None, description="行为动机")
    fear: str | None = Field(default=None, description="最怕失去的事物")
    stance: str | None = Field(default=None, description="对核心冲突的立场")
    dialogue_style: str | None = Field(default=None, description="说话风格")
    story_function: str | None = Field(default=None, description="叙事功能角色")

    def override_fields(self) -> list[str]:
        """返回用户显式提供的 Runtime 字段列表。"""
        overrides: list[str] = []
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            if value is not None:
                overrides.append(field_name)
        return overrides

    def summary(self) -> str:
        """紧凑人类可读摘要（用于 Prompt）。"""
        parts = []
        if self.goal:
            parts.append(f"目标：{self.goal}")
        if self.motivation:
            parts.append(f"动机：{self.motivation}")
        if self.fear:
            parts.append(f"恐惧：{self.fear}")
        if self.stance:
            parts.append(f"立场：{self.stance}")
        if self.personality:
            parts.append(f"性格：{'、'.join(self.personality)}")
        if self.dialogue_style:
            parts.append(f"台词风格：{self.dialogue_style}")
        if self.story_function:
            parts.append(f"故事功能：{self.story_function}")
        return " | ".join(parts) if parts else "（本次无特殊 Runtime 约束）"


# ============================================================================
# CharacterInput —— 用户/API 输入
# ============================================================================
class CharacterInput(BaseModel):
    """用户提供的角色输入。

    role_id：本次 Story 内的角色标识（稳定）。
    character_id：长期角色 ID（opaque, 将来查库用）。
    canon：必须提供，至少需要 name。
    runtime：可选本次动态 override。
    """
    role_id: str | None = None
    character_id: str | None = None

    canon: CharacterCanon
    runtime: CharacterRuntimeInput | None = None


# ============================================================================
# ResolvedCharacter —— 内部统一快照
# ============================================================================
class ResolvedCharacter(BaseModel):
    """CharacterResolver 的输出。

    每个进入下游的角色都附带：
      - canon：最终 Canon（用户原值 + overlay 纠偏）
      - runtime：最终 Runtime（LLM 生成 + 用户 override 覆盖）
      - source：来源标记
      - canon_locked_fields：哪些 Canon 字段不可改
      - runtime_override_fields：哪些 Runtime 字段来自用户
    """

    role_id: str
    character_id: str | None = None

    canon: CharacterCanon
    runtime: CharacterRuntimeInput

    source: str = Field(default="generated", description="user | generated")

    canon_locked_fields: list[str] = Field(default_factory=list)
    runtime_override_fields: list[str] = Field(default_factory=list)

    # Phase 8: Selected Character Memory
    selected_memories: list[str] = Field(default_factory=list, description="Selected memory IDs")
    memory_contexts: list[str] = Field(default_factory=list, description="Memory summaries for Prompt")

    # 如果 Canon overlay 发生冲突
    canon_conflicts: list[dict] = Field(default_factory=list)

    @property
    def name(self) -> str:
        return self.canon.name