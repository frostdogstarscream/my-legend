"""Phase 6: Content Form data models.

Content Form 决定故事最终怎样表达（音频剧本 vs 散文故事）。
与 Mode（怎样构思）和 Template（怎样组织）完全解耦。
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ContentForm(str, Enum):
    """内容表达形式枚举。"""

    AUDIO_DRAMA = "audio_drama"
    PROSE_STORY = "prose_story"


class ContentFormProfile(BaseModel):
    """Content Form 的静态档案。

    每个 Content Form 声明：
      - key: 唯一标识
      - writer: 对应的 Writer 节点名称
      - validators: 需要运行的校验器列表
      - supports_audio_events: 是否支持音频事件
      - supports_dialogue_labels: 是否使用角色标签（如 林然：）
      - supports_narrative_paragraphs: 是否使用叙述段落
    """

    key: str = Field(description="唯一标识, 'audio_drama' | 'prose_story'")
    version: str = Field(default="1.0")

    label: str = Field(default="")

    writer: str = Field(description="Writer node name")
    validators: tuple[str, ...] = Field(default_factory=tuple)

    supports_audio_events: bool = False
    supports_dialogue_labels: bool = False
    supports_narrative_paragraphs: bool = False

    target_length_type: str = Field(
        default="duration_sec",
        description="长度度量单位: 'duration_sec' | 'chars' | 'words'",
    )

    metadata: dict = Field(default_factory=dict)


# ============================================================================
# Builtin Profiles
# ============================================================================
AUDIO_DRAMA_PROFILE = ContentFormProfile(
    key="audio_drama",
    version="1.0",
    label="广播剧",
    writer="episode_writer",
    validators=("audio_structure", "speaker_labels", "audio_events"),
    supports_audio_events=True,
    supports_dialogue_labels=True,
    supports_narrative_paragraphs=False,
    target_length_type="duration_sec",
)

PROSE_STORY_PROFILE = ContentFormProfile(
    key="prose_story",
    version="1.0",
    label="散文故事",
    writer="prose_writer",
    validators=("prose_structure", "prose_content"),
    supports_audio_events=False,
    supports_dialogue_labels=False,
    supports_narrative_paragraphs=True,
    target_length_type="chars",
    metadata={
        "min_chars": 500,
        "target_chars": 2000,
        "max_chars": 5000,
        "min_paragraphs": 5,
    },
)