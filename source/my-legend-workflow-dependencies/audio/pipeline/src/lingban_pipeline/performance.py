from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from .adapters import StructuredTextModel
from .contracts import PerformanceDocument, ProductionTask
from .io import digest, file_hash, load, save
from .registry import CapabilityError
from .instructions import policy, validate_instruction, weighted_length

TEMPLATE_VERSION = "performance-four-levels/4-flexible-length"
FIELDS = {"intent", "entry", "turn", "release", "close", "intensity", "emphasis", "pauses", "delivery"}


def design(script: dict, config: dict, model=None, line_store=None):
    project = config.get("project", {})
    roles = {r["role_id"]: r | {"voice": config.get("role_voices", {}).get(r["role_id"])} for r in script["roles"]}
    scenes = {s["scene_id"]: config.get("scene_defaults", {}) | s["performance"] | config.get("scene_overrides", {}).get(s["scene_id"], {}) | {"description": s["description"]} for s in script["scenes"]}
    events = script["events"]
    lines, sounds, assumptions = {}, {}, []
    generation_record = {"mode": "provided_directions", "model_called": False}
    for index, e in enumerate(events):
        if e["kind"] == "speech":
            # Context is limited and explicit. A line change invalidates actual dependent prompts,
            # not every unrelated scene or media task in the project.
            context = [{"block_id": other["block_id"], "kind": other["kind"], "text": other["text"], "role_id": other["role_id"]} for other in events[max(0, index - config.get("context_window", 1)):index + config.get("context_window", 1) + 1] if other["scene_id"] == e["scene_id"]]
            layer = {"intent": "", "entry": "", "turn": "", "release": "", "close": "", "intensity": "", "emphasis": [], "pauses": [], "delivery": ""} | e["performance"]
            if layer.get("stress") and not layer.get("emphasis"):
                layer["emphasis"] = [item if isinstance(item, str) else item["span_text"] for item in layer["stress"]]
            if layer.get("emotion_arc") and not layer.get("release"):
                layer["release"] = layer["emotion_arc"]
            lines[e["block_id"]] = {"text": e["text"], "role_id": e["role_id"], "scene_id": e["scene_id"], "context": context, "performance": layer}
            if not any(layer[k] for k in ("intent", "entry", "release")):
                assumptions.append({"block_id": e["block_id"], "code": "performance_unspecified", "message": "Use source directions and project style for draft; emotional trajectory needs listening review"})
        if e["kind"] in {"sfx", "ambience", "music"}:
            sounds[e["block_id"]] = {"scene_id": e["scene_id"], "requirements": e["sound"], "description": e["text"]}
    if config.get("text_model", {}).get("enabled"):
        designer = model or StructuredTextModel(config["text_model"])
        produced, records = {}, {}
        for block_id, original in lines.items():
            limits = policy(config, original["performance"])
            related_roles = {original["role_id"]} | {item["role_id"] for item in original["context"] if item["role_id"]}
            inputs = {"template_version": TEMPLATE_VERSION, "project": project,
                      "roles": {key: {k: v for k, v in roles[key].items() if k != "voice"} for key in sorted(related_roles)},
                      "scenes": {original["scene_id"]: scenes[original["scene_id"]]},
                      "lines": {block_id: original}, "instruction_policy": limits}
            advice = limits["recommended_han_range"]
            guidance = f"朗读指令建议写{advice[0]}到{advice[1]}个汉字。该范围只作建议，简单台词可以更短，必要时可以更长；优先保留表演信息，不凑字数。超过当前加权阈值退回重写，程序不截断。"
            prompt = json.dumps({"task": "Design the ONE supplied line only. Never rewrite spoken text, IDs, roles, context, or plot facts. Return exactly {lines: {block_id: performance}}. Performance must contain intent, entry, turn, release, close, intensity, emphasis, pauses, delivery AND a nonempty instructions string. Instructions must preserve project/role/scene style and this line's intent, emotional progression, emphasis and necessary pauses, within the supplied configurable weighted budget. The Han-character range is advisory, not a minimum or maximum validation rule. Do not fill the budget with redundant words. Han characters count 2, all others count 1. Emphasis is an array of literal substrings. Keep narration identity, perspective, read_aloud, display and source_kind unchanged. Media effects are post-processing, not spoken directions.", "instruction_length_guidance": guidance, **inputs}, ensure_ascii=False)
            def validate(value):
                if set(value) != {"lines"} or set(value["lines"]) != {block_id}:
                    raise ValueError("Line set changed")
                line = value["lines"][block_id]
                # Accept the legacy full-line response only if every locked
                # source property is identical. New responses contain editable
                # performance data only; source data is copied programmatically.
                if "text" in line or "role_id" in line or "context" in line or "scene_id" in line:
                    for key in ("text", "role_id", "scene_id", "context"):
                        if line.get(key) != original[key]:
                            raise ValueError("Source or context changed")
                    proposed = line["performance"]
                elif set(line) == {"performance"}:
                    proposed = line["performance"]
                else:
                    proposed = line
                if not isinstance(proposed, dict) or not FIELDS <= set(proposed):
                    raise ValueError("Missing performance fields")
                if not isinstance(proposed["emphasis"], list) or not isinstance(proposed["pauses"], list):
                    raise ValueError("Invalid emphasis")
                if any(not isinstance(word, str) or word not in original["text"] for word in proposed["emphasis"]):
                    raise ValueError("Emphasis not in text")
                if not isinstance(proposed.get("instructions"), str) or not proposed["instructions"].strip():
                    raise ValueError("Invalid instructions")
                merged = original["performance"] | proposed
                for locked in ("source_kind", "perspective", "read_aloud", "display", "narration_purpose"):
                    if locked in original["performance"]:
                        merged[locked] = original["performance"][locked]
                instruction = proposed["instructions"]
                merged["instructions"] = validate_instruction(instruction, limits["max_weight"])
                merged["instruction_origin"] = "model"
                result = deepcopy(original)
                result["performance"] = merged
                result["instruction_policy"] = limits
                result["instruction_weight"] = weighted_length(instruction)
                return result
            def action():
                line = designer.generate(prompt, validate)
                return {"line": line, "generation_record": {"initial_prompt": prompt, **deepcopy(getattr(designer, "last_request_record", {}))}}
            fingerprint = digest({"input": inputs, "model_config": config["text_model"]})
            result = line_store(block_id, fingerprint, action) if line_store else action() | {"cache_reused": False}
            candidate = result["line"]
            if any(candidate.get(key) != original[key] for key in ("text", "role_id", "scene_id", "context")):
                raise ValueError("Source or context changed")
            validate_instruction(candidate["performance"]["instructions"], limits["max_weight"])
            # Recovered results must pass the same structural/source checks as
            # freshly generated output, not just a file hash and length check.
            validate({"lines": {block_id: candidate}})
            produced[block_id] = candidate
            records[block_id] = result["generation_record"] | {"cache_reused": result["cache_reused"], "input_fingerprint": fingerprint}
        lines = produced
        generation_record = {"mode": "structured_per_line", "model_called": any(not r["cache_reused"] for r in records.values()), "lines": records}
    return PerformanceDocument(source_version_id=script["source_version_id"], script_sha256=digest(script), artifact_revision=script["artifact_revision"], template_version=TEMPLATE_VERSION,
                               project=project, roles=roles, scenes=scenes, lines=lines, sounds=sounds,
                               visual_semantics=[{k: e[k] for k in ("block_id", "scene_id", "role_id", "target", "text", "performance")} for e in events if e["kind"] == "action"],
                               timing_constraints=[{"block_id": e["block_id"], "relations": e["timing"]} for e in events if e["timing"]], assumptions=assumptions, generation_record=generation_record).model_dump()


def voice_instruction(line: dict, performance: dict):
    p = line["performance"]
    if p.get("instructions"):
        return p["instructions"]
    role = performance["roles"].get(line["role_id"], {})
    scene = performance["scenes"][line["scene_id"]]
    parts = [performance["project"].get("performance", ""), role.get("description", ""),
             scene.get("performance", ""), p.get("intent", ""), p.get("emotion_arc", "")]
    parts += [p.get(key, "") for key in ("entry", "turn", "release", "close", "intensity", "delivery")]
    if p.get("emphasis"):
        parts.append("重音：" + "、".join(p["emphasis"]))
    if p.get("pauses"):
        parts.append("停顿：" + json.dumps(p["pauses"], ensure_ascii=False, separators=(",", ":")))
    return "；".join(dict.fromkeys(str(x) for x in parts if x)) or "自然交流，保持角色声音一致。"


def design_input_fingerprint(script: dict, config: dict):
    """Only data actually read by the performance model invalidates its result."""
    base = design(script, config | {"text_model": {"enabled": False}})
    return digest({"template": TEMPLATE_VERSION, "input": {key: base[key] for key in ("project", "roles", "scenes", "lines")}, "model_config": config.get("text_model", {}), "policy": policy(config)})


def reuse_design(script: dict, config: dict, previous: dict):
    base = design(script, config | {"text_model": {"enabled": False}})
    if previous["template_version"] != TEMPLATE_VERSION or set(base["lines"]) != set(previous["lines"]):
        raise ValueError("Performance reuse has incompatible inputs")
    for key, line in base["lines"].items():
        if any(line[field] != previous["lines"][key][field] for field in ("text", "role_id", "scene_id", "context")):
            raise ValueError("Performance reuse changed source or context")
    base["lines"] = deepcopy(previous["lines"])
    base["assumptions"] = deepcopy(previous["assumptions"])
    base["generation_record"] = deepcopy(previous["generation_record"]) | {"reuse_reason": "unchanged_performance_inputs", "reused_from_script_sha256": previous["script_sha256"]}
    return PerformanceDocument.model_validate(base).model_dump()


def sound_prompt(e: dict):
    if e["sound"].get("prompt"):
        return e["sound"]["prompt"]
    requirement = dict(description=e["text"], **e["sound"])
    return "生成独立的声音素材，不朗读文字说明或拟声词。声音需求：" + json.dumps(requirement, ensure_ascii=False) + "。只包含所需声源，排除人物台词、旁白和未要求的音乐或背景。"


def create_tasks(script: dict, performance: dict, config: dict, input_root: Path):
    tasks = []
    for e in script["events"]:
        if e["kind"] not in {"speech", "sfx", "ambience", "music"}:
            continue
        provider = config.get("tts" if e["kind"] == "speech" else "sfx", {})
        media = deepcopy(e["media"])
        render = media.get("render", e["performance"].get("render", "dialogue" if e["kind"] == "speech" else e["kind"]))
        adapter = media.get("strategy", "local" if media.get("file") else provider.get("adapter", "local"))
        if adapter == "library":
            adapter = "local"
        for item in [media] + media.get("layers", []):
            if item.get("file"):
                p = (input_root / item["file"]).resolve()
                if not p.exists():
                    raise CapabilityError(f"{e['block_id']}: local media missing")
                item["file"] = str(p)
                item["sha256"] = file_hash(p)
        payload = dict(media=media, keypoints=e["keypoints"], processing=config.get("processing_profiles", {}).get(render, {}) | media.get("processing", {}), render=render, provider=provider,
                       sample_rate=config.get("sample_rate", 48000), channels=config.get("channels", 1), processing_defaults=config.get("processing", {}))
        dependencies = [e["block_id"]]
        if e["kind"] == "speech":
            line = performance["lines"][e["block_id"]]
            voice = performance["roles"][e["role_id"]].get("voice")
            catalog = config.get("voice_catalog", [])
            if adapter != "local" and (not voice or catalog and voice not in catalog):
                raise CapabilityError(f"{e['block_id']}: voice missing from configured catalog")
            maximum = policy(config, e["performance"])["max_weight"]
            instruction = voice_instruction(line, performance)
            if adapter in {"qianwen_audio_tts", "qianwen_tts"} and (adapter == "qianwen_audio_tts" or "instruct" in provider.get("model", "")):
                try:
                    validate_instruction(instruction, maximum)
                except ValueError as exc:
                    raise CapabilityError("Delivery instruction requires bounded compaction before TTS") from exc
            payload.update(text=e["text"], voice=voice, instructions=instruction, instruction_max_weight=maximum,
                           instruction_weight=weighted_length(instruction), context=line["context"], required_controls=e["performance"].get("required_controls", []))
            dependencies.extend(c["block_id"] for c in line["context"])
        else:
            if e["kind"] == "music" and adapter not in {"local", "compose"}:
                raise CapabilityError("Music requires an existing asset or composition recipe")
            payload.update(prompt=sound_prompt(e), seed=media.get("seed", config.get("seed", 42)), requirements=e["sound"])
        candidate = media.get("candidate_version", 1)
        identity = digest({"block_id": e["block_id"], "kind": e["kind"], "payload": payload, "template": performance["template_version"], "adapter": adapter, "candidate": candidate})
        tasks.append(ProductionTask(task_id="task_" + identity, block_id=e["block_id"], kind=e["kind"], source_version_id=script["source_version_id"], artifact_revision=script["artifact_revision"], input_sha256=identity, candidate_version=candidate, adapter=adapter, payload=payload, dependencies=sorted(set(dependencies))).model_dump())
    return tasks
