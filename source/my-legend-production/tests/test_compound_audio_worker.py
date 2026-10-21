"""Queue/API contract integration with synthetic PCM, no external generation."""
from copy import deepcopy
from hashlib import sha256
import json
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from my_legend_production.production.api import register_production_routes
from my_legend_production.production.audio import AudioStage
from my_legend_production.production.audio_cache import export_cache
from my_legend_production.production.audio_plan import AudioPlanner
from my_legend_production.production.audio_remake import remake_request
from my_legend_production.production.compound_audio import CompoundArrangement, resolve_compound_frames
from my_legend_production.production.compound_timeline import CompoundTimeline
from my_legend_production.production.contracts import ArtifactRef, RemakeAudioRequest
from my_legend_production.production.playback import playback_view
from my_legend_production.production.acceptance_media import audit_downloads
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.story import StoryStage
from my_legend_production.production.worker import ProductionWorker
from my_legend_production.worker import save
from test_compound_audio_plan import CompoundModel
from test_production_contracts import production_request
from test_production_queue import env
from test_production_story import StoryService, advance


def compound_audio(root, request_file, timeout):
    request=json.loads(request_file.read_bytes())
    plan=CompoundArrangement.model_validate_json(open(request['arrangement_path'],'rb').read())
    operations=[o for e in plan.events for o in e.operations]
    lengths={o.operation_id:4800 for o in operations if o.mode in ('speech','one_shot','background')}
    if request.get('audio_revision'):
        # Fixture models a single requested replacement with a new measured duration.
        target=request['audio_revision']['regenerate'][0]['operation_id']; lengths[target]=9600
    positions=resolve_compound_frames(plan,lengths)
    events=[]
    for e in plan.events:
        ops=[]
        for o in e.operations:
            value=dict(operation_id=o.operation_id,mode=o.mode,cue_text=o.cue_text,
                       start_frame=positions[o.operation_id,'start'],end_frame=positions[o.operation_id,'end'])
            if o.mode in ('speech','one_shot','background'):
                value.update(source_audio_sha256='a'*64,processing='unchanged')
            ops.append(value)
        events.append(dict(source=e.source.model_dump(),kind=e.kind,text=e.text,identity_id=e.identity_id,operations=ops,
                           **({'sound_intent':e.sound_intent} if e.sound_intent else {})))
    total=max(p for (o,point),p in positions.items());output=root/'output';output.mkdir(exist_ok=True)
    with wave.open(str(output/'mix.wav'),'wb') as f:
        f.setparams((1,2,48000,0,'NONE','not compressed'));f.writeframes(b'\x01\x01'*total)
    digest=sha256((output/'mix.wav').read_bytes()).hexdigest()
    timeline=CompoundTimeline(source_version_id=plan.source_version_id,arrangement_sha256=request['arrangement_sha256'],
        total_frames=total,audio_sha256=digest,events=events)
    save(output/'timeline.json',timeline.model_dump())
    save(output/'compile-report.json',dict(arrangement_sha256=request['arrangement_sha256'],delivery_sha256=request['delivery_sha256'],
        audio={'sha256':digest},fixture_only=True))
    binding={key:request[key] for key in ('source_version_id','delivery_sha256','manifest_sha256','voices','arrangement_sha256')}
    cache=export_cache(root,[],binding,{}) # Intentionally empty synthetic cache, native cache exercised separately.
    save(root/'result.json',dict(status='succeeded',source_version_id=plan.source_version_id,
        provider_calls=1 if request.get('audio_revision') else len(lengths),unsettled_tasks=0,audio_sha256=digest,
        timeline_sha256=sha256((output/'timeline.json').read_bytes()).hexdigest(),
        compile_report_sha256=sha256((output/'compile-report.json').read_bytes()).hexdigest(),
        raw_cache_sha256=cache['sha256'],arrangement_sha256=request['arrangement_sha256'],fixture_only=True))
    return 0


def test_compound_worker_playback_audit_and_operation_remake_reuse_original_plan(env,tmp_path):
    repo,store=env;model=CompoundModel(fixture=False);attempts=[]
    def runner(root,request_file,timeout):
        attempts.append(1)
        return 75 if len(attempts)==1 else compound_audio(root,request_file,timeout)
    worker=ProductionWorker(repo,store,{'story':StoryStage(StoryService()),
        'audio':AudioStage(store.root.parent,store.root.parent,runner=runner,planner=AudioPlanner(model,compound=True))})
    identifier=repo.create('alice',production_request(outputs=['audio']))
    advance(worker,repo,5)
    assert repo.get(identifier,'alice')['status']=='running' and len(model.calls)==2
    advance(worker,repo,1);job=repo.get(identifier,'alice')
    assert job['status']=='succeeded',job
    refs={a['kind']:ArtifactRef.model_validate(a) for a in job['artifacts']}
    view=playback_view(job,store)
    assert view['manifest']['duration_seconds']==.4
    directory=tmp_path/'downloads';directory.mkdir()
    for ref in refs.values():(directory/ref.sha256).write_bytes(store.verify(ref).read_bytes())
    audit=audit_downloads(job,view,directory,ffmpeg=None,report_directory=directory)
    assert audit['source_events']==5 and audit['human_quality']=='pending'
    plan=store.json(refs['AudioArrangement']);selection=dict(plan['events'][0]['source'],operation_id=plan['events'][0]['operations'][0]['operation_id'])
    body=RemakeAudioRequest(expected_revision=job['revision'],idempotency_key='compound-remake-1',regenerate=[selection])
    app=FastAPI();register_production_routes(app,repo,store,lambda req:req.headers.get('x-user','alice'),lambda:{})
    client=TestClient(app);url=f'/api/v1/production-jobs/{identifier}/remake-audio'
    assert client.post(url,json=body.model_dump(),headers={'x-user':'bob'}).status_code==404
    response=client.post(url,json=body.model_dump());assert response.status_code==202,response.text
    new_id=response.json()['data']['id']
    assert client.post(url,json=body.model_dump()).json()['data']['id']==new_id
    advance(worker,repo,2);revised=repo.get(new_id,'alice')
    assert revised['status']=='succeeded',revised
    assert len(model.calls)==2 and revised['actual_calls']==1
    new_refs={a['kind']:ArtifactRef.model_validate(a) for a in revised['artifacts']}
    assert new_refs['AudioArrangement']==refs['AudioArrangement'] and new_refs['AudioArrangementReview']==refs['AudioArrangementReview']
    assert playback_view(revised,store)['manifest']['duration_seconds']==.5
    assert store.json(new_refs['AudioArrangementEvidence'])['reused'] is True
    assert repo.get(identifier,'alice')==job
    invalid=body.model_copy(update={'regenerate':[body.regenerate[0].model_copy(update={'operation_id':'absent'})]})
    with pytest.raises(ProductionError,match='EVENT_UNSUPPORTED'):remake_request(job,invalid,store)
    with pytest.raises(ProductionError,match='VISUAL_UNSUPPORTED'):remake_request(job,body.model_copy(update={'outputs':['visual']}),store)
    # New plan references require ownership independently of the owned original story/cache.
    request=remake_request(job,body.model_copy(update={'idempotency_key':'unowned-plan-test'}),store)
    request.audio_revision.review=store.put_json('AudioArrangementReview',job['source_version_id'],{'unowned':True})
    with pytest.raises(ProductionError,match='SOURCE_ARTIFACT_NOT_OWNED'):repo.create('alice',request)


def test_remake_selections_allow_distinct_operations_but_reject_overlap():
    source=dict(episode_id='e',scene_id='s',line_id='l')
    def body(items):return RemakeAudioRequest(expected_revision=1,idempotency_key='selection-fixture',regenerate=items)
    assert len(body([dict(source,operation_id='one'),dict(source,operation_id='two')]).regenerate)==2
    for items in ([source,source],[source,dict(source,operation_id='one')],
                  [dict(source,operation_id='one'),source],[dict(source,operation_id='one')]*2):
        with pytest.raises(ValidationError,match='DUPLICATE_AUDIO_EVENT'):body(items)


def test_audio_stage_recompile_loop_preserves_cumulative_usage_and_final_plan(env):
    """Orchestration fixture; native no-provider recompilation has a separate media test."""
    repo,store=env;calls=[];model=CompoundModel(fixture=False)
    class Planner(AudioPlanner):
        repairs=0
        def repair_timing(self,ctx,plan,measured,failure,attempt):
            self.repairs+=1
            review=next(ctx.store.json(a) for a in self(ctx,refs[0]).artifacts if a.kind=='AudioArrangementReview')
            return plan,{'review':review,'fixture_only':True}
    planner=Planner(model,compound=True);refs=[]
    def runner(root,request_file,timeout):
        request=json.loads(request_file.read_bytes());calls.append(request)
        if len(calls)==1:
            refs.append(store.put('StoryDelivery',request['source_version_id'],open(request['delivery_path'],'rb').read()))
            plan=CompoundArrangement.model_validate_json(open(request['arrangement_path'],'rb').read())
            count=sum(o.mode in ('speech','one_shot','background') for e in plan.events for o in e.operations)
            save(root/'native-complete.json',{'fixture_only':True})
            save(root/'measured-inputs.json',{'fixture_only':True})
            save(root/'result.json',{'status':'failed','provider_calls':count,'unsettled_tasks':0,
                                   'error_code':'ARRANGEMENT_EMPTY_AUDIO_INTERVAL'})
            return 2
        assert request['timing_only'] is True
        return compound_audio(root,request_file,timeout)
    worker=ProductionWorker(repo,store,{'story':StoryStage(StoryService()),
        'audio':AudioStage(store.root.parent,store.root.parent,runner=runner,planner=planner)})
    job=repo.create('alice',production_request(outputs=['audio']));advance(worker,repo,5)
    state=repo.get(job,'alice')
    assert state['status']=='succeeded' and state['unsettled_calls']==0
    assert len(calls)==2 and planner.repairs==1 and len(model.calls)==2
    assert state['actual_calls']==15 # 9 story + 2 planning/review + 4 cumulative native calls
