"""Offline bridge contract and durable-worker tests; no provider requests."""
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from my_legend_production.production.sound_bridge import SoundBridgeStage, validate_package
from my_legend_production.production.audio import AudioStage
from my_legend_production.production.audio_simple import compile_intents
from my_legend_production.production.compound_audio import bind_compound, resolve_compound_frames
from my_legend_production.production.contracts import ArtifactRef, stage_graph, fingerprint
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.worker import ProductionWorker, StageContext
from my_legend_production.production.story import StoryStage
from test_audio_plan import Context
from test_audio_choices import passing_review
from test_audio_simple import fixture
from test_production_contracts import production_request
from test_production_queue import env
from test_production_story import StoryService, advance
from test_compound_audio_worker import compound_audio
from my_legend_production.production.api import register_production_routes
from my_legend_production.production.cli import capabilities


class BridgeModel:
    def __init__(self, failure=None):
        self.calls=[];self.failure=failure
    def complete(self,system,payload,schema,timeout):
        self.calls.append(deepcopy(payload))
        if self.failure=='network':raise RuntimeError('fixture network failure')
        if 'source_rows' in payload:
            if self.failure=='classification':return {'content':'{}'}
            return {'content':json.dumps({'choices':{s['slot_id']:dict(
                classification='non_audio' if s['kind']=='action' else 'audible',reason='fixture classification')
                for s in payload['source_rows'] if s['kind'] not in ('dialogue','narration')}})}
        if 'arrangement' in payload:
            answer=passing_review(payload)
            if self.failure=='review':
                obj=json.loads(answer['content'])
                slot=next(x for x in payload['review_slots'] if x['kind']=='sfx')
                obj['checks'][slot['slot_id']]['issues']=[dict(rule='missing_sound_component',
                    evidence=slot['text'],reason='fixture rejection',operation_ids=[])]
                answer['content']=json.dumps(obj)
            return answer
        return {'content':json.dumps({'choices':{s['slot_id']:dict(sounds=[] if s['kind']=='action' else [dict(
            cue_text=s['text'],kind='one_shot',placement='between_lines',level='ambient',reason='fixture sound')],
            controls=[],simplifications=[]) for s in payload['required_events'] if s['kind'] not in ('dialogue','narration')}})}


def setup_context(tmp_path):
    ctx=Context(tmp_path)
    ctx.request=production_request(outputs=['audio'],sound_workflow='sound_bridge/1')
    story=StoryService()
    refs=[ctx.store.put_json(k,story.delivery['story_version_id'],v) for k,v in
          [('StoryDelivery',story.delivery),('RenderManifest',story.manifest)]]
    ctx.stage['artifacts']=[r.model_dump() for r in refs]
    return ctx


class MisclassifiedOpeningModel(BridgeModel):
    """Reproduce the real failure: control classification, missing-sound review, repair."""
    def __init__(self, refuse_reclassification=False, drop_repaired_sound=False):
        super().__init__()
        self.refuse = refuse_reclassification
        self.drop = drop_repaired_sound
        self.reviews = 0

    def complete(self, system, payload, schema, timeout):
        answer = super().complete(system,payload,schema,timeout)
        data = json.loads(answer['content'])
        if 'source_rows' in payload:
            data['choices']['line_0001']['classification'] = (
                'audible' if payload.get('required_audible_slots') and not self.refuse else 'control_only')
        elif 'required_events' in payload:
            if payload['required_events'][0].get('sound_intent')=='control_only' or self.drop:
                data['choices']['line_0001']['sounds'] = []
        else:
            self.reviews += 1
            if self.reviews == 1:
                data['checks']['line_0001']['issues'] = [dict(rule='missing_sound_component',
                    evidence=payload['review_slots'][0]['text'],reason='opening audible collision is missing',operation_ids=[])]
        return dict(content=json.dumps(data,ensure_ascii=False))


def opening_context(tmp_path):
    ctx = setup_context(tmp_path)
    refs = {a['kind']:ArtifactRef.model_validate(a) for a in ctx.stage['artifacts']}
    delivery = ctx.store.json(refs['StoryDelivery']);manifest = ctx.store.json(refs['RenderManifest'])
    text = '翻找抽屉的轻微碰撞声，突然停止'
    delivery['episodes'][0]['scenes'][0]['utterances'][0]['text'] = text
    manifest['tasks'][0]['text'] = text
    ctx.stage['artifacts'] = [ctx.store.put_json(k,delivery['story_version_id'],v).model_dump()
                              for k,v in [('StoryDelivery',delivery),('RenderManifest',manifest)]]
    return ctx


def test_missing_opening_sound_reclassifies_and_survives_replay(tmp_path):
    ctx=opening_context(tmp_path);model=MisclassifiedOpeningModel()
    bridge,result=finish_bridge(ctx,model)
    assert result.status=='succeeded'
    assert len(model.calls)==6  # classify, plan/review, reclassify, plan/review
    assert bridge(ctx)==result and len(model.calls)==6
    refs={a['kind']:ArtifactRef.model_validate(a) for a in ctx.stage['artifacts']}
    refs.update({a.kind:a for a in result.artifacts})
    package=validate_package(ctx,refs)
    assert package.sound_tasks[0].cue_text=='翻找抽屉的轻微碰撞声，突然停止'
    assert package.sound_tasks[0].mode=='one_shot'
    classification=ctx.store.json(refs['SoundSourceClassification'])
    assert classification['choices']['line_0001']['classification']=='audible'
    assert classification['initial_classification_sha256'] != refs['SoundSourceClassification'].sha256
    evidence=ctx.store.json(refs['AudioArrangementEvidence'])
    assert len(evidence['blocking_findings'])==1 and evidence['unresolved_findings']==[]
    assert evidence['repairs']==1 and len(evidence['classification_repairs'])==1


@pytest.mark.parametrize('refuse,drop',[(True,False),(False,True)])
def test_failed_reclassification_or_missing_material_never_publishes_package(tmp_path,refuse,drop):
    ctx=opening_context(tmp_path);model=MisclassifiedOpeningModel(refuse,drop)
    _,result=finish_bridge(ctx,model)
    assert result.status=='failed'
    assert not any(a.kind=='SoundProductionPackage' for a in result.artifacts)
    evidence=ctx.store.json(next(a for a in result.artifacts if a.kind=='AudioArrangementIssueReport'))
    assert evidence['blocking_findings'] and evidence['unresolved_findings']
    assert len(model.calls)<=7


def test_no_repair_budget_stops_before_classification_retry(tmp_path):
    ctx=opening_context(tmp_path);ctx.request.budget.max_repairs_per_stage=0
    model=MisclassifiedOpeningModel();_,result=finish_bridge(ctx,model)
    assert result.status=='failed' and len(model.calls)==3


def test_worker_reclassification_keeps_draft_and_publishes_new_final_artifact(env):
    repo,store=env;model=MisclassifiedOpeningModel()
    identifier=repo.create('alice',production_request(outputs=['audio'],sound_workflow='sound_bridge/1'))
    worker=ProductionWorker(repo,store,{'story':StoryStage(StoryService()),'sound_plan':SoundBridgeStage(model)})
    advance(worker,repo,6)
    job=repo.get(identifier,'alice')
    assert next(s for s in job['stages'] if s['name']=='sound_plan')['status']=='succeeded',job
    refs={a['kind']:ArtifactRef.model_validate(a) for a in job['artifacts']}
    assert validate_package(SimpleNamespace(store=store,request=production_request()),refs).sound_tasks
    assert refs['SoundSourceClassificationDraft'].sha256 != refs['SoundSourceClassification'].sha256
    assert len(model.calls)==6
    assert repo.execution_evidence(identifier,'alice')['policy_repairs']==1


def test_later_approval_cannot_clear_unchanged_missing_sound(tmp_path):
    from my_legend_production.production.audio_simple import SimpleAudioPlanner
    ctx=opening_context(tmp_path)
    ctx.request.budget.max_repairs_per_stage=2
    ref=ArtifactRef.model_validate(ctx.stage['artifacts'][0])
    # Exercise planner without a reclassifier: later reviewers falsely approve the unchanged marker.
    model=MisclassifiedOpeningModel()
    result=SimpleAudioPlanner(model)(ctx,ref,classifications={'line_0001':dict(classification='control_only')})
    assert result.status=='failed' and model.reviews==3
    evidence=ctx.store.json(result.artifacts[0])
    assert evidence['feedback']['unresolved_findings']


def finish_bridge(ctx,model):
    bridge=SoundBridgeStage(model)
    first=bridge(ctx);assert first.status=='running'
    ctx.stage['artifacts'] += [a.model_dump() for a in first.artifacts]
    result=bridge(ctx)
    return bridge,result


def test_opt_in_graph_and_legacy_idempotency():
    old=production_request(outputs=['audio']);new=production_request(outputs=['audio'],sound_workflow='sound_bridge/1')
    assert stage_graph(new)=={'story':[],'audio':['story','sound_plan'],'sound_plan':['story']}
    assert stage_graph(old)=={'story':[],'audio':['story']}
    raw=old.model_dump(exclude={'sound_workflow','audio_revision'})
    assert fingerprint(raw)==fingerprint(old) and fingerprint(old)!=fingerprint(new)
    with pytest.raises(ValidationError):production_request(sound_workflow='sound_bridge/1')


def test_durable_classification_package_and_replay(tmp_path):
    ctx=setup_context(tmp_path);model=BridgeModel();bridge,result=finish_bridge(ctx,model)
    assert result.status=='succeeded' and len(model.calls)==3
    assert bridge(ctx)==result and len(model.calls)==3
    refs={a['kind']:ArtifactRef.model_validate(a) for a in ctx.stage['artifacts']}
    refs.update({a.kind:a for a in result.artifacts})
    package=validate_package(ctx,refs)
    delivery=ctx.store.json(refs['StoryDelivery'])
    spoken=[u for ep in delivery['episodes'] for sc in ep['scenes'] for u in sc['utterances'] if u['kind'] in ('dialogue','narration')]
    assert [t.text for t in package.speech_tasks]==[u['text'] for u in spoken]
    assert all(t.voice and t.performance for t in package.speech_tasks)
    assert package.human_listening=='pending' and package.timing_basis=='resolve_after_real_media'


@pytest.mark.parametrize('fault',['voice','text','review','classification','unresolved','missing'])
def test_audio_rejects_tampered_handoff_before_media_calls(tmp_path,fault):
    ctx=setup_context(tmp_path);_,result=finish_bridge(ctx,BridgeModel())
    refs={a['kind']:ArtifactRef.model_validate(a) for a in ctx.stage['artifacts']}
    refs.update({a.kind:a for a in result.artifacts})
    kind='SoundProductionPackage'
    value=ctx.store.json(refs[kind])
    if fault=='voice':value['speech_tasks'][0]['voice']='wrong'
    elif fault=='text':value['speech_tasks'][0]['text']='删改的台词'
    elif fault=='review':value['review_sha256']='f'*64
    elif fault=='classification':
        kind='SoundSourceClassification';value=ctx.store.json(refs[kind]);value['delivery_sha256']='f'*64
    elif fault=='unresolved':
        kind='AudioArrangementEvidence';value=ctx.store.json(refs[kind]);value['unresolved_findings']=[{'issue':'missing sound'}]
    if fault=='missing':refs.pop(kind)
    else:refs[kind]=ctx.store.put_json(kind,refs[kind].source_version_id,value)
    ctx.stage['artifacts']=[a.model_dump() for a in refs.values()]
    def forbidden(*args):pytest.fail('must reject before audio submission')
    with pytest.raises(ProductionError,match='SOUND_BRIDGE_PACKAGE'):
        AudioStage(tmp_path,tmp_path,runner=forbidden)(ctx)


def test_control_only_keeps_original_line_and_requires_no_material():
    slots,choices,raw=fixture()
    # An isolated stop before any background is an explicit non-audio marker.
    delivery=json.loads(raw);line=delivery['episodes'][0]['scenes'][0]['utterances'][0]
    line['text']='脚步声停住，短暂的安静。'
    raw=json.dumps(delivery,ensure_ascii=False).encode()
    slots[0]['text']=line['text'];slots[0]['sound_intent']='control_only'
    plan,_=compile_intents(slots[:2],{'line_0001':dict(sounds=[],controls=[],simplifications=[])},'fixture',sha256(raw).hexdigest())
    assert plan.events[0].kind=='sfx' and plan.events[0].operations[0].mode=='action_note'
    points=resolve_compound_frames(plan,{'speech_0002':48000})
    assert points['speech_0002','start']==0
    bad={'line_0001':dict(sounds=[dict(cue_text=line['text'],kind='one_shot',reason='cannot generate silence')],controls=[],simplifications=[])}
    with pytest.raises(ValidationError):compile_intents(slots[:2],bad,'fixture',sha256(raw).hexdigest())


def test_control_only_stops_active_background_and_refuses_unresolved_target():
    slots,choices,raw=fixture()
    slots[2]['sound_intent']='control_only'
    choices['line_0003']=dict(sounds=[],controls=[],simplifications=[])
    choices.pop('line_0005')
    with pytest.raises(ValueError,match='CONTROL_TARGET_UNRESOLVED'):
        compile_intents(slots[:4],choices | {},'fixture',sha256(raw).hexdigest())
    choices['line_0003']['controls']=[dict(background_slot='line_0001',action='stop',cue_text='雨声渐弱',reason='fixture stop')]
    plan,_=compile_intents(slots[:4],choices,'fixture',sha256(raw).hexdigest())
    assert plan.events[2].operations[0].mode=='stop_sound'
    assert sum(o.mode in ('speech','one_shot','background') for e in plan.events for o in e.operations)==3


@pytest.mark.parametrize('fault',['classification','review','network'])
def test_failure_is_visible_and_audio_not_started(env,fault):
    repo,store=env;model=BridgeModel(fault);identifier=repo.create('alice',production_request(outputs=['audio'],sound_workflow='sound_bridge/1'))
    def forbidden(ctx):pytest.fail('audio cannot run before handoff passes')
    worker=ProductionWorker(repo,store,{'story':StoryStage(StoryService()),'sound_plan':SoundBridgeStage(model),'audio':forbidden})
    advance(worker,repo,6 if fault=='review' else 5)
    state=repo.get(identifier,'alice')
    assert state['status']==('needs_input' if fault=='network' else 'failed')
    assert next(s for s in state['stages'] if s['name']=='audio')['status']=='queued'
    assert any(a['kind']=='StoryDelivery' for a in state['artifacts'])


def test_worker_handoff_survives_audio_poll_and_never_replans(env):
    repo,store=env;model=BridgeModel();runs=[]
    def runner(*args):
        runs.append(1)
        return 75 if len(runs)==1 else compound_audio(*args)
    def no_plan(*args):pytest.fail('audio must consume published handoff')
    handlers={'story':StoryStage(StoryService()),'sound_plan':SoundBridgeStage(model),
              'audio':AudioStage(store.root.parent,store.root.parent,runner=runner,planner=no_plan)}
    identifier=repo.create('alice',production_request(outputs=['audio'],sound_workflow='sound_bridge/1'))
    worker=ProductionWorker(repo,store,handlers)
    advance(worker,repo,6)
    state=repo.get(identifier,'alice')
    assert next(s for s in state['stages'] if s['name']=='sound_plan')['status']=='succeeded'
    assert len(model.calls)==3
    advance(ProductionWorker(repo,store,handlers),repo,2)
    state=repo.get(identifier,'alice')
    assert state['status']=='succeeded',state
    assert len(model.calls)==3 and len(runs)==2
    assert {a['kind'] for a in state['artifacts']} >= {'SoundProductionPackage','AudioMix','MeasuredTimeline'}


def test_api_checks_bridge_availability_and_serves_frozen_package(env,monkeypatch):
    repo,store=env;app=FastAPI()
    register_production_routes(app,repo,store,lambda req:'alice',capabilities)
    client=TestClient(app);request=production_request(outputs=['audio'],sound_workflow='sound_bridge/1')
    monkeypatch.setenv('MY_LEGEND_PRODUCTION_MODULES','story,audio')
    blocked=client.post('/api/v1/production-jobs',json=request.model_dump())
    assert blocked.status_code==409 and blocked.json()['detail']['modules']==['sound_plan']
    monkeypatch.setenv('MY_LEGEND_PRODUCTION_MODULES','story,sound_plan,audio')
    assert client.get('/api/v1/production-capabilities').json()['data']['sound_workflows']==['sound_bridge/1']
    response=client.post('/api/v1/production-jobs',json=request.model_dump())
    assert response.status_code==202
    identifier=response.json()['data']['id']
    worker=ProductionWorker(repo,store,{'story':StoryStage(StoryService()),'sound_plan':SoundBridgeStage(BridgeModel())})
    advance(worker,repo,6)
    job=client.get('/api/v1/production-jobs/'+identifier).json()['data']
    package=next(a for a in job['artifacts'] if a['kind']=='SoundProductionPackage')
    download=client.get('/api/v1/production-jobs/'+identifier+'/artifacts/'+package['sha256'])
    assert download.status_code==200 and sha256(download.content).hexdigest()==package['sha256']
    assert download.json()['speech_tasks']
    assert next(s for s in job['stages'] if s['name']=='audio')['status']=='queued'
