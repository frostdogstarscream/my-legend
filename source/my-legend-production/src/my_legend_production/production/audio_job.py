"""Standalone audio child in the pinned audio environment. Native ledger controls retries."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import time
import sqlite3
from copy import deepcopy
from uuid import uuid4

from ..core import json_bytes, key, require
from ..performance_production import prepare_audio, run_audio, read
from ..worker import save
from .timeline import MeasuredTimeline
from .audio_cache import apply_candidates, export_cache, load_cache, seed_cache


def combine_arranged(root, batches, results, delivery_bytes, plan, *, omitted_operations=()):
    from .audio_arrangement import bind_arrangement, source_key, arrangement_bytes
    from .audio_arrangement_render import compile_arrangement
    compound=plan.schema_version=='audio_arrangement/2.0'
    delivery = bind_arrangement(plan, delivery_bytes)
    assets, covered = {}, []
    for batch, result in zip(batches, results, strict=True):
        directory = (Path(batch['path']) / 'audio-workflow').resolve()
        audible = any(e['kind'] in ('speech','sfx','ambience','music') for e in batch['audio_script']['events'])
        native_assets = {}
        if audible:
            require(result.get('technical_passed') is True or (
                result.get('delivery_partial') is True and omitted_operations), 'AUDIO_NATIVE_TECHNICAL_CHECK_FAILED')
            preview = (directory / result['preview_directory']).resolve()
            require(preview.is_relative_to(directory), 'AUDIO_ASSET_PATH_INVALID')
            native_assets = read(preview / 'assets.json')
        else:
            require(result.get('status') == 'not_required', 'AUDIO_MARKER_ONLY_SCENE_INVALID')
        by_block = {e['block_id']: e for e in batch['audio_script']['events']}
        for coverage in batch['operation_map'] if compound else batch['audio_map']['coverage']:
            source = coverage['source']
            identity = coverage['operation_id'] if compound else tuple(source[k] for k in ('episode_id','scene_id','line_id'))
            covered.append(identity)
            if not compound:
                require(len(coverage['target_block_ids']) == 1, 'AUDIO_MAPPING_NOT_ONE_TO_ONE')
            block = coverage['block_id'] if compound else coverage['target_block_ids'][0]
            if compound and identity in omitted_operations:
                continue
            if by_block[block]['kind'] == 'action':
                require(block not in native_assets and by_block[block].get('duration_ms') == 0, 'AUDIO_MARKER_HAS_ASSET')
                continue
            require(block in native_assets, 'AUDIO_MEASURED_ASSET_MISSING')
            asset = deepcopy(native_assets[block])
            path = (directory / asset['processed']['file']).resolve()
            require(path.is_relative_to(directory), 'AUDIO_ASSET_PATH_INVALID')
            asset['processed']['file'] = path.relative_to(root.resolve()).as_posix()
            assets[identity] = asset
    expected=([op.operation_id for e in plan.events for op in e.operations] if compound else [source_key(e.source) for e in plan.events])
    require(covered == expected, 'SOURCE_COVERAGE_MISMATCH')
    # A fresh local compile attempt is safe on replay; provider ledgers remain untouched.
    destination = root / 'compilations' / uuid4().hex
    if compound:
        from .compound_audio_render import compile_compound
        save(root / 'measured-inputs.json', dict(arrangement_sha256=sha256(arrangement_bytes(plan)).hexdigest(),
            sample_rate=plan.sample_rate, operation_frames={k:a['processed']['frames'] for k,a in assets.items()}))
        timeline,audit=compile_compound(root,destination,delivery_bytes,plan,assets)
    else:
        timeline, audit = compile_arrangement(root, destination, delivery_bytes, plan, assets)
    output = root / 'output';output.mkdir(exist_ok=True)
    for name in ('mix.wav','timeline.json','arrangement.json','compile-report.json'):
        raw = (destination/name).read_bytes()
        target = output/name
        if target.exists():
            require(target.read_bytes() == raw, 'AUDIO_COMPILE_REPLAY_CHANGED')
        else:
            target.write_bytes(raw)
    return dict(status='succeeded',source_version_id=delivery['story_version_id'],
        provider_calls=sum(r.get('call_budget',{}).get('production_calls',0) for r in results),
        audio_sha256=timeline.audio_sha256,timeline_sha256=sha256((output/'timeline.json').read_bytes()).hexdigest(),
        arrangement_sha256=timeline.arrangement_sha256,compile_report_sha256=sha256((output/'compile-report.json').read_bytes()).hexdigest(),
        sample_rate=timeline.sample_rate,channels=timeline.channels,total_frames=timeline.total_frames,
        human_listening='pending',technical_quality='passed',publication='not_requested',
        compilation_directory=destination.relative_to(root).as_posix())


def run_delivery_batch(batch, paths_file, *, audio_first=False):
    """Keep the failed native report; salvage only measured assets with all speech present."""
    try:
        return run_audio(batch['path'], paths_file)
    except Exception:
        if not audio_first:
            raise
        directory = Path(batch['path']) / 'audio-workflow'
        result = read(directory / 'host-result.json')
        require(result.get('status') == 'awaiting_review', 'AUDIO_GENERATION_OR_CHECK_FAILED')
        preview = (directory / result['preview_directory']).resolve()
        require(preview.is_relative_to(directory.resolve()), 'AUDIO_ASSET_PATH_INVALID')
        assets = read(preview / 'assets.json')
        missing = [e for e in batch['audio_script']['events']
                   if e['kind'] in ('speech','sfx','ambience','music') and e['block_id'] not in assets]
        require(bool(missing) and all(e['kind'] != 'speech' for e in missing),
                'AUDIO_SPEECH_INCOMPLETE')
        # Unknown submissions retain the normal reconciliation gate; no silent resubmission.
        require(usage_snapshot(Path(batch['path']))['unsettled_tasks'] == 0,
                'AUDIO_EXTERNAL_RESULT_UNKNOWN')
        return dict(result, delivery_partial=True, omitted_block_ids=[e['block_id'] for e in missing])


def usage_snapshot(root):
    """Read only native ledgers; counts are reserved submissions, not billed successes."""
    calls = 0
    unknown = 0
    for path in root.glob("**/audio-workflow/calls.sqlite"):
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            calls += db.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
    for path in root.glob("**/audio-workflow/tasks.sqlite"):
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            unknown += db.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('unknown','running')").fetchone()[0]
    return {"provider_calls": calls, "unsettled_tasks": unknown,
            "usage_basis": "reserved_external_submissions_including_retries"}


def combine(root, batches, results, delivery):
    import numpy as np
    import soundfile as sf
    rate = 48000
    offset = 0
    samples = []
    events = []
    calls = 0
    source_order = [(ep["episode_id"], sc["scene_id"], u["line_id"])
                    for ep in delivery["episodes"] for sc in ep["scenes"] for u in sc["utterances"]]
    for batch, result in zip(batches, results, strict=True):
        directory = Path(batch["path"]) / "audio-workflow"
        data, sample_rate = sf.read(directory / result["preview_directory"] / "draft.wav", always_2d=True)
        require(sample_rate == rate and len(data) == result["timeline"]["total_frames"], "AUDIO_TIMELINE_MISMATCH")
        positions = {p["block_id"]: p for p in result["timeline"]["placements"]}
        originals = {u["line_id"]: u for u in batch["scene"]["utterances"]}
        for coverage in batch["audio_map"]["coverage"]:
            source = coverage["source"]
            position = positions[coverage["target_block_ids"][0]]
            original = originals[source["line_id"]]
            asset = directory / position["file"]
            require(sha256(asset.read_bytes()).hexdigest() == position["sha256"], "AUDIO_ASSET_HASH_MISMATCH")
            events.append({"sequence": len(events) + 1, **{k: source[k] for k in ("episode_id", "scene_id", "line_id")},
                           "kind": original["kind"], "text": original["text"],
                           "identity_id": original.get("speaker_role_id") or original.get("narrator_id"),
                           "start_frame": offset + position["start_frame"], "end_frame": offset + position["end_frame"],
                           "duration_basis": "planned_silent_hold" if original["kind"] == "action" else "measured_audio",
                           "source_audio_sha256": position["sha256"]})
        samples.append(data)
        offset += len(data)
        calls += result["call_budget"]["production_calls"]
    require([(e["episode_id"], e["scene_id"], e["line_id"]) for e in events] == source_order, "SOURCE_COVERAGE_MISMATCH")
    output = root / "output"
    output.mkdir(exist_ok=True)
    sf.write(output / "mix.wav", np.concatenate(samples, axis=0), rate, subtype="PCM_24")
    digest = sha256((output / "mix.wav").read_bytes()).hexdigest()
    timeline = MeasuredTimeline.model_validate({"source_version_id": delivery["story_version_id"], "sample_rate": rate,
        "channels": 1, "total_frames": offset, "audio_sha256": digest, "events": events})
    save(output / "timeline.json", timeline.model_dump())
    return {"status": "succeeded", "source_version_id": delivery["story_version_id"], "provider_calls": calls,
            "audio_sha256": digest, "timeline_sha256": sha256((output / "timeline.json").read_bytes()).hexdigest(),
            "sample_rate": rate, "channels": 1, "total_frames": offset,
            "human_listening": "pending", "technical_quality": "passed", "publication": "not_requested"}


def run(root, paths_file, request):
    paths = read(paths_file)
    for relative, digest in read(Path(paths_file).parent / "dependency-lock.json")["files"].items():
        require(sha256((Path(paths_file).parent / relative).read_bytes()).hexdigest() == digest, "DEPENDENCY_CHANGED")
    if request.get('timing_only'):
        sys.path.insert(0, str(Path(paths['audio_root']) / 'src'))
        from .audio_recompile import recompile_saved
        recompile_saved(root, request)
        return
    binding = {k: request[k] for k in ("source_version_id", "delivery_sha256", "manifest_sha256", "voices")}
    if request.get("audio_revision"):
        binding["audio_revision"] = request["audio_revision"]
    if request.get('arrangement_sha256'):
        binding.update(arrangement_sha256=request['arrangement_sha256'],instruction_limits=request.get('instruction_limits'))
    if request.get('sound_library'):
        binding['sound_library']=request['sound_library']
    if request.get('audio_delivery_policy') == 'audio_first':
        binding['audio_delivery_policy'] = 'audio_first'
    frozen = root / "input-binding.json"
    if frozen.exists():
        require(read(frozen) == binding, "RUN_INPUT_CHANGED")
    else:
        save(frozen, binding)
    for kind in ("delivery", "manifest"):
        require(sha256(Path(request[kind + "_path"]).read_bytes()).hexdigest() == request[kind + "_sha256"], "SOURCE_HASH_MISMATCH")
    delivery, manifest = read(request["delivery_path"]), read(request["manifest_path"])
    require(delivery["story_version_id"] == request["source_version_id"] == manifest["story_version_id"], "SOURCE_VERSION_MISMATCH")
    arrangement = None
    if request.get('arrangement_sha256'):
        from .audio_arrangement import bind_arrangement, arrangement_bytes
        from .compound_audio import read_audio_arrangement
        raw = Path(request['arrangement_path']).read_bytes()
        require(sha256(raw).hexdigest() == request['arrangement_sha256'], 'AUDIO_ARRANGEMENT_HASH_MISMATCH')
        arrangement = read_audio_arrangement(raw)
        require(arrangement_bytes(arrangement) == raw, 'AUDIO_ARRANGEMENT_ENCODING_MISMATCH')
        bind_arrangement(arrangement, Path(request['delivery_path']).read_bytes())
        if request.get('audio_revision'):
            from .audio_arrangement import source_key
            audible={source_key(e.source) for e in arrangement.events if
                     any(op.mode in ('speech','one_shot','background') for op in e.operations)} if arrangement.schema_version=='audio_arrangement/2.0' else {
                     source_key(e.source) for e in arrangement.events if e.mode in ('speech','one_shot','background')}
            require(all(tuple(e[k] for k in ('episode_id','scene_id','line_id')) in audible
                        for e in request['audio_revision']['regenerate']), 'AUDIO_REMAKE_EVENT_UNSUPPORTED')
    sys.path.insert(0, str(Path(paths["audio_root"]) / "src"))
    import lingban_pipeline.graph as native_graph
    from lingban_pipeline.budget import CallBudget, BudgetExceeded

    class ControlledBudget(CallBudget):
        def reserve(self, batch, kind, config):
            control = read(root / "control.json")
            if (root.parent / "cancel.requested").exists():
                raise BudgetExceeded("PRODUCTION_CANCELLED")
            if time.time() >= control["deadline_epoch"]:
                raise BudgetExceeded("PRODUCTION_DEADLINE_EXCEEDED")
            return super().reserve(batch, kind, config)

    native_graph.CallBudget = ControlledBudget
    original_sfx=native_graph.QianwenSFX
    try:
        if request.get('sound_library'):
            from .sound_library_adapter import sound_library_adapter
            config=request['sound_library']
            native_graph.QianwenSFX=sound_library_adapter(original_sfx,config['root'],scope=config['scope'],
                job_id=root.parent.name,allow_unreviewed=config.get('allow_unreviewed',False))
        _, _, batches = prepare_audio(root, delivery, manifest, request["voices"], paths,
                                      instruction_limits=request.get('instruction_limits'), arrangement=arrangement)
        revision = request.get("audio_revision")
        previous, imported = {}, 0
        if revision:
            cache = Path(request["audio_cache_path"])
            require(sha256(cache.read_bytes()).hexdigest() == revision["cache"]["sha256"], "AUDIO_CACHE_HASH_MISMATCH")
            document, blobs = load_cache(cache, binding)
            previous = document["candidates"]
            for batch in batches:
                imported += seed_cache(Path(batch["path"]) / "audio-workflow", document, blobs)
        candidates = apply_candidates(batches, delivery, previous, revision["regenerate"] if revision else [], request.get("revision_nonce", ""))
        results = [run_delivery_batch(batch, paths_file, audio_first=request.get('audio_delivery_policy') == 'audio_first') if any(e['kind'] in ('speech','sfx','ambience','music')
                   for e in batch['audio_script']['events']) else {'status':'not_required','reason':'non_audio_markers_only'}
                   for batch in batches]
        save(root / "audio-results.json", results)
        omitted_ids = [m['operation_id'] for batch, result in zip(batches, results, strict=True)
                       for m in batch.get('operation_map', []) if m['block_id'] in result.get('omitted_block_ids', [])]
        omissions = []
        if omitted_ids:
            from .audio_delivery_policy import omit_sounds
            arrangement, omissions = omit_sounds(arrangement, omitted_ids, 'sound_generation_failed')
        if arrangement is not None and arrangement.schema_version == 'audio_arrangement/2.0' and not omissions:
            snapshot = dict(binding=binding, arrangement=arrangement.model_dump(), batches=batches,
                            results=results, candidates=candidates, imported=imported)
            snapshot_file = root / 'native-complete.json'
            if snapshot_file.exists():
                require(read(snapshot_file) == snapshot, 'AUDIO_NATIVE_REPLAY_CHANGED')
            else:
                save(snapshot_file, snapshot)
        report = (combine_arranged(root,batches,results,Path(request['delivery_path']).read_bytes(),arrangement,
                                  omitted_operations=[o['operation_id'] for o in omissions])
                  if arrangement else combine(root, batches, results, delivery))
        if omissions:
            report.update(delivery_policy='audio_first', omissions=omissions,
                          omitted_sound_operations=omitted_ids, semantic_review='not_passed')
        report['instruction_length_assessment'] = read(root / 'input/instruction-length-assessment.json')
        cache_report = export_cache(root, batches, binding, candidates)
        report.update(raw_cache_sha256=cache_report["sha256"], raw_cache_entries=cache_report["entries"],
                      imported_cache_entries=imported, reused_from_cache=revision["cache"]["sha256"] if revision else None,
                      regenerated_events=revision["regenerate"] if revision else [])
        report.update(usage_snapshot(root))
        save(root / "result.json", report)
    finally:
        native_graph.CallBudget = CallBudget
        native_graph.QianwenSFX = original_sfx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--paths", type=Path, required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    import msvcrt
    with (args.root / "audio-process.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            # This is an actual OS lock check, not inference from a stale lock file.
            raise SystemExit(75)
        try:
            run(args.root, args.paths, read(args.request))
        except Exception as exc:
            code = getattr(exc, "issue", {}).get("code", "AUDIO_PRODUCTION_FAILED")
            # Do not emit arbitrary exception text, request bodies, or credentials.
            import re
            if isinstance(exc, ValueError) and re.fullmatch(r'(?:ARRANGEMENT|COMPOUND|AUDIO)_[A-Z_]+', str(exc)):
                code = str(exc)
            try:
                usage = usage_snapshot(args.root)
            except Exception:
                usage = {"provider_calls": None, "unsettled_tasks": 1, "usage_basis": "unavailable"}
            save(args.root / "result.json", {"status": "failed", "error_code": code, "error_type": type(exc).__name__,
                "timing_details": getattr(exc, 'details', {}), **usage})
            raise SystemExit(2) from None


if __name__ == "__main__":
    main()
