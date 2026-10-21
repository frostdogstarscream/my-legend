"""Operation boundaries and continuity folding shared by planning and delivery."""
from __future__ import annotations

from copy import deepcopy

from .schemas import StoryDelivery


def source_delivery(source) -> StoryDelivery | None:
    raw = source.source_content_snapshot.get("delivery") if source else None
    return StoryDelivery.model_validate(raw) if raw else None


def operation_requirements(request) -> dict[str, str]:
    intent = request["intent"]
    required = {"instruction": request["user_instruction"]}
    required["timeline"] = (
        "同一交付版本内各集事实、处境和明确时间安排必须连续一致。"
        "不得把完成任务的截止时刻混同为已经约定的事件/播出时刻；"
        "时间安排如改变须在正文明确通知或解释。大纲节拍的播放秒数不是剧情时点。"
    )
    explicit = request.get("client_context", {}).get("operation_requirements", {})
    if not isinstance(explicit, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip()
                                              for k, v in explicit.items()):
        raise ValueError("INVALID_OPERATION_REQUIREMENTS")
    required.update({f"constraint:{k}": v for k, v in explicit.items()})
    if intent == "continue":
        required.update(identity="继承来源角色身份、姓名和演绎者", facts="不得违背来源已发生事实",
                        relationships="继承并合理发展来源关系", threads="按指令继承或回收伏笔，不能凭空消失")
    elif intent == "revise":
        required["edit_instruction"] = request["edit"]["instruction"]
        required.update({f"preserve:{k}": f"保持{k}不变" for k, v in request["edit"]["preserve"].items() if v})
    elif intent == "remix":
        required["remix_instruction"] = request["remix"]["instruction"]
        required.update({f"retain:{i}": t for i, t in enumerate(request["remix"]["retain_elements"])})
        required.update({f"transform:{i}": t for i, t in enumerate(request["remix"]["transform_elements"])})
    kinds = request.get("client_context", {}).get("evidence_kinds", {})
    if not isinstance(kinds, dict) or any(
        k not in required or not isinstance(v, list) or not v
        or any(item not in ("dialogue", "narration", "sfx", "music", "action") for item in v)
        for k, v in kinds.items()
    ):
        raise ValueError("INVALID_EVIDENCE_KINDS")
    return required


def evidence_kind_errors(request, checks, episodes):
    restrictions = request.get("client_context", {}).get("evidence_kinds", {})
    lines = {u.line_id: u for ep in episodes for scene in ep.scenes for u in scene.utterances}
    by_requirement = {c["requirement_id"]: c for c in checks}
    errors = []
    for key, allowed in restrictions.items():
        cited = by_requirement.get(key, {}).get("evidence_line_ids", [])
        if not any(k in lines and lines[k].kind in allowed for k in cited):
            errors.append(f"OPERATION_EVIDENCE_KIND_MISMATCH: {key}: expected {allowed}")
    return errors


def revision_impact(edit, delivery, selected_episode_id=None) -> dict:
    target = edit.target
    ep_id = target.get("episode_id") or selected_episode_id
    episodes = delivery.episodes
    ids = [ep.episode_id for ep in episodes]
    selected = next((ep for ep in episodes if ep.episode_id == ep_id), None)
    scope = edit.scope
    if scope == "whole_story":
        selected_ids = ids
    elif scope == "arc":
        selected_ids = target.get("episode_ids", [])
        positions = [ids.index(item) for item in selected_ids if item in ids]
        if (not selected_ids or len(set(selected_ids)) != len(selected_ids)
                or len(positions) != len(selected_ids)
                or positions != list(range(positions[0], positions[-1] + 1))):
            raise ValueError("REVISION_TARGET_NOT_FOUND: arc requires contiguous episode_ids")
    else:
        if scope == "ending" and not ep_id:
            selected = episodes[-1]
        if selected is None:
            raise ValueError("REVISION_TARGET_NOT_FOUND: episode_id")
        selected_ids = [selected.episode_id]
    scene_ids, line_ids = [], []
    if scope in ("line", "scene", "ending"):
        available = [s.scene_id for s in selected.scenes]
        scene_ids = (target.get("scene_ids") or [available[-1]]) if scope == "ending" else [target.get("scene_id")]
        positions = [available.index(item) for item in scene_ids if item in available]
        if (not scene_ids or len(set(scene_ids)) != len(scene_ids)
                or len(positions) != len(scene_ids)
                or (scope == "ending" and positions != list(range(positions[0], len(available))))):
            raise ValueError("REVISION_TARGET_NOT_FOUND: scene_id or ending suffix")
        if scope == "line":
            line_ids = [target.get("line_id")]
            scene = next(s for s in selected.scenes if s.scene_id == scene_ids[0])
            if line_ids[0] not in [u.line_id for u in scene.utterances]:
                raise ValueError("REVISION_TARGET_NOT_FOUND: line_id")
    affected = [ep.index for ep in episodes if ep.episode_id in selected_ids]
    downstream = []
    if edit.downstream_policy == "replan_affected":
        downstream = [ep.index for ep in episodes if ep.index > max(affected)]
    return {
        "source_version_id": delivery.story_version_id, "scope": scope,
        "downstream_policy": edit.downstream_policy,
        "target_episode_ids": selected_ids, "target_scene_ids": scene_ids,
        "target_line_ids": line_ids, "downstream_episode_indexes": downstream,
        "affected_episode_indexes": sorted([*affected, *downstream]),
        "requires_continuity_recheck": True,
    }


def fold_continuity(episodes, base=None) -> dict:
    state = deepcopy(base or {})
    state.setdefault("facts", [])
    state.setdefault("relationships", [])
    state.setdefault("unresolved_threads", [])
    state.setdefault("episode_summaries", [])
    for ep in episodes:
        for key, delta in (("facts", ep.fact_delta), ("relationships", ep.relationship_delta)):
            for item in delta:
                # Stable fact/relationship IDs replace earlier values; otherwise keep
                # the chronological evidence rather than guessing entity semantics.
                identity = item.get("id") or item.get("fact_id") or item.get("relationship_id")
                if identity:
                    state[key] = [old for old in state[key] if
                                  (old.get("id") or old.get("fact_id") or old.get("relationship_id")) != identity]
                if item not in state[key]:
                    state[key].append(deepcopy(item))
        state["unresolved_threads"] = [t for t in state["unresolved_threads"] if t not in ep.closed_threads]
        state["unresolved_threads"].extend(t for t in ep.new_open_threads if t not in state["unresolved_threads"])
        state["episode_summaries"].append({"index": ep.index, "summary": ep.episode_summary})
    return state


def operation_errors(snapshot, delivery) -> list[str]:
    source = source_delivery(snapshot.preview.source_reference)
    intent = snapshot.request["intent"]
    errors = []
    generated = delivery.generated_episode_ids
    expected = snapshot.preview.plan.episode_outlines
    changed = [ep for ep in delivery.episodes if ep.episode_id in generated]
    if len(set(generated)) != len(generated) or len(changed) != len(expected):
        errors.append("generated episode count/identity mismatch")
    if [ep.index for ep in changed] != [ep.index for ep in expected]:
        errors.append("generated episode indexes differ from approved plan")
    if source:
        if delivery.parent_story_version_id != source.story_version_id:
            errors.append("parent version mismatch")
        same = intent in ("continue", "revise")
        if (delivery.story_id == source.story_id) != same:
            errors.append("story lineage mismatch")
        if intent == "continue":
            if delivery.character_snapshot != source.character_snapshot:
                errors.append("continuation changed character snapshots")
            if delivery.episodes[:len(source.episodes)] != source.episodes:
                errors.append("continuation changed inherited episodes")
            if len(delivery.episodes) != len(source.episodes) + len(expected):
                errors.append("continuation missing complete playback episodes")
        if intent == "revise":
            impact = snapshot.preview.impact_analysis
            if [e.episode_id for e in delivery.episodes] != [e.episode_id for e in source.episodes]:
                errors.append("revision changed episode identity/order")
            for old, new in zip(source.episodes, delivery.episodes):
                if old.index not in impact["affected_episode_indexes"]:
                    if old != new:
                        errors.append(f"revision changed protected episode {old.episode_id}")
                elif old.index not in impact["downstream_episode_indexes"] and impact["scope"] in ("line", "scene", "ending"):
                    if [s.scene_id for s in old.scenes] != [s.scene_id for s in new.scenes]:
                        errors.append("local revision changed scene identity/order")
                    for old_scene, new_scene in zip(old.scenes, new.scenes):
                        if old_scene.scene_id not in impact["target_scene_ids"] and old_scene != new_scene:
                            errors.append(f"revision changed protected scene {old_scene.scene_id}")
                        elif impact["scope"] == "line":
                            if [u.line_id for u in old_scene.utterances] != [u.line_id for u in new_scene.utterances]:
                                errors.append("line revision changed line identity/order")
                            for a, b in zip(old_scene.utterances, new_scene.utterances):
                                if a.line_id not in impact["target_line_ids"] and a != b:
                                    errors.append(f"revision changed protected line {a.line_id}")
    return errors
