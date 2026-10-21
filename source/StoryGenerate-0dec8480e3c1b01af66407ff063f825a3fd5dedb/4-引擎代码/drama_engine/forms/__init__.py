"""Phase 9: Content Form Handlers — individual modules for registry import."""
from .handlers import (
    AudioDramaHandler,
    ProseStoryHandler,
    NovelHandler,
    StorytellingHandler,
    StandupHandler,
    CrosstalkHandler,
    NovelValidator,
    StorytellingValidator,
    StandupValidator,
    CrosstalkValidator,
)
from .registry import (
    ContentForm,
    ContentFormRegistry,
    get_form_handler,
    register_form_handler,
    available_forms,
)

__all__ = [
    "AudioDramaHandler", "ProseStoryHandler",
    "NovelHandler", "StorytellingHandler", "StandupHandler", "CrosstalkHandler",
    "NovelValidator", "StorytellingValidator", "StandupValidator", "CrosstalkValidator",
    "ContentForm", "ContentFormRegistry",
    "get_form_handler", "register_form_handler", "available_forms",
]