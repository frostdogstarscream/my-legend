"""Audio boundary tests with synthetic PCM and real PostgreSQL; no model requests."""
from copy import deepcopy
from hashlib import sha256
import json
import sqlite3
import wave

import pytest
from pydantic import ValidationError

from my_legend_production.production.audio import AudioStage, select_voices, sound_library_configuration
from my_legend_production.production.audio_job import usage_snapshot
from my_legend_production.production.contracts import ArtifactRef, VoicePolicy
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.timeline import MeasuredTimeline
from my_legend_production.production.worker import ProductionWorker
from my_legend_production.worker import save
from test_production_contracts import production_request
from test_production_queue import env
from test_production_story import StoryService, StoryStage, advance


def test_sound_library_policy_is_owner_scoped_and_historical_jobs_keep_original_policy(tmp_path):
    root=tmp_path/'job';root.mkdir()
    alice=sound_library_configuration(root,tmp_path,'alice')
    bob=sound_library_configuration(root,tmp_path,'bob')
    assert alice['root']!=bob['root'] and alice['scope']!=bob['scope']
    assert 'alice' not in alice['root'] and alice['allow_unreviewed'] is True
    assert sound_library_configuration(root,tmp_path,None) is None
    save(root/'request.json',{'source_version_id':'old'})
    assert sound_library_configuration(root,tmp_path,'alice') is None
    save(root/'request.json',{'sound_library':dict(alice,allow_unreviewed=False)})
    assert sound_library_configuration(root,tmp_path,'alice')['allow_unreviewed'] is False


def synthetic_audio(root, request_file, timeout):
    request = json.loads(request_file.read_text(encoding="utf-8"))
    delivery = json.loads(open(request["delivery_path"], encoding="utf-8").read())
    events = []
    for ep in delivery["episodes"]:
        for scene in ep["scenes"]:
            for line in scene["utterances"]:
                i = len(events)
                events.append({"sequence": i + 1, "episode_id": ep["episode_id"], "scene_id": scene["scene_id"],
                    "line_id": line["line_id"], "kind": line["kind"], "text": line["text"],
                    "identity_id": line.get("speaker_role_id") or line.get("narrator_id"),
                    "start_frame": i * 4800, "end_frame": (i + 1) * 4800,
                    "duration_basis": "planned_silent_hold" if line["kind"] == "action" else "measured_audio",
                    "source_audio_sha256": "a" * 64})
    output = root / "output"
    output.mkdir(exist_ok=True)
    with wave.open(str(output / "mix.wav"), "wb") as audio:
        audio.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * len(events) * 4800)
    digest = sha256((output / "mix.wav").read_bytes()).hexdigest()
    timeline = {"source_version_id": delivery["story_version_id"], "sample_rate": 48000, "channels": 1,
                "total_frames": len(events) * 4800, "audio_sha256": digest, "events": events}
    save(output / "timeline.json", timeline)
    save(root / "result.json", {"status": "succeeded", "source_version_id": delivery["story_version_id"],
        "provider_calls": sum(e["kind"] != "action" for e in events), "unsettled_tasks": 0,
        "audio_sha256": digest, "timeline_sha256": sha256((output / "timeline.json").read_bytes()).hexdigest(),
        "fixture_only": True})
    return 0


def setup_audio(env, runner=synthetic_audio):
    repo, store = env
    client = StoryService()
    job = repo.create("alice", production_request(outputs=["audio"]))
    audio = AudioStage(store.root.parent, store.root.parent, runner=runner)
    worker = ProductionWorker(repo, store, {"story": StoryStage(client), "audio": audio})
    advance(worker, repo, 4)
    return job, worker


def test_audio_exports_verified_timeline_and_independent_results(env):
    repo, store = env
    job, worker = setup_audio(env)
    advance(worker, repo, 1)
    state = repo.get(job, "alice")
    assert state["status"] == "succeeded", state
    assert state["unsettled_calls"] == 0
    refs = {r["kind"]: ArtifactRef.model_validate(r) for r in state["artifacts"]}
    assert {"AudioMix", "MeasuredTimeline", "VoiceBindings", "AudioReport"} <= refs.keys()
    assert "Video" not in refs
    timeline = MeasuredTimeline.model_validate(store.json(refs["MeasuredTimeline"]))
    assert timeline.audio_sha256 == refs["AudioMix"].sha256
    assert next(s for s in state["stages"] if s["name"] == "audio")["human_review"] == "pending"


def synthetic_arranged_audio(root, request_file, timeout):
    """Only API-boundary fixture; native media compilation is tested separately."""
    from my_legend_production.production.audio_arrangement import AudioArrangement,resolve_frames,source_key
    from my_legend_production.production.timeline import ControlledTimeline
    request=json.loads(request_file.read_bytes())
    plan=AudioArrangement.model_validate_json(open(request['arrangement_path'],'rb').read())
    frames=resolve_frames(plan,{source_key(e.source):4800 for e in plan.events if e.kind!='action'})
    events=[]
    for n,e in enumerate(plan.events,1):
        key=source_key(e.source)
        event=dict(sequence=n,**e.source.model_dump(),kind=e.kind,text=e.text,identity_id=e.identity_id,
            start_frame=frames[key,'start'],end_frame=frames[key,'end'])
        event.update(dict(duration_basis='non_audio_marker',marker_type='action_note') if e.kind=='action' else
                     dict(duration_basis='measured_audio',source_audio_sha256='a'*64,render_role=e.mode))
        events.append(event)
    total=max(e['end_frame'] for e in events);output=root/'output';output.mkdir(exist_ok=True)
    with wave.open(str(output/'mix.wav'),'wb') as f:
        f.setparams((1,2,48000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*total)
    digest=sha256((output/'mix.wav').read_bytes()).hexdigest()
    timeline=ControlledTimeline(source_version_id=plan.source_version_id,arrangement_sha256=request['arrangement_sha256'],
        sample_rate=48000,channels=1,total_frames=total,audio_sha256=digest,events=events)
    save(output/'timeline.json',timeline.model_dump())
    save(output/'compile-report.json',dict(arrangement_sha256=request['arrangement_sha256'],delivery_sha256=request['delivery_sha256'],
        audio={'sha256':digest},fixture_only=True))
    save(root/'result.json',dict(status='succeeded',source_version_id=plan.source_version_id,
        provider_calls=sum(e.kind!='action' for e in plan.events),unsettled_tasks=0,audio_sha256=digest,
        timeline_sha256=sha256((output/'timeline.json').read_bytes()).hexdigest(),
        compile_report_sha256=sha256((output/'compile-report.json').read_bytes()).hexdigest(),
        arrangement_sha256=request['arrangement_sha256'],fixture_only=True))
    return 0


def test_audio_worker_persists_planner_calls_and_replays_without_duplicate_model_request(env):
    from my_legend_production.production.audio_plan import AudioPlanner
    from my_legend_production.production.playback import playback_view
    from test_audio_plan import Model
    repo,store=env;model=Model();attempts=[]
    def runner(root,request_file,timeout):
        attempts.append(1)
        return 75 if len(attempts)==1 else synthetic_arranged_audio(root,request_file,timeout)
    job=repo.create('alice',production_request(outputs=['audio']))
    worker=ProductionWorker(repo,store,{'story':StoryStage(StoryService()),
        'audio':AudioStage(store.root.parent,store.root.parent,runner=runner,planner=AudioPlanner(model))})
    advance(worker,repo,5)
    assert repo.get(job,'alice')['status']=='running' and len(model.calls)==2
    advance(worker,repo,1)
    state=repo.get(job,'alice')
    assert state['status']=='succeeded',state
    assert len(model.calls)==2 and state['actual_calls']==15 and state['unsettled_calls']==0
    refs={a['kind']:ArtifactRef.model_validate(a) for a in state['artifacts']}
    assert {'AudioArrangement','AudioArrangementReview','AudioArrangementEvidence','AudioCompileReport'}<=refs.keys()
    assert store.json(refs['MeasuredTimeline'])['schema_version']=='measured_timeline/1.2'
    assert playback_view(state,store)['manifest']['duration_seconds']==.4


def test_failed_audio_arrangement_stops_before_any_audio_generation(env):
    from my_legend_production.production.audio_plan import AudioPlanner
    from test_audio_plan import Model
    repo,store=env;model=Model('semantic')
    def forbidden(*a):raise AssertionError('Rejected plan must not generate audio')
    job=repo.create('alice',production_request(outputs=['audio']))
    worker=ProductionWorker(repo,store,{'story':StoryStage(StoryService()),
        'audio':AudioStage(store.root.parent,store.root.parent,runner=forbidden,planner=AudioPlanner(model))})
    advance(worker,repo,5)
    state=repo.get(job,'alice')
    assert state['status']=='failed' and state['error_code']=='AUDIO_ARRANGEMENT_SEMANTIC_REVIEW_FAILED'
    # This request fixture allows one repair, hence two plan/review pairs.
    assert len(model.calls)==4 and state['actual_calls']==13
    assert not any(a['kind']=='AudioMix' for a in state['artifacts'])


def test_audio_budget_counts_rendered_assets_not_sfx_control_lines(env):
    from my_legend_production.production.audio_plan import AudioPlanner
    from test_audio_plan import Model
    class ControlModel(Model):
        def complete(self,system,payload,schema,timeout):
            response=super().complete(system,payload,schema,timeout)
            if 'arrangement' not in payload:
                data=json.loads(response['content']);events=data['events'];last_speech=events[-2]['source']
                events[0].update(mode='background',end=dict(event=last_speech,point='end'))
                events[1]['start']=dict(event=None,point='start')
                events[-1].update(mode='change_background',start=dict(event=last_speech,point='start'),
                    transition_end=dict(event=last_speech,point='end'),control_target=events[0]['source'],target_gain_db=-12.0)
                response['content']=json.dumps(data)
            return response
    client=StoryService()
    line=client.delivery['episodes'][0]['scenes'][0]['utterances'][-1]
    line.update(kind='sfx',text='环境声渐渐变小。')
    client.manifest['tasks'][-1].update(source_kind='sfx',text=line['text'])
    repo,store=env;job=repo.create('alice',production_request(outputs=['audio']))
    worker=ProductionWorker(repo,store,{'story':StoryStage(client),
        'audio':AudioStage(store.root.parent,store.root.parent,runner=lambda *a:75,planner=AudioPlanner(ControlModel()))})
    advance(worker,repo,5)
    assert repo.get(job,'alice')['status']=='running'
    with repo.transaction() as db:
        row=db.execute("SELECT reserved_units FROM calls WHERE job_id=%s AND call_key='audio:generation-budget'",(job,)).fetchone()
    assert row['reserved_units']==4 # 3 speech + 1 bed, not the 5 audible-kind source rows.


@pytest.mark.parametrize('fault,code',[('downgrade','AUDIO_ARRANGEMENT_TIMELINE_VERSION_MISMATCH'),
    ('compile_binding','AUDIO_COMPILE_REPORT_BINDING_MISMATCH')])
def test_arranged_worker_rejects_legacy_output_or_unbound_compile_report(env,fault,code):
    from my_legend_production.production.audio_plan import AudioPlanner
    from test_audio_plan import Model
    repo,store=env
    def runner(root,request,timeout):
        if fault=='downgrade':return synthetic_audio(root,request,timeout)
        synthetic_arranged_audio(root,request,timeout)
        path=root/'output/compile-report.json';audit=json.loads(path.read_bytes());audit['delivery_sha256']='0'*64
        save(path,audit)
        result=json.loads((root/'result.json').read_bytes());result['compile_report_sha256']=sha256(path.read_bytes()).hexdigest()
        save(root/'result.json',result);return 0
    job=repo.create('alice',production_request(outputs=['audio']))
    worker=ProductionWorker(repo,store,{'story':StoryStage(StoryService()),
        'audio':AudioStage(store.root.parent,store.root.parent,runner=runner,planner=AudioPlanner(Model()))})
    advance(worker,repo,5)
    state=repo.get(job,'alice')
    assert state['status']=='failed' and state['error_code']==code
    assert not any(a['kind']=='AudioMix' for a in state['artifacts'])


@pytest.mark.parametrize("corruption,expected", [("hash", "AUDIO_OUTPUT_HASH_MISMATCH"),
    ("frames", "AUDIO_TIMELINE_MISMATCH"), ("coverage", "SOURCE_COVERAGE_MISMATCH")])
def test_bad_audio_never_publishes_but_usage_survives(env, corruption, expected):
    def runner(root, request, timeout):
        synthetic_audio(root, request, timeout)
        path = root / "output/timeline.json"
        timeline = json.loads(path.read_text(encoding="utf-8"))
        if corruption == "frames":
            timeline["total_frames"] += 1
        elif corruption == "coverage":
            timeline["events"][0]["text"] = "changed"
        save(path, timeline)
        report = json.loads((root / "result.json").read_text(encoding="utf-8"))
        report["timeline_sha256"] = "0" * 64 if corruption == "hash" else sha256(path.read_bytes()).hexdigest()
        save(root / "result.json", report)
        return 0
    repo, _ = env
    job, worker = setup_audio(env, runner)
    advance(worker, repo, 1)
    state = repo.get(job, "alice")
    assert state["error_code"] == expected
    assert state["actual_calls"] > 9
    assert not any(a["kind"] == "AudioMix" for a in state["artifacts"])


def test_unknown_audio_result_preserves_cost_and_blocks_resubmit(env):
    def runner(root, request, timeout):
        save(root / "result.json", {"status": "failed", "provider_calls": 2, "unsettled_tasks": 1})
        return 2
    repo, _ = env
    job, worker = setup_audio(env, runner)
    advance(worker, repo, 1)
    state = repo.get(job, "alice")
    assert state["status"] == "needs_input" and state["error_code"] == "EXTERNAL_RESULT_UNKNOWN"
    assert state["actual_calls"] == 11
    with pytest.raises(ProductionError, match="EXTERNAL_RESULT_UNKNOWN"):
        repo.resume(job, "alice", 1, {})


def test_zero_submission_failure_is_settled_and_explicit_resume_reuses_budget(env):
    attempts = []
    def runner(root, request, timeout):
        attempts.append(1)
        if len(attempts) == 1:
            save(root / 'result.json', {'status':'failed', 'provider_calls':0, 'unsettled_tasks':0})
            return 2
        return synthetic_audio(root, request, timeout)
    repo, _ = env
    job, worker = setup_audio(env, runner)
    advance(worker, repo, 1)
    failed = repo.get(job, 'alice')
    assert failed['status'] == 'failed' and failed['unsettled_calls'] == 0
    assert failed['actual_calls'] == 9  # The story fixture calls remain, audio is zero.
    reservation = failed['reserved_calls']
    repo.resume(job, 'alice', failed['revision'], {})
    advance(worker, repo, 1)
    succeeded = repo.get(job, 'alice')
    assert succeeded['status'] == 'succeeded' and succeeded['unsettled_calls'] == 0
    assert succeeded['actual_calls'] > 9 and succeeded['reserved_calls'] == reservation
    assert len(attempts) == 2
    with repo.transaction() as db:
        events = db.execute("SELECT details FROM events WHERE job_id=%s AND event_type='zero_call_rearmed'", (job,)).fetchall()
    assert len(events) == 1 and events[0]['details']['previous_revision'] == 1


def test_known_paid_failure_settles_budget_and_resume_does_not_resubmit(env):
    attempts=[]
    def runner(root,request,timeout):
        attempts.append(1)
        save(root/'result.json',{'status':'failed','provider_calls':2,'unsettled_tasks':0,
                               'error_code':'ARRANGEMENT_EMPTY_AUDIO_INTERVAL'})
        return 2
    repo,_=env;job,worker=setup_audio(env,runner);advance(worker,repo,1)
    failed=repo.get(job,'alice')
    assert failed['status']=='failed' and failed['unsettled_calls']==0 and failed['unknown_calls']==0
    assert failed['actual_calls']==11 and failed['error_code']=='ARRANGEMENT_EMPTY_AUDIO_INTERVAL'
    assert any(a['kind']=='AudioFailureReport' for a in failed['artifacts'])
    with pytest.raises(ProductionError,match='AUDIO_NEW_REVISION_REQUIRED'):
        repo.resume(job,'alice',failed['revision'],{})
    assert len(attempts)==1 and repo.get(job,'alice')['actual_calls']==11


@pytest.mark.parametrize('calls,unknown', [(0,False),(2,False),(2,True)])
def test_missing_child_report_recovers_durable_usage(env,calls,unknown):
    def runner(root,request,timeout):
        if calls:
            ledger=root/'scene/audio-workflow';ledger.mkdir(parents=True)
            with sqlite3.connect(ledger/'calls.sqlite') as db:
                db.execute('CREATE TABLE calls(id TEXT)')
                db.executemany('INSERT INTO calls VALUES(?)',[(str(i),) for i in range(calls)])
            with sqlite3.connect(ledger/'tasks.sqlite') as db:
                db.execute('CREATE TABLE tasks(status TEXT)')
                db.execute('INSERT INTO tasks VALUES(?)',('running' if unknown else 'succeeded',))
        return 2
    repo,_=env;job,worker=setup_audio(env,runner);advance(worker,repo,1)
    state=repo.get(job,'alice')
    assert state['actual_calls']==9+calls
    assert state['unknown_calls']==int(unknown)
    if unknown:
        assert state['status']=='needs_input' and state['error_code']=='EXTERNAL_RESULT_UNKNOWN'
    else:
        assert state['status']=='failed' and state['error_code']=='AUDIO_RESULT_MISSING'
        assert state['unsettled_calls']==0
        assert any(a['kind']=='AudioFailureReport' for a in state['artifacts'])


def test_forced_audio_stop_preserves_native_usage_and_requires_reconciliation(env):
    def runner(root, request, timeout):
        ledger = root / "scene" / "audio-workflow"
        ledger.mkdir(parents=True)
        with sqlite3.connect(ledger / "calls.sqlite") as db:
            db.execute("CREATE TABLE calls(id TEXT)")
            db.executemany("INSERT INTO calls VALUES(?)", [("1",), ("2",)])
        with sqlite3.connect(ledger / "tasks.sqlite") as db:
            db.execute("CREATE TABLE tasks(status TEXT)")
            db.execute("INSERT INTO tasks VALUES('running')")
        raise ProductionError("DEADLINE_EXCEEDED")
    repo, _ = env
    job, worker = setup_audio(env, runner)
    advance(worker, repo, 1)
    state = repo.get(job, "alice")
    assert state["status"] == "failed" and state["error_code"] == "DEADLINE_EXCEEDED"
    assert state["actual_calls"] == 11 and state["unsettled_calls"] > 0 and state["unknown_calls"] == 1
    assert not any(a["kind"] == "AudioMix" for a in state["artifacts"])
    with pytest.raises(ProductionError, match="EXTERNAL_RESULT_UNKNOWN"):
        repo.resume(job, "alice", 1, {})


def test_native_usage_reads_reservations_and_unknown_tasks(tmp_path):
    root = tmp_path / "scene" / "audio-workflow"
    root.mkdir(parents=True)
    with sqlite3.connect(root / "calls.sqlite") as db:
        db.execute("CREATE TABLE calls(id TEXT)")
        db.executemany("INSERT INTO calls VALUES(?)", [("1",), ("2",)])
    with sqlite3.connect(root / "tasks.sqlite") as db:
        db.execute("CREATE TABLE tasks(status TEXT)")
        db.executemany("INSERT INTO tasks VALUES(?)", [("succeeded",), ("unknown",)])
    report = usage_snapshot(tmp_path)
    assert report["provider_calls"] == 2 and report["unsettled_tasks"] == 1


def test_voice_mapping_is_stable_by_identity_not_cast_order():
    delivery = StoryService().delivery
    before = select_voices(delivery, VoicePolicy())
    reordered = deepcopy(delivery)
    for ep in reordered["episodes"]:
        for scene in ep["scenes"]:
            scene["utterances"].reverse()
    assert select_voices(reordered, VoicePolicy()) == before
    assert before["narrator"] == "longsanshu_v3.1"


def test_call_observation_is_cumulative_and_settlement_counts_only_delta(env):
    repo, _ = env
    job = repo.create("alice", production_request(outputs=["script"]))
    stage = repo.claim("test", 30)
    repo.reserve_call(stage, "usage", "hash", 5)
    repo.observe_call(stage, "usage", 2)
    repo.observe_call(stage, "usage", 1)
    repo.complete_call(stage, "usage", {"count": 3}, actual_units=3)
    repo.complete_call(stage, "usage", {"count": 3}, actual_units=3)
    assert repo.get(job, "alice")["actual_calls"] == 3


def test_timeline_rejects_unmeasured_speech_and_out_of_bounds(tmp_path):
    event = {"sequence": 1, "episode_id": "e", "scene_id": "s", "line_id": "l", "kind": "dialogue",
             "text": "fixture", "start_frame": 0, "end_frame": 10, "duration_basis": "visual_estimate",
             "source_audio_sha256": "a" * 64}
    raw = {"source_version_id": "v", "sample_rate": 48000, "channels": 1, "total_frames": 10,
           "audio_sha256": "b" * 64, "events": [event]}
    with pytest.raises(ValidationError, match="AUDIO_EVENT_MUST_BE_MEASURED"):
        MeasuredTimeline.model_validate(raw)
    event.update(duration_basis="measured_audio", end_frame=11)
    with pytest.raises(ValidationError, match="TIMELINE_BOUNDS_INVALID"):
        MeasuredTimeline.model_validate(raw)
