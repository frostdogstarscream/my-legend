"""Phase 5: Story Template data models.

Template 是数据，不是 Graph Node。
新增 Template 不应修改 Graph / Node / Prompt 代码。
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ============================================================================
# Template Source
# ============================================================================
class TemplateSource(str, Enum):
    explicit = "explicit"       # 用户显式指定 template_id
    mode_default = "mode_default"  # Mode 的默认模板
    freeform = "freeform"       # 无模板，自由结构


# ============================================================================
# Template Beat
# ============================================================================
class TemplateBeat(BaseModel):
    """模板中的一个叙事节拍。

    例如 Opening / Complication / Climax / Resolution。
    """
    key: str = Field(description="节拍唯一标识, 如 'opening'")
    purpose: str = Field(description="该节拍的叙事目的")
    guidance: str | None = Field(default=None, description="给 LLM 的引导")
    required: bool = Field(default=True, description="是否必须包含")


# ============================================================================
# Story Template Spec (Repository Entity)
# ============================================================================
class StoryTemplateSpec(BaseModel):
    """模板仓库中的完整模板定义。

    同时作为 ORM entity 和 domain model。
    """
    template_id: str = Field(description="模板唯一标识, 如 'GENERAL_THREE_ACT'")
    version: int = Field(default=1, ge=1, description="版本号")
    name: str = Field(default="", description="人类可读名称")

    supported_modes: list[str] = Field(
        default_factory=list,
        description="适用模式列表, 如 ['general']"
    )

    beats: list[TemplateBeat] = Field(
        default_factory=list,
        description="叙事节拍序列"
    )

    constraints: list[str] = Field(
        default_factory=list,
        description="模板专属约束, 如 'ending 必须是闭合的'"
    )

    recommended_characters: int | None = Field(
        default=None,
        description="建议角色数量"
    )

    tone_hints: list[str] = Field(
        default_factory=list,
        description="基调提示"
    )

    defaults: dict[str, Any] = Field(
        default_factory=dict,
        description="默认值 (e.g. target_episodes)"
    )

    ending_guidance: str | None = Field(
        default=None,
        description="结局方向引导, 如 'closed' / 'open'"
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="扩展元数据（不含 DB timestamps）"
    )


# ============================================================================
# Resolved Story Template (Runtime Snapshot)
# ============================================================================
class ResolvedStoryTemplate(BaseModel):
    """解析后的模板运行时快照。

    这是进入 State 的 snapshot。不与 ORM 对象耦合。
    """
    template_id: str
    version: int
    name: str
    source: TemplateSource

    supported_modes: list[str] = Field(default_factory=list)

    beats: list[TemplateBeat] = Field(default_factory=list)

    constraints: list[str] = Field(default_factory=list)

    recommended_characters: int | None = None

    tone_hints: list[str] = Field(default_factory=list)

    ending_guidance: str | None = None

    def beat_keys(self) -> list[str]:
        return [b.key for b in self.beats]

    def beat_summary(self) -> str:
        """压缩为 LLM 友好的结构摘要（只传节拍 key + purpose，不传 metadata）。"""
        items = [f"{b.key}: {b.purpose}" for b in self.beats]
        return "\n".join(items)

    def outline_context(self) -> str:
        """Outline 节点专用的压缩上下文。"""
        parts = [f"Template: {self.name}"]
        parts.append("叙事节拍:")
        for b in self.beats:
            req = "必须" if b.required else "可选"
            g = f" — {b.guidance}" if b.guidance else ""
            parts.append(f"  [{req}] {b.key}: {b.purpose}{g}")
        if self.constraints:
            parts.append("约束:")
            for c in self.constraints:
                parts.append(f"  - {c}")
        if self.tone_hints:
            parts.append(f"基调: {', '.join(self.tone_hints)}")
        if self.ending_guidance:
            parts.append(f"结局方向: {self.ending_guidance}")
        return "\n".join(parts)

    defaults: dict[str, Any] = Field(
        default_factory=dict,
        description="默认值快照 (e.g. default_recipe)"
    )

    @classmethod
    def from_spec(cls, spec: StoryTemplateSpec, source: TemplateSource) -> "ResolvedStoryTemplate":
        return cls(
            template_id=spec.template_id,
            version=spec.version,
            name=spec.name,
            source=source,
            supported_modes=spec.supported_modes,
            beats=spec.beats,
            constraints=spec.constraints,
            recommended_characters=spec.recommended_characters,
            tone_hints=spec.tone_hints,
            ending_guidance=spec.ending_guidance,
            defaults=spec.defaults,
        )