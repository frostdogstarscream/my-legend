"""Phase 9: Content Form Registry.

Each Content Form defines:
  - How the final story is written (Writer)
  - How it's validated (Validator)
  - What rule packs apply
  - Package projection

Principle: Graph calls registry.resolve(content_form) → handler.writer(), NOT
a hardcoded if/elif for every form.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Protocol, runtime_checkable

from ..core.errors import EngineErrorCode, StoryEngineError


# ═══════════════════════════════════════════════════════════════════
# ContentForm enum
# ═══════════════════════════════════════════════════════════════════
class ContentForm(str, Enum):
    AUDIO_DRAMA = "audio_drama"
    PROSE_STORY = "prose_story"
    NOVEL = "novel"                    # Phase 9
    STORYTELLING = "storytelling"      # Phase 9
    STANDUP = "standup"                # Phase 9
    CROSSTALK = "crosstalk"            # Phase 9


# ═══════════════════════════════════════════════════════════════════
# Content Form Handler Protocol
# ═══════════════════════════════════════════════════════════════════
@runtime_checkable
class ContentFormHandler(Protocol):
    """Minimal interface for a Content Form implementation."""

    @property
    def key(self) -> str: ...

    def writer_prompt(self, plan: dict, characters: list, mode_key: str) -> str:
        """Return the writer prompt for this form."""
        ...

    def validators(self) -> list[Any]:
        """Return validators for this form."""
        ...

    def rule_packs(self) -> list[str]:
        """Return rule pack names for this form."""
        ...

    def package(self, raw_output: str, plan: dict) -> dict:
        """Package the raw LLM output into the result dict."""
        ...


# ═══════════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════════
class ContentFormRegistry:
    """Global Content Form registry.

    Singleton. Use get_form() / register_form() / available_forms().
    """

    _instance: ContentFormRegistry | None = None

    def __init__(self):
        if ContentFormRegistry._instance is not None:
            raise RuntimeError("ContentFormRegistry is singleton")
        self._forms: dict[str, ContentFormHandler] = {}
        ContentFormRegistry._instance = self
        self._bootstrap()

    def _bootstrap(self):
        from .audio_drama import AudioDramaHandler
        from .prose_story import ProseStoryHandler
        from .novel import NovelHandler
        from .storytelling import StorytellingHandler
        from .standup import StandupHandler
        from .crosstalk import CrosstalkHandler
        self.register(AudioDramaHandler())
        self.register(ProseStoryHandler())
        self.register(NovelHandler())
        self.register(StorytellingHandler())
        self.register(StandupHandler())
        self.register(CrosstalkHandler())

    def register(self, handler: ContentFormHandler):
        self._forms[handler.key] = handler

    def get(self, key: str) -> ContentFormHandler:
        handler = self._forms.get(key)
        if handler is None:
            available = ", ".join(sorted(self._forms.keys()))
            raise StoryEngineError(
                code=EngineErrorCode.CONTENT_FORM_NOT_SUPPORTED,
                message=f"Content form '{key}' not supported. Available: {available}",
            )
        return handler

    def available_forms(self) -> list[str]:
        return sorted(self._forms.keys())

    def is_registered(self, key: str) -> bool:
        return key in self._forms


# ═══════════════════════════════════════════════════════════════════
# Convenience
# ═══════════════════════════════════════════════════════════════════
_instance: ContentFormRegistry | None = None


def _get_registry() -> ContentFormRegistry:
    global _instance
    if _instance is None:
        _instance = ContentFormRegistry()
    return _instance


def get_form_handler(key: str) -> ContentFormHandler:
    return _get_registry().get(key)


def register_form_handler(handler: ContentFormHandler) -> None:
    _get_registry().register(handler)


def available_forms() -> list[str]:
    return _get_registry().available_forms()