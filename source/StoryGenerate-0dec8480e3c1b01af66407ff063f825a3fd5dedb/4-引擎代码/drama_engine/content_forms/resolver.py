"""Phase 6: Content Form Resolver — 确定性，0 LLM。

Priority:
  1. explicit content_form → 精确匹配
  2. default = audio_drama
"""
from __future__ import annotations

from .models import ContentForm, ContentFormProfile
from .registry import get_content_form
from ..forms.registry import get_form_handler


def resolve_content_form(content_form: str | None = None) -> ContentFormProfile:
    """解析 Content Form。

    content_form=None → audio_drama（向后兼容）。
    content_form="unknown" → ContentFormNotSupportedError。
    """
    key = content_form or ContentForm.AUDIO_DRAMA.value
    # Resolve through the Phase 9 registry first so there is one authoritative
    # list of form keys.  The current graph adapter intentionally supports only
    # the two legacy renderers; other handlers remain available through
    # drama_engine.forms until their graph writers are integrated.
    get_form_handler(key)
    return get_content_form(key)


def content_form_enum(key: str | None) -> ContentForm:
    """将 string key 转为 ContentForm enum。"""
    key = key or ContentForm.AUDIO_DRAMA.value
    return ContentForm(key)
