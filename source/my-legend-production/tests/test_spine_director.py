from copy import deepcopy
import pytest
from test_commander_preview import commander
from my_legend_production.production.contracts import DirectedPerformanceSource
from my_legend_production.production.directing_compile import create_context, compile_plan, required_checks, validate_review
from my_legend_production.production.directing_contracts import DirectingReview
from my_legend_production.production.life_contracts import LifeValidationError
from my_legend_production.production.repository import ProductionError
from test_production_queue import env


def seeded(env,tmp_path,monkeypatch,legacy_budget=True,asset_policy=False):
    import json
    from test_h5_delivery import seed
    from my_legend_production.production import spine_director as director
    from my_legend_production.production.contracts import DirectingRequest
    repo,store,job,saved=seed(env,tmp_path,monkeypatch)
    monkeypatch.setattr(director,'verify_resources',lambda value,**kw:{'character_catalog':value['catalog_sha256']})
    (tmp_path/'commander-scene-current.json').write_text(json.dumps({'catalog_sha256':saved['scene_track']['segments'][0]['scene']['catalog_sha256']}))
    body=DirectingRequest(idempotency_key='directing-test',catalog_sha256=saved['catalog_sha256'],
        bindings={a['role_id']:a['actor_id'] for a in saved['stage']['actors']},
        initial_outfits={a['role_id']:a['initial_outfit_id'] for a in saved['stage']['actors']},casting_confirmed=True)
    request=director.prepare_directing_request(repo,store,'alice',job['id'],body)
    if not asset_policy:
        # This fixture covers historical literal-policy jobs unless requested.
        request.source=request.source.model_copy(update={'visual_policy':None,'capability_sha256':None})
    if legacy_budget:
        # Existing jobs keep their original one-repair/four-call budget.
        request.budget=request.budget.model_copy(update={'max_provider_calls':4,'max_repairs_per_stage':1,'max_elapsed_seconds':2700})
    return repo,store,job,saved,request


class Model:
    def __init__(self,invalid=False,unknown=False):self.calls=0;self.invalid=invalid;self.unknown=unknown
    def complete(self,system,payload,schema,timeout):
        import json
        self.calls+=1
        if self.unknown:raise RuntimeError('uncertain')
        if 'required_checks' in payload:
            quotes={'line:'+':'.join(l['source'][k] for k in ('episode_id','scene_id','line_id')):l['text'] for l in payload['input']['source_lines']}
            gaps={'line:'+':'.join(a['source'][k] for k in ('episode_id','scene_id','line_id')) for a in payload['plan']['assessments'] if a['classification']=='gap'}
            valid=not self.invalid and payload['execution'].get('preflight_passed') is not False
            value=dict(schema_version='spine_directing_review/1.2',passed=valid,checks=[dict(key=k,passed=valid,
                reason='核对原文和执行摘要',quote=quotes.get(k,''),coverage_complete=(k not in gaps) if k in quotes else None,
                unperformed_actions=[quotes[k]] if k in gaps else [],unrendered_details=[]) for k in payload['required_checks']])
        else:
            value=dict(scenes=[dict(episode_id='ep',scene_id='scene',resource_scene_id='library',reason='现有图书馆',difference='',actors=[dict(role_id='a',x=.333,facing=1,visible=True),dict(role_id='b',x=.667,facing=-1,visible=True)])],
                assessments=[dict(source=l['source'],quote=l['text'],classification='gap' if l['kind']=='action' else 'no_visible_action',reason='动作暂不执行并明确报告' if l['kind']=='action' else '普通对白') for l in payload['source_lines']])
        return {'content':json.dumps(value,ensure_ascii=False)}


def run_worker(repo,store,model):
    from my_legend_production.production import spine_director as d
    from my_legend_production.production.performance_delivery import PerformancePackageStage
    from my_legend_production.production.worker import ProductionWorker
    return ProductionWorker(repo,store,{'performance_context':d.PerformanceContextStage(),'spine_direct':d.SpineDirectStage(model),
        'spine_compile':d.SpineCompileStage(),'performance_package':PerformancePackageStage()},only_configured_stages=True)


def test_full_worker_delivery_and_manual_revision(env,tmp_path,monkeypatch):
    from my_legend_production.production.performance_delivery import delivered_preview
    from my_legend_production.production.spine_director import read_directed_preview
    from my_legend_production.production.directing_edit import edit_directed
    from my_legend_production.production.spine_preview import StagePreviewRequest,save_preview
    repo,store,source,old,request=seeded(env,tmp_path,monkeypatch)
    jid=repo.create('alice',request);assert repo.create('alice',request)==jid
    model=Model();w=run_worker(repo,store,model)
    for _ in range(4):assert w.tick()
    job=repo.get(jid,'alice');assert job['status']=='succeeded',job
    assert model.calls==job['actual_calls']==2
    result=delivered_preview(job,store);value=result['preview']
    assert result['manifest']['model_review']=='passed_with_gaps' and result['manifest']['human_review']=='pending'
    assert value['audio']==old['audio'] and repo.get(source['id'],'alice')==source
    assert read_directed_preview(tmp_path,source,value['preview_sha256'],store)==value
    body=StagePreviewRequest(profile='life_v2',operation='validate_and_save',directed_from=value['preview_sha256'],
        script=value['stage'],scene_bindings=value['scene_bindings'],furniture_track=value['furniture_track'],
        expression_track=[dict(id='manual-eye',instance_id='actor_1',start_frame=1,end_frame=1000,channel='eyes',preset='closed')],
        mouth_enabled=value['mouth_enabled'])
    edited=save_preview(tmp_path,edit_directed(tmp_path,source,body,store))
    assert edited['directing']['review_status']=='edited_pending'
    assert read_directed_preview(tmp_path,source,edited['preview_sha256'],store)==edited
    assert read_directed_preview(tmp_path,source,value['preview_sha256'],store)==value


@pytest.mark.parametrize('unknown',[False,True])
def test_bounded_repair_and_unknown_result(env,tmp_path,monkeypatch,unknown):
    repo,store,source,old,request=seeded(env,tmp_path,monkeypatch)
    jid=repo.create('alice',request);model=Model(invalid=True,unknown=unknown);w=run_worker(repo,store,model)
    assert w.tick() and w.tick()
    job=repo.get(jid,'alice')
    assert model.calls==(1 if unknown else 4)
    assert job['status']==('needs_input' if unknown else 'failed')
    assert not w.tick()
    assert repo.get(source['id'],'alice')==source
    assert any(a['kind']=='SpineDirectingIssueReport' for a in job['artifacts'])
    if unknown:assert job['unknown_calls']==1 and job['reserved_calls']==1


def test_new_jobs_retain_feedback_across_two_repairs(env,tmp_path,monkeypatch):
    from my_legend_production.production.contracts import ArtifactRef
    repo,store,source,old,request=seeded(env,tmp_path,monkeypatch,legacy_budget=False)
    assert request.budget.max_provider_calls==6 and request.budget.max_repairs_per_stage==2
    class TwiceReviewed(Model):
        reviews=0
        feedback=[]
        def complete(self,system,payload,schema,timeout):
            if 'required_checks' in payload:
                self.reviews+=1;self.invalid=self.reviews<3
            if 'repair_feedback' in payload:self.feedback.append(deepcopy(payload['repair_feedback']))
            return super().complete(system,payload,schema,timeout)
    model=TwiceReviewed();jid=repo.create('alice',request);w=run_worker(repo,store,model)
    for _ in range(4):assert w.tick()
    job=repo.get(jid,'alice')
    assert job['status']=='succeeded' and model.calls==job['actual_calls']==6
    assert len(model.feedback)==2 and all(f['previous_review']['passed'] is False for f in model.feedback)
    evidence=store.json(ArtifactRef.model_validate(next(a for a in job['artifacts'] if a['kind']=='SpineDirectingEvidence')))
    assert evidence['repairs']==2 and [r['attempt'] for r in evidence['responses']]==[0,0,1,1,2,2]


def test_new_two_repair_job_stops_on_unknown_first_call(env,tmp_path,monkeypatch):
    repo,store,source,old,request=seeded(env,tmp_path,monkeypatch,legacy_budget=False)
    model=Model(unknown=True);jid=repo.create('alice',request);w=run_worker(repo,store,model)
    assert w.tick() and w.tick() and not w.tick()
    job=repo.get(jid,'alice')
    assert model.calls==1 and job['status']=='needs_input' and job['unknown_calls']==1


def test_api_cast_only_ownership_idempotency_and_cancel(env,tmp_path,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from my_legend_production.production.api import register_production_routes
    from my_legend_production.production.spine_director import DIRECTING_STAGES
    repo,store,source,old,request=seeded(env,tmp_path,monkeypatch)
    app=FastAPI();register_production_routes(app,repo,store,lambda r:r.headers.get('x-owner','alice'),
        lambda:{'modules':{n:{'configured':True} for n in DIRECTING_STAGES}})
    client=TestClient(app);url=f"/api/v1/production-jobs/{source['id']}/h5-directing-jobs"
    body={k:request.source.model_dump()[k] for k in ('bindings','initial_outfits','catalog_sha256','casting_confirmed')}
    body['idempotency_key']='api-directing-test'
    response=client.post(url,json=body);assert response.status_code==202,response.text
    jid=response.json()['data']['id']
    assert client.post(url,json=body).json()['data']['id']==jid
    assert client.post(url,json=body,headers={'x-owner':'bob'}).status_code==404
    assert client.post(url,json=body|{'scene_bindings':[]}).status_code==422
    assert client.post(url,json=body|{'casting_confirmed':False}).status_code==422
    assert client.post(url,json=body|{'catalog_sha256':'0'*64}).status_code==409
    assert client.post(url,json=body|{'initial_outfits':{'a':'female1','b':'female1'}}).status_code==409
    repo.cancel(jid,'alice');model=Model();assert not run_worker(repo,store,model).tick()
    assert model.calls==0 and repo.get(jid,'alice')['status']=='cancelled'


def test_restart_reuses_completed_model_calls(env,tmp_path,monkeypatch):
    from my_legend_production.production.spine_director import SpineDirectStage
    from my_legend_production.production.worker import StageContext
    repo,store,source,old,request=seeded(env,tmp_path,monkeypatch)
    jid=repo.create('alice',request);model=Model();w=run_worker(repo,store,model)
    assert w.tick()
    stage=repo.claim('interrupted',stage_names=['spine_direct'])
    first=SpineDirectStage(model)(StageContext(repo,store,stage));assert model.calls==2
    # Simulate interruption after persisted provider results but before stage commit.
    with repo.transaction() as db:db.execute("UPDATE stages SET lease_until=now()-interval '1 second' WHERE job_id=%s AND name='spine_direct'",(jid,))
    assert w.tick() and w.tick() and w.tick()
    job=repo.get(jid,'alice');assert job['status']=='succeeded',job
    assert model.calls==2 and job['actual_calls']==2
    assert {a.sha256 for a in first.artifacts}<={a['sha256'] for a in job['artifacts']}


def sample(tmp_path):
    store, job, catalog, body = commander(tmp_path, '甲走到书架前并翻找。')
    source = DirectedPerformanceSource(source_job_id=job['id'],source_version_id='v1',inputs=job['artifacts'],
        catalog_sha256=catalog['catalog_sha256'],scene_catalog_sha256=body.scene_bindings[0]['scene']['catalog_sha256'],
        bindings=body.bindings,initial_outfits={'a':'male1','b':'female1'})
    context=create_context(job,source,store,tmp_path)
    lines=context['model_input']['source_lines']
    plan=dict(scenes=[dict(episode_id='ep',scene_id='scene',resource_scene_id='library',reason='书架对应图书馆',difference='',
        actors=[dict(role_id='a',x=.333,facing=1,visible=True),dict(role_id='b',x=.667,facing=-1,visible=True)])],
        actions=[dict(id='approach',role_id='a',anchor={'source':lines[-1]['source']},quote='走到书架前',reason='原文实际发生',
            kind='move',target_kind='object',target_id='bookcase_front')],
        expressions=[dict(id='eyes',role_id='b',anchor={'source':lines[1]['source']},quote='你好',reason='问候时微笑',channel='eyes',preset='smile',duration_ms=1000)],
        assessments=[dict(source=l['source'],quote=l['text'],classification='gap' if i==2 else 'no_visible_action',
            cue_ids=['approach'] if i==2 else [],reason='翻找没有对应动作' if i==2 else '对白无实际动作') for i,l in enumerate(lines)])
    return store,job,source,context,plan


def test_fresh_plan_and_honest_gaps(tmp_path):
    store,job,source,context,plan=sample(tmp_path)
    before=deepcopy(job)
    value,summary=compile_plan(job,store,context,plan,tmp_path)
    assert value['schema_version']=='spine_stage_preview/3.2'
    assert value['review_confirmed'] is False
    assert all(s['confirmation_source']=='model' and not s['confirmed'] for s in value['scene_track']['segments'])
    assert value['coverage'][-1]['status']=='needs_correction'
    assert value['stage']['actors'][0]['initial_outfit_id']=='male1'
    assert not context.get('old_visual_tracks') and job==before
    assert value==compile_plan(job,store,context,plan,tmp_path)[0]


@pytest.mark.parametrize('case',['actor','scene','source','quote','coverage','collision','outfit'])
def test_invalid_plan_is_rejected(tmp_path,case):
    store,job,source,context,plan=sample(tmp_path)
    if case=='actor':plan['actions'][0]['role_id']='new_actor'
    if case=='scene':plan['scenes'][0]['resource_scene_id']='imaginary'
    if case=='source':plan['actions'][0]['anchor']['source']={**plan['actions'][0]['anchor']['source'],'line_id':'absent'}
    if case=='quote':plan['actions'][0]['quote']='不存在的原文'
    if case=='coverage':plan['assessments'].pop()
    if case=='collision':plan['scenes'][0]['actors'][1]['x']=.333
    if case=='outfit':context['initial_outfits']['a']='female1'
    with pytest.raises((LifeValidationError,ProductionError)):compile_plan(job,store,context,plan,tmp_path)


def test_time_conflict_is_not_clipped_or_omitted(tmp_path):
    store,job,source,context,plan=sample(tmp_path)
    plan['actions'][0]['duration_ms']=30000
    value,summary=compile_plan(job,store,context,plan,tmp_path)
    assert not value['stage']['cues']
    assert summary['issues'][0]['code']=='DIRECTING_TIME_CONFLICT'
    assert value['coverage'][-1]['status']=='needs_correction'
    assert value['coverage_decisions']==[]
    plan['actions'][0]['target_id']='imaginary'
    with pytest.raises(LifeValidationError):compile_plan(job,store,context,plan,tmp_path)


def test_silent_role_cannot_receive_talking_mouth_override(tmp_path):
    store,job,source,context,plan=sample(tmp_path)
    plan['expressions'][0].update(channel='mouth',preset='open',role_id='a')
    with pytest.raises(LifeValidationError) as error:compile_plan(job,store,context,plan,tmp_path)
    assert '实际对白' in error.value.diagnostics[0]['message']


def test_independent_review_requires_all_source_evidence(tmp_path):
    store,job,source,context,plan=sample(tmp_path)
    quotes={'line:'+':'.join(l['source'].values()):l['text'] for l in context['model_input']['source_lines']}
    review=DirectingReview(passed=True,checks=[dict(key=k,passed=True,reason='核对通过',quote=quotes.get(k,'')) for k in required_checks(context)])
    validate_review(review,context)
    review.checks[-1].quote='伪造'
    with pytest.raises(ProductionError):validate_review(review,context)


def test_preflight_reports_expression_and_path_conflicts_together(tmp_path):
    store,job,source,context,plan=sample(tmp_path)
    plan['expressions'][0]['preset']='surprised'
    plan['actions'][0].update(target_kind='slot',target_id='slot_1')
    original=deepcopy(plan)
    with pytest.raises(LifeValidationError) as error:compile_plan(job,store,context,plan,tmp_path)
    diagnostics={d['path']:d for d in error.value.diagnostics}
    assert 'eyes' in diagnostics and 'approach' in diagnostics
    assert 'surprised' not in diagnostics['eyes']['allowed']
    assert 'positions_at_start' in diagnostics['approach']
    assert 'positions_at_end' in diagnostics['approach']
    assert plan==original


def test_planning_schema_uses_frozen_actor_capabilities(tmp_path):
    from my_legend_production.production.directing_compile import planning_schema
    store,job,source,context,plan=sample(tmp_path)
    schema=planning_schema(context)
    assert schema['$defs']['DirectedAction']['properties']['role_id']['enum']==['a','b']
    assert 'surprised' not in schema['$defs']['DirectedExpression']['properties']['preset']['enum']
    assert 'library' in schema['$defs']['DirectedScene']['properties']['resource_scene_id']['enum']
    assert schema['$defs']['AudioEventRef']['enum']==[l['source'] for l in context['model_input']['source_lines']]


def test_noop_move_does_not_mask_other_preflight_failures(tmp_path):
    store,job,source,context,plan=sample(tmp_path)
    plan['scenes'][0]['actors'][0]['x']=.333
    plan['actions'][0].update(target_kind='slot',target_id='slot_0')
    plan['expressions'][0]['preset']='surprised'
    with pytest.raises(LifeValidationError) as error:compile_plan(job,store,context,plan,tmp_path)
    errors={d['path']:d for d in error.value.diagnostics}
    assert '原地行走' in errors['approach']['message']
    assert 'eyes' in errors


def test_all_mixed_source_anchors_are_reported_without_remapping(tmp_path):
    store,job,source,context,plan=sample(tmp_path)
    for kind in ('actions','expressions'):
        plan[kind][0]['anchor']['source']={**plan[kind][0]['anchor']['source'],'scene_id':'wrong-scene'}
    before=deepcopy(plan)
    with pytest.raises(LifeValidationError) as error:compile_plan(job,store,context,plan,tmp_path)
    assert {d['path'] for d in error.value.diagnostics}=={'approach:anchor','eyes:anchor'}
    assert all(d['matching_source_lines'][0]['scene_id']=='scene' for d in error.value.diagnostics)
    assert plan==before


def test_coverage_reports_each_wrong_source_reference_and_empty_claim(tmp_path):
    store,job,source,context,plan=sample(tmp_path)
    plan['assessments'][0].update(classification='performed',cue_ids=['approach'])
    plan['assessments'][1].update(classification='performed',cue_ids=[])
    with pytest.raises(LifeValidationError) as error:compile_plan(job,store,context,plan,tmp_path)
    assert len(error.value.diagnostics)==2
    wrong,empty=error.value.diagnostics
    assert wrong['source']==plan['assessments'][0]['source']
    assert wrong['invalid_cues']==[{'cue_id':'approach','actual_source':plan['actions'][0]['anchor']['source']}]
    assert empty['source']==plan['assessments'][1]['source']


def test_malformed_envelope_still_reports_known_plan_failures_for_repair(env,tmp_path,monkeypatch):
    import json
    repo,store,source,old,request=seeded(env,tmp_path,monkeypatch)
    class InspectModel(Model):
        feedback=None
        def complete(self,system,payload,schema,timeout):
            first=self.calls==0
            if payload.get('repair_feedback'):self.feedback=payload['repair_feedback']
            result=super().complete(system,payload,schema,timeout)
            if first:
                assert 'repair_feedback' not in payload
                plan=json.loads(result['content']);plan['repair_feedback']=None
                plan['scenes'][0]['actors'][0]['x']=.333
                line=payload['source_lines'][-1]
                plan['actions']=[dict(id='noop',role_id='a',anchor={'source':line['source']},quote=line['text'],
                    reason='test invalid move',kind='move',target_kind='slot',target_id='slot_0')]
                result['content']=json.dumps(plan)
            return result
    model=InspectModel();jid=repo.create('alice',request);w=run_worker(repo,store,model)
    for _ in range(4):assert w.tick()
    assert repo.get(jid,'alice')['status']=='succeeded' and model.calls==3
    assert 'Extra inputs' in model.feedback['details']
    diagnostics=model.feedback['preflight_diagnostics']
    assert diagnostics['acceptance_allowed'] is False
    assert any(d['path']=='noop' for d in diagnostics['diagnostics'])


def test_repair_keeps_rejected_review_without_duplicate_plan_text(env,tmp_path,monkeypatch):
    repo,store,source,old,request=seeded(env,tmp_path,monkeypatch)
    class InspectModel(Model):
        feedback=None
        def complete(self,system,payload,schema,timeout):
            if payload.get('repair_feedback'):self.feedback=payload['repair_feedback']
            return super().complete(system,payload,schema,timeout)
    model=InspectModel(invalid=True)
    jid=repo.create('alice',request);w=run_worker(repo,store,model)
    assert w.tick() and w.tick()
    assert model.calls==4 and repo.get(jid,'alice')['status']=='failed'
    assert 'previous_plan' in model.feedback and 'previous_response' not in model.feedback
    assert model.feedback['previous_review']['passed'] is False


def test_output_limit_is_recorded_and_uses_only_one_repair(env,tmp_path,monkeypatch):
    repo,store,source,old,request=seeded(env,tmp_path,monkeypatch)
    class LimitedModel(Model):
        feedback=None
        def complete(self,system,payload,schema,timeout):
            if payload.get('repair_feedback'):self.feedback=payload['repair_feedback']
            response=super().complete(system,payload,schema,timeout)
            response['finish_reason']='length'
            return response
    model=LimitedModel();jid=repo.create('alice',request);w=run_worker(repo,store,model)
    assert w.tick() and w.tick() and not w.tick()
    job=repo.get(jid,'alice')
    assert model.calls==job['actual_calls']==2
    assert job['status']=='failed' and job['error_code']=='DIRECTING_OUTPUT_LIMIT'
    assert model.feedback['error_code']=='DIRECTING_OUTPUT_LIMIT'
    assert all(a['kind']!='SpineDirectingReview' for a in job['artifacts'])


def test_explicit_resume_rechecks_saved_final_plan_and_only_spends_review_slot(env,tmp_path,monkeypatch):
    from my_legend_production.production import spine_director
    from my_legend_production.production.life_contracts import LifeValidationError
    repo,store,source,_,request=seeded(env,tmp_path,monkeypatch,legacy_budget=False)
    original=spine_director.compile_plan
    def local_failure(*args,**kwargs):
        raise LifeValidationError([dict(path='fixture',code='INVALID',message='local compiler defect')])
    monkeypatch.setattr(spine_director,'compile_plan',local_failure)
    jid=repo.create('alice',request);model=Model();w=run_worker(repo,store,model)
    assert w.tick() and w.tick()
    failed=repo.get(jid,'alice')
    assert failed['status']=='failed' and failed['actual_calls']==model.calls==5
    monkeypatch.setattr(spine_director,'compile_plan',original)
    repo.resume(jid,'alice',failed['revision'],{})
    assert w.tick() and w.tick() and w.tick()
    done=repo.get(jid,'alice')
    assert done['status']=='succeeded' and done['actual_calls']==model.calls==6
    assert repo.get(source['id'],'alice')==source
