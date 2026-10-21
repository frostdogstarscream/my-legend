from __future__ import annotations

from .contracts import Timeline
from .io import digest


def schedule(script: dict, assets: dict, config: dict, visual: dict):
    rate = config.get("sample_rate", 48000)
    items, constraints, issues = {}, {}, []
    previous = None
    required_points = set()
    for e in script["events"]:
        block_id, kind = e["block_id"], e["kind"]
        asset = assets.get(block_id)
        action = visual.get("actions", {}).get(block_id)
        if asset:
            info = asset["processed"]
            if info["sample_rate"] != rate:
                raise ValueError("Media uses a different timeline sample rate")
            duration, basis, points = info["frames"], "measured", asset["keypoints"]
        elif action:
            duration, basis, points = round(action["duration_ms"] * rate / 1000), action["duration_basis"], action["keypoints"]
            if basis == "external_locked":
                issues.append(dict(block_id=block_id, code="external_duration_confirmation_required", severity="review", confirmation_id="visual:" + block_id + ":external_duration"))
        else:
            duration, basis, points = round((e.get("duration_ms") or 0) * rate / 1000), "planned", e["keypoints"]
            if kind in {"speech", "sfx", "ambience", "music"}:
                issues.append(dict(block_id=block_id, code="missing_asset", severity="blocking"))
            if kind in {"pause", "transition"} and e.get("duration_ms") is None:
                issues.append(dict(block_id=block_id, code="missing_interval_duration", severity="blocking"))
        point_map = {}
        for p in points:
            frame = round(p["frame"] * rate / p["sample_rate"])
            if p["name"] in point_map or frame >= duration:
                issues.append(dict(block_id=block_id, code="invalid_keypoint", point=p["name"], severity="blocking"))
            point_map[p["name"]] = dict(p, frame=frame)
        items[block_id] = dict(block_id=block_id, kind=kind, duration=duration, duration_basis=basis, points=point_map,
                               text=e["text"], role_id=e["role_id"], scene_id=e["scene_id"], asset=asset)
        rels = list(e["timing"])
        if not rels:
            gap = config.get("scene_overrides", {}).get(e["scene_id"], {}).get("gap_ms", config.get("scene_defaults", {}).get("gap_ms", config.get("gap_ms", 0)))
            rels = [dict(anchor=previous, anchor_point="end", self_point="start", offset_ms=gap if previous else 0, mode="equal", provenance="project_default")]
        constraints[block_id] = rels
        # Default sequence progresses through all audible intervals, but not overlay anchors/actions.
        if kind not in {"action", "ambience", "music"}:
            previous = block_id

    def point(event_id, name):
        if name == "start":
            return 0
        if name == "end":
            return items[event_id]["duration"]
        value = items[event_id]["points"].get(name)
        if not value:
            issues.append(dict(block_id=event_id, code="missing_word_position" if name.startswith("word:") else "missing_keypoint", point=name, severity="blocking"))
            return 0  # Draft fallback remains visibly blocked for release.
        if value["status"] != "confirmed":
            key = event_id + ":" + name
            required_points.add(key)
            issues.append(dict(block_id=event_id, code="keypoint_confirmation_required", point=name, confirmation_id=key, severity="review"))
        return value["frame"]

    positions, visiting = {}, set()
    def locate(event_id):
        if event_id in positions:
            return positions[event_id]
        if event_id in visiting:
            raise ValueError("Timing dependency cycle at " + event_id)
        visiting.add(event_id)
        equal, after = [], []
        for relation in constraints[event_id]:
            anchor = relation["anchor"]
            offset = round(relation.get("offset_ms", 0) * rate / 1000)
            desired = (locate(anchor) + point(anchor, relation.get("anchor_point", "end")) if anchor else 0) + offset - point(event_id, relation.get("self_point", "start"))
            (after if relation.get("mode") == "after" else equal).append(desired)
        value = equal[0] if equal else max([0] + after)
        if any(x != value for x in equal) or any(x > value for x in after):
            issues.append(dict(block_id=event_id, code="timing_constraint_conflict", equality_frames=equal, lower_bound_frames=after, severity="blocking"))
        if value < 0:
            issues.append(dict(block_id=event_id, code="negative_start", requested_frame=value, severity="blocking"))
            value = 0
        visiting.remove(event_id)
        positions[event_id] = value
        return value
    for event_id in items:
        try:
            locate(event_id)
        except ValueError as exc:
            issues.append(dict(block_id=event_id, code="dependency_cycle", severity="blocking", message=str(exc)))
            visiting.clear()
            positions[event_id] = 0
    placements, subtitles, checks = [], [], []
    for e in script["events"]:
        item = items[e["block_id"]]
        start = positions[e["block_id"]]
        end = start + item["duration"]
        placement = {k: item[k] for k in ("block_id", "kind", "role_id", "scene_id", "text", "duration_basis")}
        placement.update(start_frame=start, end_frame=end, keypoints=list(item["points"].values()))
        placement["render"] = e["media"].get("render", e["performance"].get("render", "dialogue" if e["kind"] == "speech" else e["kind"]))
        if item["asset"]:
            placement.update(asset_id=item["asset"]["asset_id"], asset_revision=item["asset"]["artifact_revision"], task_id=item["asset"]["task_id"], file=item["asset"]["processed"]["file"], sha256=item["asset"]["processed"]["sha256"])
        placements.append(placement)
        if item["kind"] == "speech":
            subtitles.append(dict(block_id=e["block_id"], start_frame=start, end_frame=end, text=e["text"]))
        for rel in constraints[e["block_id"]]:
            target = (positions[rel["anchor"]] + point(rel["anchor"], rel.get("anchor_point", "end")) if rel["anchor"] else 0) + round(rel.get("offset_ms", 0) * rate / 1000)
            actual = start + point(e["block_id"], rel.get("self_point", "start"))
            checks.append(dict(block_id=e["block_id"], relation=rel, target_frame=target, actual_frame=actual, satisfied=(actual >= target if rel.get("mode") == "after" else actual == target)))
    total = max((p["end_frame"] for p in placements), default=0)
    tail_ms = config.get("tail_ms", 0)
    if not isinstance(tail_ms, (int, float)) or isinstance(tail_ms, bool) or tail_ms < 0:
        raise ValueError("Invalid explicit tail duration")
    tail_frames = round(tail_ms * rate / 1000)
    total += tail_frames
    budget_ms = config.get("budget_ms")
    budget = {"requested_ms": budget_ms, "actual_ms": total * 1000 / rate, "excess_ms": max(0, total * 1000 / rate - budget_ms) if budget_ms is not None else None}
    if budget["excess_ms"]:
        issues.append(dict(code="budget_exceeded", severity="warning", excess_ms=budget["excess_ms"]))
    # Same issue may be consulted when resolving positions and generating check records.
    issues = list({digest(i): i for i in issues}.values())
    # Evidence about an execution references this version; it cannot itself define
    # the version or the next feedback would create a recursive fingerprint.
    visual_geometry = {k: visual.get(k) for k in ("actions", "status")}
    identity = dict(source_version_id=script["source_version_id"], script_sha256=digest(script), sample_rate=rate, total_frames=total, tail_frames=tail_frames, placements=placements, constraints=checks, visual=visual_geometry)
    return Timeline(timeline_version="tl_" + digest(identity)[:24], source_version_id=script["source_version_id"], script_sha256=digest(script), sample_rate=rate, total_frames=total, placements=placements, subtitles=subtitles, constraints=checks, issues=issues, budget=budget, visual=visual).model_dump()
