import json
from pathlib import Path
import pytest

from my_legend_production.production.visual_catalog import catalog_from_paths
from my_legend_production.production.visual_contracts import VisualContext
from my_legend_production.production.visual_execution import compile_track, compile_preview
from my_legend_production.production.timeline import MeasuredTimeline
from test_production_visual_context import sample
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.native_segments import segment_compiler


def execute(lines, actions, *, max_events=None, durations=None, gaps=None):
    workspace = Path(__file__).resolve().parents[3]
    paths_file = workspace / "runtime/my-legend-production/workflows.json"
    catalog = catalog_from_paths(paths_file, "pixel_performance/1")
    if max_events is not None:
        catalog["capabilities"]["max_events"] = max_events
    delivery, raw = sample()
    raw["asset_catalog_sha256"] = catalog["catalog_sha256"]
    delivery["episodes"][0]["scenes"][0]["utterances"] = lines
    raw["scenes"][0]["actions"] = actions
    raw["scenes"][0]["presence_evidence"] = [{"episode_id": "ep", "scene_id": "room", "line_id": lines[0]["line_id"], "quote": lines[0]["text"]}]
    events, frame = [], 0
    for i, line in enumerate(lines):
        frame += (gaps or [0] * len(lines))[i] * 48000
        duration = (durations or [1] * len(lines))[i] * 48000
        events.append({"sequence": i+1, "episode_id": "ep", "scene_id": "room", "line_id": line["line_id"],
            "kind": line["kind"], "text": line["text"], "identity_id": line.get("speaker_role_id"),
            "start_frame": frame, "end_frame": frame+duration,
            "duration_basis": "planned_silent_hold" if line["kind"] == "action" else "measured_audio", "source_audio_sha256": "a"*64})
        frame += duration
    timing = MeasuredTimeline.model_validate({"source_version_id": "v", "sample_rate": 48000, "channels": 1,
        "total_frames": frame, "audio_sha256": "b"*64, "events": events})
    return compile_track(delivery, VisualContext.model_validate(raw), timing, catalog,
                         json.loads(paths_file.read_text(encoding="utf-8")))


def action(line, name, **fields):
    return {"line_id": line["line_id"], "actor_role_ids": ["a"], "action": name,
        "evidence": [{"episode_id": "ep", "scene_id": "room", "line_id": line["line_id"], "quote": line["text"]}], **fields}


def test_native_compiler_executes_enter_exit_on_measured_clock():
    workspace = Path(__file__).resolve().parents[3]
    paths_file = workspace / "runtime/my-legend-production/workflows.json"
    catalog = catalog_from_paths(paths_file, "pixel_performance/1")
    delivery, raw = sample()
    raw["asset_catalog_sha256"] = catalog["catalog_sha256"]
    events = []
    for i, line in enumerate(delivery["episodes"][0]["scenes"][0]["utterances"]):
        events.append({"sequence": i+1, "episode_id": "ep", "scene_id": "room", "line_id": line["line_id"],
            "kind": line["kind"], "text": line["text"], "identity_id": line.get("speaker_role_id"),
            "start_frame": i*48000, "end_frame": (i+1)*48000,
            "duration_basis": "planned_silent_hold" if line["kind"] == "action" else "measured_audio", "source_audio_sha256": "a"*64})
    timeline = MeasuredTimeline.model_validate({"source_version_id": "v", "sample_rate": 48000, "channels": 1,
        "total_frames": 144000, "audio_sha256": "b"*64, "events": events})
    track = compile_track(delivery, VisualContext.model_validate(raw), timeline, catalog,
                          json.loads(paths_file.read_text(encoding="utf-8")))
    assert len(track["frames"]) == 31 and track["manifest"]["duration_ms"] == 3000
    assert [m["source"][-1] for m in track["mapping"]] == ["l1", "l2", "l3"]
    assert {f["background"] for f in track["frames"]} == {"courtyard"}
    actor = lambda index: track["frames"][index]["actors"][0]
    assert actor(12)["visible"]
    assert actor(2)["position"] != actor(8)["position"]
    assert actor(22)["position"] != actor(28)["position"]
    assert track["human_review"] == "pending"


def test_segment_boundaries_preserve_position_facing_hidden_state_and_reentry():
    lines = [{"line_id": f"l{i+1}", "kind": kind, "text": text} for i, (kind, text) in enumerate([
        ("action", "甲走进院子。"), ("action", "甲移到远处。"), ("action", "甲转向左侧。"),
        ("narration", "院子很安静。"), ("action", "甲离开院子。"), ("narration", "院子空了。"),
        ("action", "甲再次走进院子。")])]
    actions = [action(lines[0], "enter"), action(lines[1], "move", target_anchor="slot_3"),
        action(lines[2], "face", direction="left"), action(lines[4], "exit"), action(lines[6], "enter")]
    track = execute(lines, actions, max_events=2)
    segments = track["segments"]
    assert len(segments) == 4
    assert not segments[0]["continued"] and all(s["continued"] for s in segments[1:])
    for previous, current in zip(segments, segments[1:]):
        assert previous["final_state"] == current["initial_state"]
    pose = lambda state: next(iter(state.values()))
    assert pose(segments[1]["initial_state"])["position"] == [192.0, 116.0]
    assert pose(segments[2]["initial_state"])["direction"] == "left"
    assert pose(segments[3]["initial_state"])["visible"] is False
    assert pose(segments[3]["final_state"])["visible"] is True
    assert track["frames"][35]["actors"][0]["position"] == [192.0, 116.0]
    assert track["frames"][55]["actors"][0]["visible"] is False
    assert [m["source"][-1] for m in track["mapping"]] == [line["line_id"] for line in lines]


def test_long_continuous_scene_splits_without_relaxing_native_limits_or_losing_events():
    enter = {"line_id": "l1", "kind": "action", "text": "甲走进院子。"}
    lines = [enter] + [{"line_id": f"n{i}", "kind": "narration", "text": "风吹过院子。"} for i in range(105)]
    leave = {"line_id": "leave", "kind": "action", "text": "甲离开院子。"}
    lines.append(leave)
    track = execute(lines, [action(enter, "enter"), action(leave, "exit")])
    assert len(track["segments"]) >= 3
    assert len(track["mapping"]) == len(lines) == 107
    assert len({m["block_id"] for m in track["mapping"]}) == len(lines)
    assert all(s["native_duration_ms"] <= 90000 and s["event_count"] <= 100 for s in track["segments"])
    assert track["total_frames"] == 107*48000
    assert track["frames"][-1]["time_ms"] == 107000
    for previous, current in zip(track["segments"], track["segments"][1:]):
        assert previous["final_state"] == current["initial_state"]


def test_unsegmentable_atomic_event_fails_instead_of_truncating():
    enter = {"line_id": "l1", "kind": "action", "text": "甲走进院子。"}
    with pytest.raises(ProductionError, match="VISUAL_SINGLE_EVENT_CAPACITY_EXCEEDED"):
        execute([enter], [action(enter, "enter")], durations=[91])


def test_audio_gaps_hold_completed_state_without_starting_future_actions():
    lines = [{"line_id": "l1", "kind": "action", "text": "甲走进院子。"},
             {"line_id": "l2", "kind": "action", "text": "甲离开院子。"}]
    track = execute(lines, [action(lines[0], "enter"), action(lines[1], "exit")], gaps=[1, 2])
    initial = track["frames"][5]["actors"][0]
    assert initial["visible"] is False
    gap = track["frames"][25]
    assert gap["actors"][0]["position"] == [72.0, 108.0]
    assert gap["actors"][0]["visible"] is True
    assert gap["actors"][0]["walk_frame"] == 0
    assert gap["block_id"] is None and gap["subtitle"] is None
    assert track["frames"][45]["actors"][0]["position"] != [72.0, 108.0]


def test_native_extension_refuses_changed_dependency_before_compilation(tmp_path):
    source = tmp_path / "pixel-story-mobile/packages/story_player/lib/src"
    source.mkdir(parents=True)
    (source / "compiler.dart").write_text("changed native rules", encoding="utf-8")
    with pytest.raises(ProductionError, match="VISUAL_NATIVE_EXTENSION_SOURCE_CHANGED"):
        with segment_compiler({"visual_root": str(tmp_path)}):
            pytest.fail("changed dependency must not execute")


def test_native_move_without_displacement_cannot_publish_a_track():
    lines = [{'line_id':'l1','kind':'action','text':'甲进入。'}, {'line_id':'l2','kind':'action','text':'甲移动。'}]
    with pytest.raises(ProductionError, match='VISUAL_ACTION_NO_DISPLACEMENT'):
        execute(lines,[action(lines[0],'enter'),action(lines[1],'move',target_anchor='slot_1')])


def test_exit_after_reaching_threshold_walks_offstage_and_reentry_survives_segmentation():
    lines=[{'line_id':f'l{i+1}','kind':kind,'text':text} for i,(kind,text) in enumerate([
        ('action','甲进入。'),('action','甲走到出口。'),('action','甲走出院门。'),
        ('narration','院子空了。'),('action','甲再次进入。')])]
    actions=[action(lines[0],'enter'),action(lines[1],'move',target_anchor='exit'),
             action(lines[2],'exit'),action(lines[4],'enter')]
    track=execute(lines,actions,max_events=1)
    leaving=next(e for e in track['execution_effects'] if e['action']=='exit')
    assert leaving['before']['position']==[308,108] and leaving['before']['visible']
    assert leaving['after']['position']==[340,108] and not leaving['after']['visible']
    assert track['frames'][22]['actors'][0]['position'][0] < track['frames'][28]['actors'][0]['position'][0]
    assert not track['frames'][35]['actors'][0]['visible']
    assert track['frames'][-1]['actors'][0]['visible']
    for previous,current in zip(track['segments'],track['segments'][1:]):
        assert previous['final_state']==current['initial_state']


def test_preflight_uses_native_spacing_and_proves_move_toward_other_actor():
    workspace = Path(__file__).resolve().parents[3]
    paths_file = workspace / 'runtime/my-legend-production/workflows.json'
    catalog = catalog_from_paths(paths_file,'pixel_performance/1')
    delivery, raw = sample()
    raw['asset_catalog_sha256'] = catalog['catalog_sha256']
    raw['appearances'].append({**raw['appearances'][0], 'role_id':'b'})
    scene = raw['scenes'][0]
    scene.update(cast=['a','b'],initial_cast=['b'])
    move = {'line_id':'move','kind':'action','text':'甲走近乙。'}
    delivery['episodes'][0]['scenes'][0]['utterances'].insert(2,move)
    scene['actions'].insert(1,action(move,'move',target_anchor='slot_2'))
    preview = compile_preview(delivery,VisualContext.model_validate(raw),catalog,json.loads(paths_file.read_text()))
    assert preview['clock_basis']=='planning_estimate' and preview['audio_generated'] is False
    assert preview['issues']==[]
    assert preview['scene_initial_states'][0]['actors']['b']['visible'] is True
    assert preview['scene_initial_states'][0]['actors']['a']['visible'] is False
    effect = next(e for e in preview['effects'] if e['action']=='move')
    assert effect['before']['position']==[72,108] and effect['after']['position']==[128,108]
    distance = effect['distances_to_roles']['b']
    assert distance['after'] < distance['before']
    scene['actions'][1]['target_anchor']='slot_1'
    rejected=compile_preview(delivery,VisualContext.model_validate(raw),catalog,json.loads(paths_file.read_text()))
    assert rejected['issues'][0]['code']=='VISUAL_ACTION_NO_DISPLACEMENT'
    assert rejected['issues'][0]['source'][-1]=='move'
