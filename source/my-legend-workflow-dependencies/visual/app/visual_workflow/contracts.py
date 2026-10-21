"""子图输入与评审契约；播放器协议仍由现有 Dart 模型负责严格校验。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .script_models import VisualScript

class Request(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal['visual_request/1.0'] = 'visual_request/1.0'
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    target_profile: Literal['mobile_current_v2', 'web_basic_v1']
    # 自动生成模式将问题交给剧情编辑端，不把待确认当作批准。旧模式继续兼容。
    generation_mode: Literal['reviewed', 'automatic'] = 'reviewed'
    # 新模板只执行立绘平移；旧输入默认保持原策略，不改变历史版本的行为。
    presentation_profile: Literal['legacy_basic', 'standing_dialogue_v1'] = 'legacy_basic'
    deliverable: Literal['runtime_package', 'preview'] = 'runtime_package'
    document: dict | None = None
    script: VisualScript | None = None
    preview_skin: Literal['basic', 'fogport_v1', 'fogport_v2', 'fogport_painted_v1'] = 'basic'
    asset_pack_id: str = 'basic_pixel'
    asset_pack_version: str = '1.0.0'
    # 对通用外观的认可属于内容确认，不由系统猜测角色长相。
    context_confirmed: bool = False
    context_notes: str = ''
    parent_visual_version: str | None = None

    @model_validator(mode='after')
    def one_source(self):
        if (self.document is None) == (self.script is None):
            raise ValueError('document 与 script 必须提供其中一个')
        if self.preview_skin != 'basic' and self.target_profile != 'web_basic_v1':
            raise ValueError('生成角色素材目前只适配网页目标')
        return self

class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    review_id: str
    expected_plan_hash: str
    decision: Literal['approve', 'reject']
    notes: str = ''
    answers: dict | None = None
