"""Phase 3: Canon Overlay —— 确定性覆盖 LLM 可能改掉的用户 Canon 字段。

原则：
  1. LLM cast 输出 → overlay 用户 Canon locked fields → 最终值
  2. 如果 overlay 实际修改了字段 → 记录 conflict（telemetry warning）
  3. 不是让 LLM 别改（Prompt 做不到），而是改完之后覆盖回去

同时还提供 Runtime Overlay：用户 Runtime Override 覆盖 LLM 生成的 Behavior。
"""
from __future__ import annotations

from .models import CharacterCanon, CharacterRuntimeInput, ResolvedCharacter


# ============================================================================
# Canon Overlay
# ============================================================================
def overlay_canon(
    llm_output: dict,
    locked_canon: CharacterCanon,
    locked_fields: list[str],
    role_id: str = "",
) -> tuple[dict, list[dict]]:
    """将 locked_canon 的字段确定性覆盖到 LLM 输出上。

    Returns:
      (final_dict, conflicts): final_dict 是修复后的 dict，conflicts 是发现的冲突列表
    """
    conflicts: list[dict] = []
    canon_dict = locked_canon.model_dump(exclude_none=True)
    result = dict(llm_output)

    for field_path in locked_fields:
        if "." in field_path:
            parent_key, child_key = field_path.split(".", 1)
            canon_parent = canon_dict.get(parent_key)
            if canon_parent and isinstance(canon_parent, dict):
                canon_value = canon_parent.get(child_key)
                llm_parent = result.get(parent_key, {})
                if isinstance(llm_parent, dict):
                    llm_value = llm_parent.get(child_key)
                    if canon_value is not None and llm_value != canon_value:
                        result.setdefault(parent_key, {})
                        result[parent_key][child_key] = canon_value
                        conflicts.append({
                            "role_id": role_id,
                            "field": field_path,
                            "expected": canon_value,
                            "llm_output": llm_value,
                            "action": "overlaid",
                        })
        else:
            canon_value = canon_dict.get(field_path)
            if canon_value is not None:
                llm_value = result.get(field_path)
                if llm_value != canon_value:
                    result[field_path] = canon_value
                    conflicts.append({
                        "role_id": role_id,
                        "field": field_path,
                        "expected": canon_value,
                        "llm_output": llm_value,
                        "action": "overlaid",
                    })

    return result, conflicts


# ============================================================================
# Runtime Overlay
# ============================================================================
def overlay_runtime(
    llm_runtime_dict: dict,
    user_runtime: CharacterRuntimeInput,
    override_fields: list[str],
    role_id: str = "",
) -> tuple[dict, list[dict]]:
    """将用户的 Runtime Override 覆盖 LLM 生成的 Runtime 字段。

    Returns:
      (final_dict, conflicts): final_dict 是修复后的 dict，conflicts 是发现的冲突
    """
    user_dict = user_runtime.model_dump(exclude_none=True)
    conflicts: list[dict] = []
    result = dict(llm_runtime_dict)

    for field_name in override_fields:
        user_value = user_dict.get(field_name)
        if user_value is not None:
            llm_value = result.get(field_name)
            if llm_value != user_value:
                result[field_name] = user_value
                conflicts.append({
                    "role_id": role_id,
                    "field": f"runtime.{field_name}",
                    "expected": user_value,
                    "llm_output": llm_value,
                    "action": "overlaid",
                })

    return result, conflicts


# ============================================================================
# Canon Validator（确定性）
# ============================================================================
def validate_character_canon_preserved(
    final: dict,
    locked_canon: CharacterCanon,
    locked_fields: list[str],
    role_id: str = "",
) -> list[dict]:
    """纯代码校验：所有 canon_locked_fields 是否与输入一致。

    Returns:
      issues 列表（空 = 全部保持）
    """
    issues: list[dict] = []
    canon_dict = locked_canon.model_dump(exclude_none=True)

    for field_path in locked_fields:
        if "." in field_path:
            parent_key, child_key = field_path.split(".", 1)
            canon_parent = canon_dict.get(parent_key)
            expected = canon_parent.get(child_key) if isinstance(canon_parent, dict) else None
            actual_parent = final.get(parent_key, {})
            actual = actual_parent.get(child_key) if isinstance(actual_parent, dict) else None
        else:
            expected = canon_dict.get(field_path)
            actual = final.get(field_path)

        if expected is not None and actual != expected:
            issues.append({
                "role_id": role_id,
                "field": field_path,
                "expected": expected,
                "actual": actual,
                "severity": "error",
            })

    return issues


# ============================================================================
# 批量 Overlay（便捷函数）
# ============================================================================
def reconcile_cast(
    llm_cast: list[dict],
    snapshots: list[ResolvedCharacter],
) -> tuple[list[dict], list[dict]]:
    """对 LLM 产出的 cast 列表执行 Canon Overlay。

    逐个匹配 snapshots，修复任何 drifted 字段。
    返回 (fixed_cast, all_conflicts)。
    """
    fixed: list[dict] = []
    all_conflicts: list[dict] = []
    # 建立 role_id → snapshot 索引，同时用 name 做辅助匹配
    snap_by_role = {s.role_id: s for s in snapshots}
    snap_by_name = {s.canon.name: s for s in snapshots}

    for entry in llm_cast:
        name = entry.get("name", "")
        role_id = entry.get("role_id", "")

        snapshot = (
            snap_by_role.get(role_id)
            or snap_by_name.get(name)
        )

        if snapshot and snapshot.source == "user" and snapshot.canon_locked_fields:
            fixed_entry, conflicts = overlay_canon(
                entry, snapshot.canon, snapshot.canon_locked_fields, snapshot.role_id
            )
            fixed.append(fixed_entry)
            all_conflicts.extend(conflicts)
        else:
            fixed.append(entry)

    return fixed, all_conflicts