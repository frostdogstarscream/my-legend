"""Phase 3: Character Canon + Runtime 系统。"""
from .models import (
    CharacterAppearance,
    CharacterCanon,
    CharacterInput,
    CharacterRuntimeInput,
    ResolvedCharacter,
)
from .resolver import (
    CharacterResolver,
    CharacterCountExceededError,
    CharacterRoleIdConflictError,
    CharacterIdConflictError,
    CharacterInvalidError,
)
from .overlay import (
    overlay_canon,
    overlay_runtime,
    validate_character_canon_preserved,
    reconcile_cast,
)

__all__ = [
    # Models
    "CharacterAppearance",
    "CharacterCanon",
    "CharacterInput",
    "CharacterRuntimeInput",
    "ResolvedCharacter",
    # Resolver
    "CharacterResolver",
    "CharacterCountExceededError",
    "CharacterRoleIdConflictError",
    "CharacterIdConflictError",
    "CharacterInvalidError",
    # Overlay
    "overlay_canon",
    "overlay_runtime",
    "validate_character_canon_preserved",
    "reconcile_cast",
]