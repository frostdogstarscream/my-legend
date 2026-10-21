"""统一剧本契约只描述故事与已选择的表现条件，不包含播放器代码和像素坐标。"""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')]
Text = Annotated[str, Field(min_length=1, max_length=500)]

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class Appearance(StrictModel):
    template_type: Literal['adult', 'elder', 'child', 'animal']
    color_key: Literal['blue', 'green', 'red', 'brown']
    accessory: Literal['none', 'hat', 'beard'] = 'none'

class Character(StrictModel):
    role_id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=20)]
    description: Annotated[str, Field(min_length=1, max_length=1000)]
    # 没选外观时保留缺项，由人工补充；不根据名字推断年龄、性别或形象。
    appearance: Appearance | None = None
    visual_requirements: list[Text] = Field(default_factory=list)

class SourceTiming(StrictModel):
    start_ms: Annotated[int, Field(ge=0)]
    end_ms: Annotated[int, Field(gt=0)]
    verified: bool
    basis: Literal['visual_estimate', 'provided_media', 'fixed_schedule']

    @model_validator(mode='after')
    def interval(self):
        if self.end_ms <= self.start_ms:
            raise ValueError('结束时间必须大于开始时间')
        return self

class Event(StrictModel):
    block_id: Identifier
    type: Literal['dialogue', 'narration', 'action', 'pause']
    role_id: Identifier | None = None
    participant_role_ids: list[Identifier] = Field(default_factory=list)
    target_role_id: Identifier | None = None
    text: Text | None = None
    # 保留上游的未知动作标签，交由目标能力校验报告 unsupported，而非静默改写。
    action: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    target_anchor: Literal['slot_1','slot_2','slot_3','slot_4','entry','exit'] | None = None
    direction: Literal['down','left','right','up'] | None = None
    emotion: Literal['neutral','happy','sad','angry','surprised','serious'] | None = None
    intensity: Annotated[float, Field(ge=0, le=1)] | None = None
    duration_ms: Annotated[int, Field(ge=100, le=90000)] | None = None
    source_ref: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    timing: SourceTiming | None = None

    @model_validator(mode='after')
    def shape(self):
        # 不同事件类型严格区分字段，避免把动作提示词当成字幕或遗漏对象。
        if self.type in ('dialogue', 'narration') and (self.text is None or not self.text.strip()):
            raise ValueError('对白和旁白必须提供非空原文')
        if self.type == 'dialogue' and self.role_id is None:
            raise ValueError('对白必须指定说话人')
        if self.type == 'action':
            if (self.role_id is None) == (not self.participant_role_ids):
                raise ValueError('行动必须指定单人或多人施动者，二选一')
            if self.action is None or self.text is None or not self.text.strip():
                raise ValueError('行动必须提供动作标签与原始说明')
            if self.action in ('attack','cast','combo') and self.target_role_id is None:
                raise ValueError('该行动必须明确对象')
        if self.type != 'action' and any(x is not None for x in (self.action,self.target_anchor,self.direction)):
            raise ValueError('仅行动可以提供动作、站位和方向')
        if self.type != 'action' and self.participant_role_ids:
            raise ValueError('仅行动可以提供多人施动者')
        if self.type in ('narration','pause') and any(x is not None for x in (self.role_id,self.target_role_id,self.emotion,self.intensity)):
            raise ValueError('旁白和停顿不能携带人物或表情参数')
        if self.type == 'pause' and self.text is not None:
            raise ValueError('停顿不能包含字幕')
        if self.type == 'action':
            if self.action == 'move' and self.target_anchor is None:
                raise ValueError('移动必须选择站位')
            if self.action != 'move' and self.target_anchor is not None:
                raise ValueError('只有移动支持目标站位')
            if self.action == 'face' and (self.direction is None) == (self.target_role_id is None):
                raise ValueError('转向需要目标人物或方向，二选一')
            if self.action != 'face' and self.direction is not None:
                raise ValueError('只有转向支持方向参数')
            if self.action not in ('react',) and any(x is not None for x in (self.emotion,self.intensity)):
                raise ValueError('当前动作不支持情绪或强度')
        return self

class Scene(StrictModel):
    scene_id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=40)]
    description: Annotated[str, Field(min_length=1, max_length=1000)]
    background_type: Literal['indoor','courtyard','outdoor']
    cast: list[Identifier] = Field(min_length=1, max_length=4)
    initial_cast: list[Identifier] | None = None
    blocks: list[Event] = Field(min_length=1, max_length=100)
    result: Annotated[str, Field(min_length=1, max_length=1000)] | None = None

class Constraints(StrictModel):
    preserve_dialogue: Literal[True] = True
    locked_facts: list[Text]
    allow_new_plot: Literal[False] = False
    # 当前产品范围：基础动作以外默认用原行动说明显示旁白；显式 False 仍拒绝降级。
    allow_narration_fallback: bool = True
    allow_visual_simplification: bool = False

class VisualScript(StrictModel):
    schema_version: Literal['visual_script/1.0']
    document_id: Identifier
    source_version_id: Identifier
    revision: Annotated[int, Field(ge=1)] = 1
    title: Annotated[str, Field(min_length=1, max_length=80)]
    source_text: str | None = None
    excerpt: bool = False
    continuity_confirmed: bool = False
    target_duration_ms: Annotated[int, Field(ge=1, le=90000)] | None = None
    constraints: Constraints
    characters: list[Character] = Field(min_length=1, max_length=6)
    scenes: list[Scene] = Field(min_length=1, max_length=2)

    @model_validator(mode='after')
    def references(self):
        def unique(values, name):
            if len(values) != len(set(values)):
                raise ValueError(f'{name}不能重复')
        roles = [a.role_id for a in self.characters]
        unique(roles, '人物ID'); unique([s.scene_id for s in self.scenes], '场景ID')
        blocks = []
        for scene in self.scenes:
            unique(scene.cast, '本场人物'); unique(scene.initial_cast or [], '开场人物')
            if not set(scene.cast) <= set(roles):
                raise ValueError(f'{scene.scene_id}引用不存在的人物')
            if scene.initial_cast is not None and not set(scene.initial_cast) <= set(scene.cast):
                raise ValueError(f'{scene.scene_id}开场人物不在本场名单内')
            previous_end = None
            for block in scene.blocks:
                blocks.append(block.block_id)
                unique(block.participant_role_ids, '多人施动者')
                refs = block.participant_role_ids + [r for r in (block.role_id,block.target_role_id) if r]
                if not set(refs) <= set(scene.cast):
                    raise ValueError(f'{block.block_id}引用不在本场的人物')
                if block.timing:
                    if previous_end is not None and block.timing.start_ms < previous_end:
                        raise ValueError(f'{block.block_id}源时间与上一事件重叠或倒序')
                    previous_end = block.timing.end_ms
        unique(blocks, '事件ID')
        if len(blocks) > 100:
            raise ValueError('全剧最多100条事件')
        return self
