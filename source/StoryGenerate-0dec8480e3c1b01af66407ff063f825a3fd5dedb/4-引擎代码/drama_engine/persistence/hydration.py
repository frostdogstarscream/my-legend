"""Phase 4B: Character Template Hydration。

将 character_id 从 opaque identifier 转化为可查询的 DB Character Template：
    - character_id only → DB 加载 Canon + Runtime Defaults
    - character_id + runtime → DB Canon + DB Runtime Defaults + Request Runtime
    - inline Canon only → 不变（ephemeral character）
    - None → 不变（全自动生成）

不与数据库直接交互：通过 Repository Protocol。
"""
from __future__ import annotations

from typing import Any

from ..characters.models import CharacterCanon, CharacterInput, CharacterRuntimeInput
from .repository import CharacterTemplate, MemoryCharacterTemplateRepository


class CharacterTemplateNotFoundError(KeyError):
    """CHARACTER_NOT_FOUND: character_id 在 DB 中不存在。"""


class CharacterCanonConflictError(ValueError):
    """CHARACTER_CANON_CONFLICT: 请求 inline Canon 与 DB Canon 不一致。"""


def hydrate_character_inputs(
    raw_inputs: list[dict[str, Any]] | None,
    character_repo,
) -> list[CharacterInput]:
    """将原始 character 输入（dict 列表）hydration 为 CharacterInput。

    支持三种范式：
      A. {"canon": {...}}                        → inline Canon (ephemeral)
      B. {"character_id": "CHAR_001"}             → DB 加载
      C. {"character_id": "CHAR_001", "runtime": {...}}  → DB + request runtime

    优先级：
      Request Runtime > Template Runtime Defaults > Auto Generation
    """
    if not raw_inputs:
        return []

    results: list[CharacterInput] = []
    for raw in raw_inputs:
        character_id = raw.get("character_id", "")
        inline_canon = raw.get("canon", {})
        runtime_override = raw.get("runtime", {})

        if character_id:
            # Case B / C：从 DB 加载 Template
            template = character_repo.get_active(character_id)
            if template is None:
                raise CharacterTemplateNotFoundError(
                    f"CHARACTER_NOT_FOUND: character_id={character_id}"
                )

            # Canon 来自 DB，不可被请求覆盖
            canon = _build_canon_from_template(template)

            # 如果请求同时传了 inline Canon，做一致性检查
            if inline_canon:
                _check_canon_consistency(character_id, canon, inline_canon)

            # Runtime：合并 DB defaults + request runtime
            runtime = _merge_runtime(template, runtime_override)

            results.append(CharacterInput(
                character_id=character_id,
                canon=canon,
                runtime=runtime,
            ))
        elif inline_canon:
            # Case A：inline Canon only（ephemeral）
            results.append(_build_inline_character(inline_canon, runtime_override))
        else:
            # 空对象：跳过
            continue

    return results


def _build_canon_from_template(template: CharacterTemplate) -> CharacterCanon:
    """从 CharacterTemplate 构造 Phase 3 CharacterCanon。"""
    canon_data: dict[str, Any] = dict(template.canon)
    canon_data.setdefault("character_id", template.character_id)
    canon_data.setdefault("name", template.name)
    return CharacterCanon(**canon_data)


def _build_inline_character(
    canon_data: dict[str, Any],
    runtime_data: dict[str, Any],
) -> CharacterInput:
    """从 inline Canon 构造 CharacterInput。"""
    character_id = canon_data.get("character_id", "")
    return CharacterInput(
        character_id=character_id if character_id else "",
        canon=CharacterCanon(**canon_data),
        runtime=CharacterRuntimeInput(**runtime_data) if runtime_data else CharacterRuntimeInput(),
    )


def _merge_runtime(
    template: CharacterTemplate,
    request_runtime: dict[str, Any],
) -> CharacterRuntimeInput:
    """合并 Template Runtime Defaults + Request Runtime。

    priority: Request Runtime > Template Runtime Defaults
    """
    merged: dict[str, Any] = dict(template.runtime_defaults)
    merged.update({k: v for k, v in request_runtime.items() if v is not None})
    return CharacterRuntimeInput(**merged) if merged else CharacterRuntimeInput()


def _check_canon_consistency(
    character_id: str,
    db_canon: CharacterCanon,
    inline_canon: dict[str, Any],
) -> None:
    """检查 inline Canon 与 DB Canon 是否一致。

    冲突时拒绝（不覆盖 DB），未来可加 allow_non_canon 模式。
    """
    for field in db_canon.locked_fields():
        db_val = _get_canon_value(db_canon, field)
        inline_val = inline_canon.get(field, None)
        if inline_val is not None and db_val is not None:
            db_str = str(db_val).strip()
            inline_str = str(inline_val).strip()
            if db_str != inline_str:
                raise CharacterCanonConflictError(
                    f"CHARACTER_CANON_CONFLICT: character_id={character_id} "
                    f"field={field} db_value={db_str!r} request_value={inline_str!r}"
                )


def _get_canon_value(canon: CharacterCanon, field_path: str) -> Any:
    """从 CharacterCanon 中提取字段值。"""
    if "." in field_path:
        parent_key, child_key = field_path.split(".", 1)
        parent = getattr(canon, parent_key, None)
        return getattr(parent, child_key, None) if parent else None
    return getattr(canon, field_path, None)