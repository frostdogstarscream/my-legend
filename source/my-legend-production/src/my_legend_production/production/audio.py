"""Audio module facade; no visual render or human-review continuation is required."""
from hashlib import sha256
import json
from pathlib import Path
import time
import wave

from ..performance_production import VOICES, validate_voices
from ..worker import save
from .contracts import ArtifactRef, StageResult
from .repository import ProductionError
from .timeline import read_timeline, timeline_source_rows
from .process_tree import run_process_tree, media_environment


def sound_library_configuration(job_root,production_root,owner_id):
    previous=Path(job_root)/'request.json'
    if previous.exists():
        # A resumed historical job keeps its original policy, including no library.
        return json.loads(previous.read_bytes()).get('sound_library')
    if not owner_id:
        return None
    scope=sha256(owner_id.encode()).hexdigest()
    return {'root':str(Path(production_root)/'sound-library'/scope),'scope':scope,'allow_unreviewed':True}


def select_voices(delivery, policy):
    if policy.mode == "configured":
        bindings = policy.bindings
    else:
        # Identity hashing is stable across cast order and scenes; no sample-specific names.
        pool = [v["id"] for v in VOICES if v["id"] != "longsanshu_v3.1"]
        bindings = {}
        for ep in delivery["episodes"]:
            for scene in ep["scenes"]:
                for line in scene["utterances"]:
                    if line["kind"] == "dialogue":
                        rid = line["speaker_role_id"]
                        bindings[rid] = pool[int(sha256(rid.encode()).hexdigest(), 16) % len(pool)]
                    elif line["kind"] == "narration":
                        bindings[line["narrator_id"]] = "longsanshu_v3.1"
    validate_voices(delivery, bindings)
    return bindings


class AudioStage:
    def __init__(self, root, workspace, *, runner=None, planner=None):
        self.root, self.workspace = Path(root), Path(workspace)
        self.runner = runner
        self.planner = planner

    def launch(self, root, request_file, timeout, *, check=None):
        with (root / "worker.log").open("ab") as log:
            return run_process_tree(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(self.workspace / "ops/my-legend/Start-Performance.ps1"), "-RequestPath", str(request_file),
                "-Root", str(root), "-IndependentAudio"], log=log,
                timeout=timeout, cancel_file=root.parent / "cancel.requested", check=check, env=media_environment())

    def __call__(self, ctx):
        refs = {a["kind"]: ArtifactRef.model_validate(a) for a in ctx.stage["artifacts"]}
        delivery = ctx.store.json(refs["StoryDelivery"])
        manifest = ctx.store.json(refs["RenderManifest"])
        lines = [u for ep in delivery["episodes"] for sc in ep["scenes"] for u in sc["utterances"]]
        if any(u["kind"] not in ("dialogue", "narration", "sfx", "action") for u in lines):
            raise ProductionError("AUDIO_INPUT_CAPABILITY_MISSING")
        voices = select_voices(delivery, ctx.request.voices)
        planning_artifacts = []
        arrangement_ref = None
        if ctx.request.sound_workflow:
            from .sound_bridge import validate_package
            package = validate_package(ctx, refs)
            voices = package.voices
            arrangement_ref = refs['AudioArrangement']
        elif ctx.request.audio_revision and ctx.request.audio_revision.arrangement:
            from .audio_revision_plan import reuse_revision_plan
            planning_artifacts = reuse_revision_plan(ctx, refs['StoryDelivery'])
            arrangement_ref = ctx.request.audio_revision.arrangement
        elif 'visual' not in ctx.request.outputs and self.planner is not None and ctx.request.audio_revision is None:
            planning = self.planner(ctx, refs['StoryDelivery'])
            if planning.status != 'succeeded':
                return planning
            planning_artifacts = planning.artifacts
            arrangement_ref = next(a for a in planning_artifacts if a.kind == 'AudioArrangement')
        count = sum(u["kind"] in ("dialogue", "narration", "sfx") for u in lines)
        if arrangement_ref is not None:
            from .compound_audio import CompoundArrangement, read_audio_arrangement
            arrangement=read_audio_arrangement(ctx.store.json(arrangement_ref))
            operations = [o for e in arrangement.events for o in e.operations] if isinstance(arrangement, CompoundArrangement) else arrangement.events
            count=sum(e.mode in ('speech','one_shot','background') for e in operations)
        budget_key = "audio:generation-budget"
        reservation = ctx.repository.reserve_call(ctx.stage, budget_key, refs["StoryDelivery"].sha256 + refs["RenderManifest"].sha256 +
            (arrangement_ref.sha256 if arrangement_ref else ''), count)
        if reservation["state"] == "unknown":
            raise ProductionError("EXTERNAL_RESULT_UNKNOWN")
        if reservation["state"] == "completed" and (reservation.get('result') or {}).get('preflight_failed'):
            # A process replay must not reopen a failed call. Explicit resume re-arms
            # this zero-submission reservation atomically with the job revision.
            raise ProductionError('AUDIO_GENERATION_OR_CHECK_FAILED')
        if reservation['state'] == 'completed' and (reservation.get('result') or {}).get('generation_failed'):
            return StageResult(status='failed', source_version_id=delivery['story_version_id'],
                error_code=reservation['result'].get('error_code', 'AUDIO_GENERATION_OR_CHECK_FAILED'),
                technical_quality='failed', artifacts=planning_artifacts)
        root = self.root / "jobs" / ctx.stage["job_id"] / "audio"
        root.mkdir(parents=True, exist_ok=True)
        request = {"source_version_id": delivery["story_version_id"], "delivery_path": str(ctx.store.verify(refs["StoryDelivery"])),
                   "manifest_path": str(ctx.store.verify(refs["RenderManifest"])), "delivery_sha256": refs["StoryDelivery"].sha256,
                   "manifest_sha256": refs["RenderManifest"].sha256, "voices": voices}
        if ctx.request.audio_delivery_policy == 'audio_first':
            request['audio_delivery_policy'] = 'audio_first'
        if 'visual' not in ctx.request.outputs:
            from .length_policy import INSTRUCTION_LIMITS
            request['instruction_limits'] = dict(INSTRUCTION_LIMITS)
            library=sound_library_configuration(root,self.root,ctx.stage.get('owner_id'))
            if library is not None:
                request['sound_library']=library
        if arrangement_ref is not None:
            request.update(arrangement_path=str(ctx.store.verify(arrangement_ref)),arrangement_sha256=arrangement_ref.sha256)
        if ctx.request.audio_revision:
            request.update(audio_revision=ctx.request.audio_revision.model_dump(mode="json", exclude_none=True),
                           audio_cache_path=str(ctx.store.verify(ctx.request.audio_revision.cache)),
                           revision_nonce=ctx.stage["job_id"])
        save(root / "request.json", request)
        deadline = ctx.stage["deadline"].timestamp()
        save(root / "control.json", {"deadline_epoch": deadline})
        if time.time() >= deadline:
            raise ProductionError("DEADLINE_EXCEEDED")
        from .audio_recompile import REPAIRABLE
        from .audio_arrangement import arrangement_bytes
        from .compound_audio import CompoundArrangement
        planning_evidence = next((ctx.store.json(a) for a in planning_artifacts if a.kind == 'AudioArrangementEvidence'), {})
        repair_limit = max(0, ctx.request.budget.max_repairs_per_stage - planning_evidence.get('repairs', 0))
        repair_records = []
        # Replays use completed native batches. This path never calls TTS/SFX again.
        if (root / 'native-complete.json').is_file():
            request['timing_only'] = True
        while True:
            save(root / 'request.json', request)
            try:
                if self.runner is None:
                    code = self.launch(root, root / 'request.json', max(1, deadline - time.time()), check=ctx.check_active)
                else:
                    code = self.runner(root, root / 'request.json', max(1, deadline - time.time()))
            except ProductionError:
                from .audio_job import usage_snapshot
                try:
                    usage = usage_snapshot(root)
                    ctx.repository.observe_call(ctx.stage, budget_key, usage['provider_calls'], unknown=True)
                except ProductionError:
                    raise
                except Exception:
                    ctx.repository.unknown_call(ctx.stage, budget_key)
                raise
            if code == 75:
                return StageResult(status='running', checkpoint={'phase': 'audio_child_active'}, poll_after_seconds=5)
            report_file = root / 'result.json'
            if not report_file.is_file():
                from .audio_job import usage_snapshot
                # The process tree has exited. Recover durable usage even when
                # the launcher failed before Python could write its final report.
                report = dict(status='failed', error_code='AUDIO_RESULT_MISSING',
                              report_missing=True, **usage_snapshot(root))
            else:
                report = json.loads(report_file.read_text(encoding='utf-8'))
            calls = report.get('provider_calls')
            uncertain = report.get('unsettled_tasks', 0) > 0 or calls is None
            ctx.repository.observe_call(ctx.stage, budget_key, calls if calls is not None else reservation['actual_units'], unknown=uncertain)
            if uncertain:
                raise ProductionError('EXTERNAL_RESULT_UNKNOWN')
            if not code and report['status'] == 'succeeded':
                break
            can_repair = (report.get('error_code') in REPAIRABLE and isinstance(arrangement if arrangement_ref else None, CompoundArrangement)
                and hasattr(self.planner, 'repair_timing') and (root / 'native-complete.json').is_file()
                and (root / 'measured-inputs.json').is_file() and ctx.request.audio_revision is None)
            revised = None
            failure = report
            while can_repair and len(repair_records) < repair_limit:
                ctx.check_active()
                revised, evidence = self.planner.repair_timing(ctx, arrangement,
                    json.loads((root / 'measured-inputs.json').read_bytes()), failure, len(repair_records))
                repair_records.append(evidence)
                if revised is not None:
                    break
                failure = dict(report, previous_repair=evidence.get('issues'))
            if revised is None:
                error = report.get('error_code', 'AUDIO_GENERATION_OR_CHECK_FAILED')
                if error not in REPAIRABLE and error != 'AUDIO_RESULT_MISSING':
                    error = 'AUDIO_GENERATION_OR_CHECK_FAILED'
                settled = ({'provider_calls': 0, 'preflight_failed': True} if calls == 0 else
                           {'provider_calls': calls, 'generation_failed': True, 'error_code': error})
                ctx.repository.complete_call(ctx.stage, budget_key, settled, actual_units=calls)
                return StageResult(status='failed', source_version_id=delivery['story_version_id'], error_code=error,
                    technical_quality='failed', artifacts=planning_artifacts + [ctx.store.put_json('AudioFailureReport',
                        delivery['story_version_id'], dict(report=report, timing_repairs=repair_records, native_materials_retained=True))])
            arrangement = revised
            arrangement_ref = ctx.store.put('AudioArrangement', revised.source_version_id, arrangement_bytes(revised))
            planning_artifacts = [a for a in planning_artifacts if a.kind not in ('AudioArrangement', 'AudioArrangementReview', 'AudioArrangementEvidence')]
            planning_artifacts += [arrangement_ref,
                ctx.store.put_json('AudioArrangementReview', revised.source_version_id, evidence['review']),
                ctx.store.put_json('AudioArrangementEvidence', revised.source_version_id,
                    dict(planning_evidence, timing_repairs=repair_records, original_arrangement_sha256=request['arrangement_sha256']))]
            request.update(arrangement_path=str(ctx.store.verify(arrangement_ref)), arrangement_sha256=arrangement_ref.sha256, timing_only=True)
        version = delivery["story_version_id"]
        if report.get('omitted_sound_operations'):
            from .audio_delivery_policy import omit_sounds, delivery_review
            if ctx.request.audio_delivery_policy != 'audio_first':
                raise ProductionError('AUDIO_DELIVERY_POLICY_MISMATCH')
            effective, omissions = omit_sounds(arrangement, report['omitted_sound_operations'], 'sound_generation_failed')
            if report.get('omissions') != omissions or (root / 'output/arrangement.json').read_bytes() != arrangement_bytes(effective):
                raise ProductionError('AUDIO_DELIVERY_OMISSION_MISMATCH')
            arrangement = effective
            arrangement_ref = ctx.store.put('AudioExecutionArrangement', version, arrangement_bytes(effective))
            planning_artifacts += [arrangement_ref,
                ctx.store.put_json('AudioExecutionReview', version, delivery_review(effective).model_dump())]
        if report["source_version_id"] != version:
            raise ProductionError("SOURCE_VERSION_MISMATCH")
        audio = (root / "output/mix.wav").read_bytes()
        timeline_raw = (root / "output/timeline.json").read_bytes()
        if sha256(audio).hexdigest() != report["audio_sha256"] or sha256(timeline_raw).hexdigest() != report["timeline_sha256"]:
            raise ProductionError("AUDIO_OUTPUT_HASH_MISMATCH")
        timeline = read_timeline(timeline_raw)
        expected_timeline = {'audio_arrangement/1.0':'measured_timeline/1.1',
                            'audio_arrangement/1.1':'measured_timeline/1.2',
                            'audio_arrangement/2.0':'measured_timeline/2.0'}.get(arrangement.schema_version) if arrangement_ref else 'measured_timeline/1.0'
        if timeline.schema_version != expected_timeline:
            raise ProductionError('AUDIO_ARRANGEMENT_TIMELINE_VERSION_MISMATCH')
        if arrangement_ref is not None and (timeline.arrangement_sha256 != arrangement_ref.sha256 or
                                           report.get('arrangement_sha256') != arrangement_ref.sha256):
            raise ProductionError('AUDIO_ARRANGEMENT_BINDING_MISMATCH')
        if arrangement_ref is not None and arrangement.schema_version == 'audio_arrangement/2.0':
            from .compound_timeline import bind_compound_timeline
            bind_compound_timeline(arrangement, timeline)
        if timeline.source_version_id != version or timeline.audio_sha256 != report["audio_sha256"]:
            raise ProductionError("SOURCE_VERSION_MISMATCH")
        with wave.open(str(root / "output/mix.wav"), "rb") as wave_file:
            if (wave_file.getnframes(), wave_file.getframerate(), wave_file.getnchannels()) != (timeline.total_frames, timeline.sample_rate, timeline.channels):
                raise ProductionError("AUDIO_TIMELINE_MISMATCH")
        expected = [(ep["episode_id"], sc["scene_id"], u["line_id"], u["kind"], u["text"])
                    for ep in delivery["episodes"] for sc in ep["scenes"] for u in sc["utterances"]]
        if timeline_source_rows(timeline) != expected:
            raise ProductionError("SOURCE_COVERAGE_MISMATCH")
        ctx.repository.complete_call(ctx.stage, budget_key, {"provider_calls": report["provider_calls"]}, actual_units=report["provider_calls"])
        artifacts = [ctx.store.put("AudioMix", version, audio, "audio/wav"), ctx.store.put("MeasuredTimeline", version, timeline_raw),
                     ctx.store.put_json("VoiceBindings", version, {"policy": ctx.request.voices.model_dump(), "bindings": voices}),
                     ctx.store.put_json("AudioReport", version, report)]
        artifacts.extend(planning_artifacts)
        if arrangement_ref is not None:
            compile_report = (root / 'output/compile-report.json').read_bytes()
            if sha256(compile_report).hexdigest() != report.get('compile_report_sha256'):
                raise ProductionError('AUDIO_COMPILE_REPORT_HASH_MISMATCH')
            audit = json.loads(compile_report)
            if (audit.get('arrangement_sha256'),audit.get('delivery_sha256'),audit.get('audio',{}).get('sha256')) != (
                    arrangement_ref.sha256,refs['StoryDelivery'].sha256,timeline.audio_sha256):
                raise ProductionError('AUDIO_COMPILE_REPORT_BINDING_MISMATCH')
            artifacts.append(ctx.store.put('AudioCompileReport',version,compile_report))
        if report.get("raw_cache_sha256"):
            cache = (root / "output/raw-cache.zip").read_bytes()
            if sha256(cache).hexdigest() != report["raw_cache_sha256"]:
                raise ProductionError("AUDIO_CACHE_HASH_MISMATCH")
            artifacts.append(ctx.store.put("AudioCache", version, cache, "application/zip"))
        return StageResult(status="succeeded", source_version_id=version, artifacts=artifacts, technical_quality="passed", human_review="pending")
