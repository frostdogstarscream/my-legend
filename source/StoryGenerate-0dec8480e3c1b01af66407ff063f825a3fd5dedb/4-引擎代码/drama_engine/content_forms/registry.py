"""Phase 6: Content Form Registry。

单例注册中心：注册、获取、列举 Content Form。
"""
from __future__ import annotations

from .models import AUDIO_DRAMA_PROFILE, PROSE_STORY_PROFILE, ContentFormProfile
from ..core.errors import EngineErrorCode, StoryEngineError


class ContentFormNotSupportedError(StoryEngineError):
    def __init__(self, form_key: str) -> None:
        available = ", ".join(_available) if _available else "audio_drama, prose_story"
        super().__init__(
            code=EngineErrorCode.CONTENT_FORM_NOT_SUPPORTED,
            message=f"内容形式 '{form_key}' 不支持。可用: {available}",
        )


# 全局注册表
_registry: dict[str, ContentFormProfile] = {}
_available: list[str] = []


def register_content_form(profile: ContentFormProfile) -> None:
    _registry[profile.key] = profile
    _available.append(profile.key)


def get_content_form(key: str | None) -> ContentFormProfile:
    key = key or "audio_drama"
    profile = _registry.get(key)
    if profile is None:
        raise ContentFormNotSupportedError(key)
    return profile


def available_content_forms() -> list[str]:
    return sorted(_available)


def _bootstrap() -> None:
    """注册内建 Content Form。"""
    if not _registry:
        register_content_form(AUDIO_DRAMA_PROFILE)
        register_content_form(PROSE_STORY_PROFILE)


_bootstrap()