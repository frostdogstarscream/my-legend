"""Source-linked visual choices; validation never claims semantic or human approval."""
from typing import Literal
from copy import deepcopy
from pydantic import Field

from .contracts import Contract
from .repository import ProductionError


# One set of field rules drives both planner guidance and deterministic validation.
# Unknown actions are retained for the capability gate, never downgraded to wait.
ACTION_PARAMETER_RULES = {
    "target_anchor": ("move", "VISUAL_MOVE_TARGET_INVALID", "move 必须填写目录锚点；其他动作必须为 null。enter/exit 的起止位置由执行器确定。"),
    "target_role_id": ("face", "VISUAL_TARGET_ROLE_INVALID", "仅 face 可以指定目标角色；move 使用 target_anchor，不使用 target_role_id。"),
    "direction": ("face", "VISUAL_DIRECTION_INVALID", "仅 face 可以填写 direction，且与 target_role_id 二选一。"),
    "emotion": ("react", "VISUAL_EMOTION_INVALID", "react 必须填写支持的 emotion；其他动作必须为 null。"),
    "intensity": ("react", "VISUAL_EMOTION_INVALID", "仅 react 可填写 0 到 1 的 intensity，其他动作必须为 null。"),
}


def action_parameter_issues(action):
    """Collect cross-field issues even when another field has an invalid enum.

    Raw model values remain untouched. Basic types/enums are checked by Pydantic.
    """
    if not isinstance(action, dict) or not isinstance(action.get("action"), str):
        return []
    kind = action["action"]
    issues = []
    for field, (owner, code, rule) in ACTION_PARAMETER_RULES.items():
        if kind != owner and action.get(field) is not None:
            issues.append({"field": field, "error_code": code, "rule": rule})
    if kind == "move" and action.get("target_anchor") is None:
        issues.append({"field": "target_anchor", "error_code": "VISUAL_MOVE_TARGET_INVALID",
                       "rule": ACTION_PARAMETER_RULES["target_anchor"][2]})
    if kind == "face" and (action.get("direction") is None) == (action.get("target_role_id") is None):
        issues.append({"field": "direction", "error_code": "VISUAL_FACE_TARGET_INVALID",
                       "rule": "face 的 direction 与 target_role_id 必须且只能填写一个。"})
    if kind == "react" and action.get("emotion") is None:
        issues.append({"field": "emotion", "error_code": "VISUAL_REACTION_EMOTION_MISSING",
                       "rule": ACTION_PARAMETER_RULES["emotion"][2]})
    return issues


def visual_parameter_feedback(raw):
    issues = []
    scenes = raw.get("scenes") if isinstance(raw, dict) else None
    if not isinstance(scenes, list):
        return issues
    for si, scene in enumerate(scenes):
        if not isinstance(scene, dict) or not isinstance(scene.get("actions"), list):
            continue
        for ai, action in enumerate(scene["actions"]):
            for issue in action_parameter_issues(action):
                issues.append({**issue, "path": ["scenes", si, "actions", ai, issue["field"]],
                               "episode_id": scene.get("episode_id"), "scene_id": scene.get("scene_id"),
                               "line_id": action.get("line_id"), "action": action.get("action")})
    return issues


def visual_planner_schema(delivery=None):
    schema = VisualContext.model_json_schema()
    binding = schema["$defs"]["ActionBinding"]
    conditions = []
    for field, (owner, _, rule) in ACTION_PARAMETER_RULES.items():
        binding["properties"][field]["description"] = rule
        conditions.append({"if": {"properties": {"action": {"const": owner}}, "required": ["action"]},
                           "else": {"properties": {field: {"type": "null"}}}})
    for kind, field in (("move", "target_anchor"), ("react", "emotion")):
        conditions.append({"if": {"properties": {"action": {"const": kind}}, "required": ["action"]},
                           "then": {"required": [field], "properties": {field: {"not": {"type": "null"}}}}})
    conditions.append({"if": {"properties": {"action": {"const": "face"}}, "required": ["action"]},
                       "then": {"oneOf": [
                           {"required": ["direction"], "properties": {"direction": {"type": "string"}, "target_role_id": {"type": "null"}}},
                           {"required": ["target_role_id"], "properties": {"target_role_id": {"type": "string"}, "direction": {"type": "null"}}},
                       ]}})
    binding["allOf"] = conditions
    if delivery is not None:
        scene_schemas = []
        for episode in delivery['episodes']:
            for source in episode['scenes']:
                scene = deepcopy(schema['$defs']['SceneBinding'])
                scene['properties']['episode_id'] = {'const':episode['episode_id']}
                scene['properties']['scene_id'] = {'const':source['scene_id']}
                scene['properties']['source_location'] = {'const':source['location']}
                action_schemas = []
                for line in source['utterances']:
                    if line['kind'] == 'action':
                        action = deepcopy(binding)
                        action['properties']['line_id'] = {'const':line['line_id']}
                        action_schemas.append(action)
                scene['properties']['actions'] = {'type':'array','minItems':len(action_schemas),
                    'maxItems':len(action_schemas), 'items':False}
                if action_schemas:
                    scene['properties']['actions']['prefixItems'] = action_schemas
                scene_schemas.append(scene)
        schema['properties']['source_version_id'] = {'const':delivery['story_version_id']}
        schema['properties']['scenes'] = {'type':'array','minItems':len(scene_schemas),'maxItems':len(scene_schemas),
                                         'prefixItems':scene_schemas,'items':False}
    return schema


class SourceEvidence(Contract):
    episode_id: str
    scene_id: str
    line_id: str
    quote: str = Field(min_length=1)


class AppearanceBinding(Contract):
    role_id: str
    template_type: Literal["adult", "elder", "child", "animal"]
    color_key: Literal["blue", "green", "red", "brown"]
    accessory: Literal["none", "hat", "beard"]
    origin: Literal["character_profile", "production_choice"]
    reason: str = Field(min_length=1)


class ActionBinding(Contract):
    line_id: str
    actor_role_ids: list[str] = Field(min_length=1)
    action: str = Field(min_length=1)
    target_role_id: str | None = None
    target_anchor: Literal["slot_1", "slot_2", "slot_3", "slot_4", "entry", "exit"] | None = None
    direction: Literal["down", "left", "right", "up"] | None = None
    emotion: Literal["neutral", "happy", "sad", "angry", "surprised", "serious"] | None = None
    intensity: float | None = Field(default=None, ge=0, le=1)
    evidence: list[SourceEvidence] = Field(min_length=1)


class SceneBinding(Contract):
    episode_id: str
    scene_id: str
    source_location: str
    background_type: Literal["indoor", "courtyard", "outdoor"]
    background_reason: str = Field(min_length=1)
    cast: list[str] = Field(min_length=1, max_length=4)
    initial_cast: list[str]
    presence_evidence: list[SourceEvidence] = Field(min_length=1)
    voice_only_role_ids: list[str] = Field(default_factory=list)
    continuity_group_id: str | None = None
    actions: list[ActionBinding]


class VisualContext(Contract):
    schema_version: Literal["production_visual_context/1.0"] = "production_visual_context/1.0"
    source_version_id: str
    asset_pack: Literal["basic_pixel/1.0.0"] = "basic_pixel/1.0.0"
    asset_catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    appearances: list[AppearanceBinding] = Field(min_length=1, max_length=6)
    scenes: list[SceneBinding] = Field(min_length=1)
    human_review: Literal["pending", "not_required"] = "pending"


def validate_visual_context(context, delivery, *, supported_actions):
    """Check source references, complete action coverage and event-by-event presence.

    Whether an action's interpretation matches prose additionally requires the semantic
    planning/review stage. This function proves only deterministic structural invariants.
    """
    if context.source_version_id != delivery["story_version_id"]:
        raise ProductionError("SOURCE_VERSION_MISMATCH")
    roles = {c["role_id"] for c in delivery["character_snapshot"]}
    bound = [a.role_id for a in context.appearances]
    if len(bound) != len(set(bound)) or not set(bound) <= roles:
        raise ProductionError("VISUAL_ROLE_BINDING_INVALID")
    scenes = {(e["episode_id"], s["scene_id"]): s for e in delivery["episodes"] for s in e["scenes"]}
    references = {(e["episode_id"], s["scene_id"], u["line_id"]): u
                  for e in delivery["episodes"] for s in e["scenes"] for u in s["utterances"]}
    keys = [(s.episode_id, s.scene_id) for s in context.scenes]
    if len(keys) != len(set(keys)) or keys != list(scenes):
        raise ProductionError("VISUAL_SCENE_COVERAGE_MISMATCH")
    continuity = {}
    for scene in context.scenes:
        key = (scene.episode_id, scene.scene_id)
        original = scenes[key]
        if scene.source_location != original["location"]:
            raise ProductionError("VISUAL_LOCATION_CHANGED")
        if len(scene.cast) != len(set(scene.cast)) or not set(scene.cast) <= set(bound):
            raise ProductionError("VISUAL_CAST_INVALID")
        if len(scene.initial_cast) != len(set(scene.initial_cast)) or not set(scene.initial_cast) <= set(scene.cast):
            raise ProductionError("VISUAL_INITIAL_PRESENCE_INVALID")
        if not set(scene.voice_only_role_ids) <= roles or set(scene.voice_only_role_ids) & set(scene.cast):
            raise ProductionError("VISUAL_VOICE_ONLY_INVALID")
        evidence = scene.presence_evidence + [item for action in scene.actions for item in action.evidence]
        for item in evidence:
            line = references.get((item.episode_id, item.scene_id, item.line_id))
            if (item.episode_id, item.scene_id) != key or not line or item.quote not in line["text"]:
                raise ProductionError("VISUAL_EVIDENCE_INVALID")
        actions = {a.line_id: a for a in scene.actions}
        expected = [u["line_id"] for u in original["utterances"] if u["kind"] == "action"]
        if len(actions) != len(scene.actions) or list(actions) != expected:
            raise ProductionError("VISUAL_ACTION_COVERAGE_MISMATCH")
        present = set(scene.initial_cast)
        if scene.continuity_group_id in continuity and present != continuity[scene.continuity_group_id]:
            raise ProductionError("VISUAL_CONTINUITY_PRESENCE_CHANGED")
        for line in original["utterances"]:
            if line["kind"] == "dialogue" and line["speaker_role_id"] not in present | set(scene.voice_only_role_ids):
                raise ProductionError("VISUAL_SPEAKER_NOT_PRESENT")
            if line["kind"] != "action":
                continue
            action = actions[line["line_id"]]
            actors = set(action.actor_role_ids)
            if len(actors) != len(action.actor_role_ids) or not actors <= set(scene.cast):
                raise ProductionError("VISUAL_ACTION_ACTOR_INVALID")
            if action.action not in supported_actions:
                raise ProductionError("VISUAL_ACTION_UNSUPPORTED")
            if len(actors) != 1:
                raise ProductionError("VISUAL_ACTION_UNSUPPORTED")
            parameter_issues = action_parameter_issues(action.model_dump())
            if parameter_issues:
                raise ProductionError(parameter_issues[0]["error_code"])
            if not any(e.line_id == line["line_id"] for e in action.evidence):
                raise ProductionError("VISUAL_ACTION_EVIDENCE_MISSING")
            if action.action == "enter":
                if actors & present:
                    raise ProductionError("VISUAL_ALREADY_PRESENT")
                present |= actors
            elif not actors <= present:
                raise ProductionError("VISUAL_ACTOR_NOT_PRESENT")
            if action.target_role_id is not None and action.target_role_id not in present:
                raise ProductionError("VISUAL_TARGET_NOT_PRESENT")
            if action.action == "exit":
                present -= actors
        if scene.continuity_group_id is not None:
            continuity[scene.continuity_group_id] = set(present)
    return context
