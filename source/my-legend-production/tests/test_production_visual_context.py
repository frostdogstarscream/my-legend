from copy import deepcopy
import pytest

from my_legend_production.production.visual_contracts import VisualContext, validate_visual_context
from my_legend_production.production.visual_contracts import action_parameter_issues, visual_parameter_feedback, visual_planner_schema
from my_legend_production.production.repository import ProductionError


def sample():
    delivery = {"story_version_id": "v", "character_snapshot": [{"role_id": "a"}, {"role_id": "b"}],
        "episodes": [{"episode_id": "ep", "scenes": [{"scene_id": "room", "location": "院子",
            "utterances": [{"line_id": "l1", "kind": "action", "text": "甲走进院子。"},
                {"line_id": "l2", "kind": "dialogue", "text": "你好。", "speaker_role_id": "a"},
                {"line_id": "l3", "kind": "action", "text": "甲离开院子。"}]}]}]}
    def evidence(line, quote):
        return {"episode_id": "ep", "scene_id": "room", "line_id": line, "quote": quote}
    context = {"source_version_id": "v", "asset_catalog_sha256": "a" * 64,
        "appearances": [{"role_id": "a", "template_type": "adult", "color_key": "blue", "accessory": "none",
                         "origin": "production_choice", "reason": "通用像素演员，未推断真实年龄。"}],
        "scenes": [{"episode_id": "ep", "scene_id": "room", "source_location": "院子", "background_type": "courtyard",
                    "background_reason": "源地点为院子", "cast": ["a"], "initial_cast": [],
                    "presence_evidence": [evidence("l1", "甲走进院子。")],
                    "actions": [{"line_id": "l1", "actor_role_ids": ["a"], "action": "enter", "evidence": [evidence("l1", "走进")]},
                                {"line_id": "l3", "actor_role_ids": ["a"], "action": "exit", "evidence": [evidence("l3", "离开")]}]}]}
    return delivery, context


def validate(delivery, raw):
    return validate_visual_context(VisualContext.model_validate(raw), delivery,
                                   supported_actions={"enter", "exit", "move", "face", "wait"})


def test_presence_is_event_based_and_unused_characters_are_not_added():
    delivery, context = sample()
    result = validate(delivery, context)
    assert result.scenes[0].initial_cast == [] and result.scenes[0].cast == ["a"]
    assert result.scenes[0].background_type == "courtyard"


@pytest.mark.parametrize('binding,codes', [
    ({'action':'enter','target_anchor':'slot_2','emotion':'neutral'}, {'VISUAL_MOVE_TARGET_INVALID','VISUAL_EMOTION_INVALID'}),
    ({'action':'exit','target_anchor':'exit'}, {'VISUAL_MOVE_TARGET_INVALID'}),
    ({'action':'move','target_role_id':'a'}, {'VISUAL_MOVE_TARGET_INVALID','VISUAL_TARGET_ROLE_INVALID'}),
    ({'action':'face','direction':'left','target_role_id':'a','target_anchor':'exit'}, {'VISUAL_FACE_TARGET_INVALID','VISUAL_MOVE_TARGET_INVALID'}),
    ({'action':'react'}, {'VISUAL_REACTION_EMOTION_MISSING'}),
    ({'action':'move','target_anchor':'slot_2'}, set()),
    ({'action':'face','target_role_id':'a'}, set()),
    ({'action':'face','direction':'left'}, set()),
    ({'action':'react','emotion':'happy','intensity':0.5}, set()),
    ({'action':'enter'}, set()),
])
def test_action_field_contract(binding, codes):
    assert {i['error_code'] for i in action_parameter_issues(binding)} == codes


def test_raw_feedback_tolerates_malformed_neighbors_without_hiding_valid_locations():
    raw = {'scenes': [None, {'episode_id':'ep','scene_id':'s','actions': [None, {'action':'move','line_id':'l'}]}]}
    issue, = visual_parameter_feedback(raw)
    assert issue['path'] == ['scenes',1,'actions',1,'target_anchor']
    assert issue['line_id'] == 'l'


def test_planner_schema_exposes_cross_field_rules_and_retains_unknown_actions():
    binding = visual_planner_schema()['$defs']['ActionBinding']
    assert len(binding['allOf']) == 8
    assert 'enum' not in binding['properties']['action']
    assert 'target_role_id' in binding['properties']['direction']['description']


def test_bound_schema_has_exact_action_order_and_no_added_narration_actions():
    delivery, _ = sample()
    scene=visual_planner_schema(delivery)['properties']['scenes']['prefixItems'][0]
    actions=scene['properties']['actions']
    assert actions['minItems']==actions['maxItems']==2 and actions['items'] is False
    assert [a['properties']['line_id']['const'] for a in actions['prefixItems']]==['l1','l3']
    delivery['episodes'][0]['scenes'][0]['utterances']=[{'line_id':'n','kind':'narration','text':'安静。'}]
    empty=visual_planner_schema(delivery)['properties']['scenes']['prefixItems'][0]['properties']['actions']
    assert empty['maxItems']==0 and 'prefixItems' not in empty


@pytest.mark.parametrize("change,error", [("actor", "VISUAL_ACTION_ACTOR_INVALID"),
    ("quote", "VISUAL_EVIDENCE_INVALID"), ("unsupported", "VISUAL_ACTION_UNSUPPORTED"),
    ("coverage", "VISUAL_ACTION_COVERAGE_MISMATCH"), ("speaker", "VISUAL_SPEAKER_NOT_PRESENT"),
    ("location", "VISUAL_LOCATION_CHANGED"), ("version", "SOURCE_VERSION_MISMATCH"),
    ("present", "VISUAL_ALREADY_PRESENT")])
def test_visual_context_rejects_untraceable_or_unexecutable_choices(change, error):
    delivery, context = sample()
    scene = context["scenes"][0]
    if change == "actor": scene["actions"][0]["actor_role_ids"] = ["b"]
    if change == "quote": scene["presence_evidence"][0]["quote"] = "invented"
    if change == "unsupported": scene["actions"][0]["action"] = "repair_lamp"
    if change == "coverage": scene["actions"].pop()
    if change == "speaker": delivery["episodes"][0]["scenes"][0]["utterances"][1]["speaker_role_id"] = "b"
    if change == "location": scene["source_location"] = "室内"
    if change == "version": context["source_version_id"] = "other"
    if change == "present": scene["initial_cast"] = ["a"]
    with pytest.raises(ProductionError, match=error):
        validate(delivery, context)
