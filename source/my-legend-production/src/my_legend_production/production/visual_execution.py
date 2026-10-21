"""Compile native actor actions and sample them on the actual audio clock."""
from bisect import bisect_right
from copy import deepcopy
from hashlib import sha256
import math
from types import SimpleNamespace

from .repository import ProductionError
from .visual_contracts import validate_visual_context
from .native_segments import segment_compiler


def identity(prefix, *parts):
    return prefix + sha256("\0".join(parts).encode()).hexdigest()[:32]


def compile_track(delivery, context, timeline, catalog, paths, *, check=None):
    return _compile_on_clock(delivery, context, timeline, catalog, paths, check=check, planning=False)


def compile_preview(delivery, context, catalog, paths, *, check=None):
    """Native geometry preflight on an explicitly estimated clock; no audio is claimed."""
    events = []
    for ep in delivery['episodes']:
        for scene in ep['scenes']:
            for line in scene['utterances']:
                i = len(events)
                events.append(SimpleNamespace(episode_id=ep['episode_id'],scene_id=scene['scene_id'],
                    line_id=line['line_id'],kind=line['kind'],text=line['text'],start_frame=i*48000,
                    end_frame=(i+1)*48000,duration_basis='planning_estimate'))
    clock = SimpleNamespace(source_version_id=context.source_version_id,events=events,sample_rate=48000,
                            total_frames=len(events)*48000,audio_sha256=None)
    track = _compile_on_clock(delivery, context, clock, catalog, paths, check=check, planning=True)
    return {'schema_version':'visual_execution_preview/1.0', 'clock_basis':'planning_estimate',
            'audio_generated':False, 'scene_initial_states':track['scene_initial_states'],
            'effects':track['execution_effects'], 'issues':effect_issues(track['execution_effects'])}


def effect_issues(effects):
    issues = []
    for effect in effects:
        if effect['action'] in ('move','enter','exit') and len({tuple(p) for p in effect['path']}) < 2:
            issues.append({'source':effect['source'], 'code':'VISUAL_ACTION_NO_DISPLACEMENT',
                           'rule':'movement must have a nonzero native path', 'effect':effect})
    return issues


def _compile_on_clock(delivery, context, timeline, catalog, paths, *, check, planning):
    validate_visual_context(context, delivery, supported_actions=catalog["capabilities"]["actions"])
    if context.source_version_id != timeline.source_version_id or context.asset_catalog_sha256 != catalog["catalog_sha256"]:
        raise ProductionError("SOURCE_VERSION_MISMATCH")
    source = [(ep["episode_id"], sc["scene_id"], u) for ep in delivery["episodes"] for sc in ep["scenes"] for u in sc["utterances"]]
    if [(ep, sc, u["line_id"], u["kind"], u["text"]) for ep, sc, u in source] != [
        (e.episode_id, e.scene_id, e.line_id, e.kind, e.text) for e in timeline.events]:
        raise ProductionError("SOURCE_COVERAGE_MISMATCH")
    if any(b.start_frame < a.end_frame for a, b in zip(timeline.events, timeline.events[1:])):
        raise ProductionError("VISUAL_OVERLAPPING_EVENT_TIMES_UNSUPPORTED")
    appearances = {a.role_id: a for a in context.appearances}
    roles = {role: identity("r_", role) for role in appearances}
    names = {c["role_id"]: c.get("canon", {}).get("name", c["role_id"]) for c in delivery["character_snapshot"]}
    actors = [{"role_id": roles[r], "name": names[r], **a.model_dump(include={"template_type", "color_key", "accessory"})}
              for r, a in appearances.items()]
    scenes = {(e["episode_id"], s["scene_id"]): s for e in delivery["episodes"] for s in e["scenes"]}
    times = {(e.episode_id, e.scene_id, e.line_id): e for e in timeline.events}
    groups = []
    # Continuous source scenes compile together so the original evaluator preserves poses.
    for binding in context.scenes:
        if groups and binding.continuity_group_id and groups[-1][-1].continuity_group_id == binding.continuity_group_id:
            groups[-1].append(binding)
        else:
            groups.append([binding])
    compiled, effects, scene_initial_states = [], [], []
    seen_initial_scenes = set()
    with segment_compiler(paths, check=check) as native_compile:
        for group in groups:
            cast = sorted({r for binding in group for r in binding.cast})
            if len(cast) > catalog["capabilities"]["max_scene_cast"]:
                raise ProductionError("VISUAL_CONTINUOUS_CAST_CAPACITY_EXCEEDED")
            if len({s.background_type for s in group}) != 1:
                raise ProductionError("VISUAL_CONTINUITY_BACKGROUND_CHANGED")
            blocks, mappings = [], []
            for binding in group:
                actions = {a.line_id: a for a in binding.actions}
                scene = scenes[(binding.episode_id, binding.scene_id)]
                for line in scene["utterances"]:
                    key = (binding.episode_id, binding.scene_id, line["line_id"])
                    event = times[key]
                    block = {"block_id": identity("b_", *key), "source_ref": ":".join(key)}
                    if line["kind"] == "action":
                        action = actions[line["line_id"]]
                        block.update(type="action", role_id=roles[action.actor_role_ids[0]], action=action.action,
                                     text=line["text"], duration_ms=max(100, round((event.end_frame-event.start_frame)*1000/timeline.sample_rate)))
                        for field in ("target_anchor", "direction", "emotion", "intensity"):
                            value = getattr(action, field)
                            if value is not None:
                                block[field] = value
                        if action.target_role_id:
                            block["target_role_id"] = roles[action.target_role_id]
                    elif line["kind"] == "dialogue" and line["speaker_role_id"] not in binding.voice_only_role_ids:
                        block.update(type="dialogue", role_id=roles[line["speaker_role_id"]], text=line["text"])
                    elif line["kind"] in ("dialogue", "narration"):
                        block.update(type="narration", text=line["text"])
                    else:
                        block.update(type="pause", duration_ms=max(100, round((event.end_frame-event.start_frame)*1000/timeline.sample_rate)))
                    blocks.append(block)
                    mappings.append({"block_id": block["block_id"], "source": list(key), "kind": line["kind"],
                                     "start_frame": event.start_frame, "end_frame": event.end_frame, "duration_basis": event.duration_basis})
            document = {"schema_version": "2.0.0", "document_id": identity("d_", delivery["story_version_id"], str(len(compiled))),
                "source_version_id": delivery["story_version_id"], "revision": 1, "title": delivery.get("title", "视觉作品"),
                "timebase": 1000, "timing_mode": "visual_estimated", "style_pack_id": "basic_pixel", "style_pack_version": "1.0.0",
                "subtitle_enabled": True, "characters": actors,
                "scenes": [{"scene_id": identity("s_", group[0].episode_id, group[0].scene_id), "name": "源场景",
                            "background_type": group[0].background_type, "cast": [roles[r] for r in cast],
                            "initial_hidden_roles": [roles[r] for r in cast if r not in group[0].initial_cast], "blocks": blocks}]}
            offset, carried = 0, None
            while offset < len(blocks):
                end = min(len(blocks), offset + catalog["capabilities"]["max_events"])
                # Bound both measured source span and native estimated duration.
                while end > offset and (mappings[end-1]["end_frame"] - mappings[offset]["start_frame"]) * 1000 > catalog["capabilities"]["max_duration_ms"] * timeline.sample_rate:
                    end -= 1
                if end == offset:
                    raise ProductionError("VISUAL_SINGLE_EVENT_CAPACITY_EXCEEDED")
                while True:
                    segment = deepcopy(document)
                    segment["document_id"] = identity("d_", delivery["story_version_id"], str(len(compiled)))
                    scene = segment["scenes"][0]
                    scene["blocks"] = blocks[offset:end]
                    if carried is not None:
                        scene["initial_hidden_roles"] = [r for r, pose in carried.items() if not pose["visible"]]
                    try:
                        plan = native_compile(segment, carried)
                        break
                    except ProductionError as exc:
                        if exc.code != "VISUAL_NATIVE_CAPACITY_EXCEEDED":
                            raise
                        if end - offset == 1:
                            raise ProductionError("VISUAL_SINGLE_EVENT_CAPACITY_EXCEEDED") from None
                        end = offset + (end-offset)//2
                initial = plan["manifest"]["scenes"][0]["initial"]
                if carried is not None and initial != carried:
                    raise ProductionError("VISUAL_CONTINUATION_STATE_MISMATCH")
                subset = mappings[offset:end]
                native_blocks = {b["block"]["block_id"]: b for s in plan["manifest"]["scenes"] for b in s["blocks"]}
                if set(native_blocks) != {m["block_id"] for m in subset}:
                    raise ProductionError("VISUAL_NATIVE_COVERAGE_MISMATCH")
                before_state = initial
                for mapping in subset:
                    block = native_blocks[mapping["block_id"]]
                    source_scene = tuple(mapping['source'][:2])
                    if source_scene not in seen_initial_scenes:
                        seen_initial_scenes.add(source_scene)
                        scene_initial_states.append({'source_scene':list(source_scene),
                            'actors':{r:{'position':before_state[v]['position'],'visible':before_state[v]['visible'],
                                         'direction':before_state[v]['direction']} for r,v in roles.items() if v in before_state}})
                    mapping.update(native_start_ms=block["start_ms"], native_end_ms=block["end_ms"])
                    after_state = {p['role_id']:p for p in plan['block_end_frames'][mapping['block_id']]['actors']}
                    if mapping['kind'] == 'action':
                        actor_id = block['block']['role_id']
                        effect = {'source':mapping['source'], 'action':block['block']['action'],
                                  'actor_role_id':next(r for r,v in roles.items() if v==actor_id),
                                  'before':before_state[actor_id], 'after':after_state[actor_id],
                                  'path':block.get('path', []), 'distances_to_roles':{}}
                        for r, native_id in roles.items():
                            if native_id != actor_id and native_id in before_state and before_state[native_id]['visible']:
                                effect['distances_to_roles'][r] = {
                                    'before':math.dist(before_state[actor_id]['position'], before_state[native_id]['position']),
                                    'after':math.dist(after_state[actor_id]['position'], after_state[native_id]['position'])}
                        effects.append(effect)
                    before_state = after_state
                compiled.append({"plan": plan, "mappings": subset, "continued": carried is not None})
                carried = plan["final_state"]
                offset = end
    # Sample all segments against one global clock, avoiding concatenation rounding drift.
    rows = [(mapping, item["plan"]) for item in compiled for mapping in item["mappings"]]
    starts = [m["start_frame"] for m, _ in rows]
    frames = []
    for n in range(math.ceil(timeline.total_frames / timeline.sample_rate * 10) + 1):
        if check and n % 100 == 0:
            check()
        sample = min(timeline.total_frames - 1, round(n * timeline.sample_rate / 10))
        mapping, plan = rows[max(0, bisect_right(starts, sample) - 1)]
        fraction = min(1, max(0, (sample - mapping["start_frame"]) / (mapping["end_frame"] - mapping["start_frame"])))
        native_time = mapping["native_start_ms"] + fraction * (mapping["native_end_ms"] - mapping["native_start_ms"])
        frame_times = [f["time_ms"] for f in plan["frames"]]
        if sample >= mapping["end_frame"]:
            frame = deepcopy(plan["block_end_frames"][mapping["block_id"]])
        elif sample < mapping["start_frame"]:
            frame = deepcopy(plan["frames"][0])
            frame["fade"] = 0.0
        else:
            frame = deepcopy(plan["frames"][max(0, bisect_right(frame_times, native_time) - 1)])
        frame["time_ms"] = n * 100
        frames.append(frame)
    if not planning and effect_issues(effects):
        raise ProductionError('VISUAL_ACTION_NO_DISPLACEMENT')
    return {"schema_version": "production_visual_preview/1.0" if planning else "production_visual_track/1.0", "source_version_id": timeline.source_version_id,
        "audio_sha256": timeline.audio_sha256, "catalog_sha256": context.asset_catalog_sha256,
        "document": {"characters": actors}, "manifest": {"duration_ms": timeline.total_frames * 1000 / timeline.sample_rate},
        "sample_rate": timeline.sample_rate, "total_frames": timeline.total_frames,
        "frames": frames, "mapping": [m for m, _ in rows], "native_plan_hashes": [c["plan"]["plan_hash"] for c in compiled],
        "segments": [{"plan_hash": c["plan"]["plan_hash"], "continued": c["continued"],
            "first_source": c["mappings"][0]["source"], "last_source": c["mappings"][-1]["source"],
            "native_duration_ms": c["plan"]["manifest"]["duration_ms"], "event_count": len(c["mappings"]),
            "initial_state": c["plan"]["manifest"]["scenes"][0]["initial"], "final_state": c["plan"]["final_state"],
            "engine": c["plan"]["continuation_engine"]} for c in compiled],
        "execution_effects": effects,
        "scene_initial_states": scene_initial_states,
        "timing_mode": "planning_estimate" if planning else "measured_audio_with_explicit_action_holds", "human_review": "pending"}
