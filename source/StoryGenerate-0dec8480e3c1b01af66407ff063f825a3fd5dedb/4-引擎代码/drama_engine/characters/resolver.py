"""Phase 3: Character Resolver（纯代码，不调用 LLM）。

职责：
  1. 校验输入角色的合法性（ID 冲突、字段完整性等）
  2. 分配 role_id
  3. 提取 Canon locked fields / Runtime override fields
  4. 检查角色数量约束
  5. 计算需要生成的缺失角色数量
  6. 产出 ResolvedCharacter 快照

它不负责：
  - 调用 LLM（cast_design/behavior_design 继续负责）
  - CANON_CONFLICT 的 overlay 修复（overlay.py 负责）
  - 最后 validator（validators.py 负责）
"""
from __future__ import annotations

from .models import (
    CharacterCanon,
    CharacterInput,
    CharacterRuntimeInput,
    ResolvedCharacter,
)
from ..core.errors import EngineErrorCode, StoryEngineError


# ---- 自定义错误 ----
class CharacterCountExceededError(StoryEngineError):
    def __init__(self, provided: int, maximum: int, mode_key: str = "") -> None:
        super().__init__(
            code=EngineErrorCode.CHARACTER_COUNT_EXCEEDED,
            message=f"提供了 {provided} 个角色，但当前模式只支持最多 {maximum} 个。"
                    f"{(' 模式: ' + mode_key) if mode_key else ''}",
        )


class CharacterRoleIdConflictError(StoryEngineError):
    def __init__(self, role_id: str) -> None:
        super().__init__(
            code=EngineErrorCode.CHARACTER_ROLE_ID_CONFLICT,
            message=f"role_id '{role_id}' 在同一请求中重复出现。",
        )


class CharacterIdConflictError(StoryEngineError):
    def __init__(self, character_id: str) -> None:
        super().__init__(
            code=EngineErrorCode.CHARACTER_ID_CONFLICT,
            message=f"character_id '{character_id}' 在同一请求中重复出现——",
        )


class CharacterInvalidError(StoryEngineError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            code=EngineErrorCode.CHARACTER_INVALID,
            message=detail,
        )


# ---- 默认角色人数 ----
DEFAULT_MAX_CHARACTERS = 5
ROLE_ID_PREFIX = "role_"


# ============================================================================
# CharacterResolver
# ============================================================================
class CharacterResolver:
    """角色解析器。

    用法:
        resolver = CharacterResolver(max_characters=5)
        snapshots, missing_count = resolver.resolve(inputs)
    """

    def __init__(self, max_characters: int = DEFAULT_MAX_CHARACTERS):
        self._max = max_characters

    # ------------------------------------------------------------------
    def resolve(
        self,
        inputs: list[CharacterInput] | None,
    ) -> tuple[list[ResolvedCharacter], int]:
        """解析用户输入 → (当前已解析角色列表, 缺失角色数)。

        缺失角色数 = max_characters - len(snapshots)。
        caller (cast_design) 负责按此数量生成剩余角色。
        """
        if not inputs:
            return [], self._max

        self._validate(inputs)
        snapshots = self._build_snapshots(inputs)
        missing = max(0, self._max - len(snapshots))
        return snapshots, missing

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _validate(self, inputs: list[CharacterInput]) -> None:
        # 数量约束
        if len(inputs) > self._max:
            raise CharacterCountExceededError(len(inputs), self._max)

        seen_role_ids: set[str] = set()
        seen_char_ids: set[str] = set()

        for inp in inputs:
            # name 强制
            if not inp.canon.name or not inp.canon.name.strip():
                raise CharacterInvalidError("每个角色至少需要提供 name")

            # role_id 唯一性
            if inp.role_id:
                if inp.role_id in seen_role_ids:
                    raise CharacterRoleIdConflictError(inp.role_id)
                seen_role_ids.add(inp.role_id)

            # character_id 唯一性（如果提供）
            if inp.character_id:
                if inp.character_id in seen_char_ids:
                    raise CharacterIdConflictError(inp.character_id)
                seen_char_ids.add(inp.character_id)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------
    def _build_snapshots(self, inputs: list[CharacterInput]) -> list[ResolvedCharacter]:
        snapshots: list[ResolvedCharacter] = []
        for idx, inp in enumerate(inputs):
            role_id = inp.role_id or f"{ROLE_ID_PREFIX}{idx + 1:02d}"

            canon_locked = inp.canon.locked_fields()
            runtime_overrides = inp.runtime.override_fields() if inp.runtime else []

            snapshots.append(
                ResolvedCharacter(
                    role_id=role_id,
                    character_id=inp.character_id or inp.canon.character_id,
                    canon=inp.canon,
                    runtime=inp.runtime or CharacterRuntimeInput(),
                    source="user",
                    canon_locked_fields=canon_locked,
                    runtime_override_fields=runtime_overrides,
                )
            )
        return snapshots

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def pin_prompt_context(self, snapshots: list[ResolvedCharacter]) -> str:
        """为 cast_design prompt 生成"已固定角色"的上下文文本。

        只传必要信息，不把整个内部 State 塞给模型。
        """
        if not snapshots:
            return ""

        lines = ["【已固定角色 — 这些角色必须保留，不得修改 Canon 字段】"]
        for s in snapshots:
            parts = [f"\n角色 {s.role_id}: {s.canon.summary()}"]
            if s.runtime_override_fields:
                parts.append(f"  本次 Runtime 约束: {s.runtime.summary()}")
            lines.extend(parts)
        return "\n".join(lines)

    def summary(self, snapshots: list[ResolvedCharacter]) -> dict:
        """生成 Telemetry 摘要。"""
        provided = sum(1 for s in snapshots if s.source == "user")
        generated = len(snapshots) - provided
        return {
            "provided_character_count": provided,
            "generated_character_count": generated,
            "total": len(snapshots),
            "canon_locked": sum(len(s.canon_locked_fields) for s in snapshots),
            "runtime_overrides": sum(len(s.runtime_override_fields) for s in snapshots),
        }

    # ------------------------------------------------------------------
    # Phase 8: Memory Integration  (non-breaking enrichment)
    # ------------------------------------------------------------------
    def enrich_with_memory(
        self,
        snapshots: list[ResolvedCharacter],
        memory_service: Any = None,
        story_query: str = "",
    ) -> list[ResolvedCharacter]:
        """Enrich resolved characters with selected Character Memory.

        Only modifies Runtime fields that are NOT already set by user
        or runtime_defaults. Selected memories are stored separately.

        When memory_service is None or disabled, returns snapshots unchanged.
        """
        if memory_service is None or not getattr(memory_service, "enabled", False):
            return snapshots

        enriched: list[ResolvedCharacter] = []
        for s in snapshots:
            if not s.character_id:
                enriched.append(s)
                continue

            # Select relevant memories
            selected = memory_service.select_for_character(
                s.character_id, story_query,
            )
            if not selected:
                enriched.append(s)
                continue

            # Canon guard
            safe, conflicts = memory_service.filter_by_canon(s.canon, selected)

            # Only fill Runtime fields that are not already set
            runtime = s.runtime
            for sm in safe:
                self._apply_memory_to_runtime(runtime, sm)

            # Store selected memory IDs for traceability
            s.selected_memories = [sm.memory_id for sm in safe]  # type: ignore[attr-defined]
            s.memory_contexts = [sm.summary() for sm in safe]  # type: ignore[attr-defined]
            enriched.append(s)

        return enriched

    def _apply_memory_to_runtime(
        self, runtime: Any, sm: Any,
    ) -> None:
        """Apply a selected memory to Runtime if the field is empty.

        Priority: User > runtime_defaults > Memory > Director.
        """
        mapping = {
            "goal_outcome": "goal",
            "preference": "stance",
            "belief_or_attitude": "stance",
            "relationship": "motivation",
            "experience": None,  # experience is contextual, not direct field
        }

        target_field = mapping.get(sm.memory_type)
        if target_field is None:
            return

        current = getattr(runtime, target_field, None)
        if current is None or (isinstance(current, str) and not current.strip()):
            setattr(runtime, target_field, sm.content)