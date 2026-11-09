"""Flow durability, scope and immutable-version integration on isolated PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from test_production_queue import env
from test_production_contracts import production_request
from test_h5_delivery import seed,worker
from my_legend_production.production.flows import FlowService,FlowRequest,FlowCasting,FlowRevision
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.contracts import StageResult,H5DeliveryRequest
from my_legend_production.production.performance_delivery import prepare_request
from my_legend_production.production.flow_runtime import FlowRepository
from my_legend_production.production.api import register_production_routes


def body():
    return FlowRequest(idempotency_key='flow-request-one',source=production_request().source,
                       casting=FlowCasting(catalog_sha256='a'*64,bindings={'one':'actor'},initial_outfits={'one':'outfit'},casting_confirmed=True))


def service(env,monkeypatch):
    from my_legend_production.production import flows
    monkeypatch.setattr(flows,'freeze_casting',lambda c:c.model_dump()|dict(scene_catalog_sha256='b'*64,capability_sha256='c'*64,visual_policy='asset_stage/1'))
    return FlowService(*env)


def test_create_idempotency_owner_and_get_never_start(env,monkeypatch):
    s=service(env,monkeypatch);b=body()
    with ThreadPoolExecutor(2) as pool:values=list(pool.map(lambda _:s.create('alice',b),range(2)))
    assert values[0]['id']==values[1]['id']
    assert s.repo.list_jobs('alice')['items']==[]
    assert s.get(values[0]['id'],'alice')['jobs']=={}
    with pytest.raises(ProductionError,match='JOB_NOT_FOUND'):s.get(values[0]['id'],'bob')
    changed=b.model_copy(deep=True);changed.source.title='different'
    with pytest.raises(ProductionError,match='IDEMPOTENCY_CONFLICT'):s.create('alice',changed)


def test_child_create_link_crash_recovers_once(env,monkeypatch):
    s=service(env,monkeypatch);f=s.create('alice',body());link=s.link
    monkeypatch.setattr(s,'link',lambda *args:(_ for _ in ()).throw(RuntimeError('simulated crash')))
    with pytest.raises(RuntimeError):s.advance(f['id'],'alice')
    assert len(s.repo.list_jobs('alice')['items'])==1
    monkeypatch.setattr(s,'link',link)
    s.advance(f['id'],'alice');s.advance(f['id'],'alice')
    assert len(s.repo.list_jobs('alice')['items'])==1
    assert s.get(f['id'],'alice')['sound_job_id']


@pytest.mark.parametrize('output',['script','audio'])
def test_nonstage_outputs_enter_same_durable_worker_queue(env,monkeypatch,output):
    s=service(env,monkeypatch);b=body();b.output=output;b.casting=None
    f=s.create('alice',b);s.advance(f['id'],'alice')
    job=s.get(f['id'],'alice')['jobs']['sound']
    assert job['request']['outputs']==[output]
    assert set(x['name'] for x in job['stages'])==({'story'} if output=='script' else {'story','sound_plan','audio'})
    assert FlowRepository(s.repo.dsn,s.repo.schema).claim('flow-worker',stage_names=['story'])['job_id']==job['id']


@pytest.mark.parametrize('output',['script','audio'])
def test_nonstage_completion_does_not_start_visual_calls(env,tmp_path,monkeypatch,output):
    s,_,source,_=completed_flow(env,tmp_path,monkeypatch)
    fid=s.insert('alice','nonstage-'+output,{'output':output},dict(title='作品',duration=90,kind='create',output=output,reuse_sound=source['id']),{},source['work_id'])
    s.advance(fid,'alice');f=s.get(fid,'alice')
    assert f['status']=='succeeded' and not f['stage_job_id'] and f['offstage_roles']==[]


def test_cancel_unlinked_child_and_no_future_stage(env,monkeypatch):
    s=service(env,monkeypatch);f=s.create('alice',body())
    row=s.row(f['id'],'alice');req=deepcopy(row['request']['sound']);req['idempotency_key']='flow:'+f['id']+':sound'
    from my_legend_production.production.contracts import ProductionRequest
    jid=s.repo.create('alice',ProductionRequest.model_validate(req))
    s.control(f['id'],'alice','cancel',1);s.advance(f['id'],'alice')
    assert s.repo.get(jid,'alice')['status']=='cancelled'
    assert s.get(f['id'],'alice')['status']=='cancelled'
    assert len(s.repo.list_jobs('alice')['items'])==1


def test_flow_worker_and_cancel_preserve_unrelated_queue(env,monkeypatch):
    s=service(env,monkeypatch);repo=s.repo
    old=repo.create('alice',production_request(idempotency_key='unrelated-old',outputs=['script']))
    with repo.transaction() as db:db.execute("UPDATE jobs SET deadline=now()-interval '1 second' WHERE id=%s",(old,))
    before=repo.get(old,'alice')
    f=s.create('alice',body());s.advance(f['id'],'alice')
    scoped=FlowRepository(repo.dsn,repo.schema)
    claimed=scoped.claim('flow-worker',stage_names=['story'])
    assert claimed['job_id']==s.get(f['id'],'alice')['sound_job_id']
    s.control(f['id'],'alice','cancel',1)
    assert repo.get(old,'alice')==before


def test_cancel_advance_race_is_serialized(env,monkeypatch):
    s=service(env,monkeypatch);f=s.create('alice',body())
    with ThreadPoolExecutor(2) as pool:
        results=[pool.submit(s.advance,f['id'],'alice'),pool.submit(s.control,f['id'],'alice','cancel',1)]
        for r in results:r.result()
    assert s.get(f['id'],'alice')['status']=='cancelled'
    assert all(j['status']=='cancelled' for j in s.repo.list_jobs('alice')['items'])


def test_failed_stage_resume_preserves_job_identity(env,monkeypatch):
    s=service(env,monkeypatch);f=s.create('alice',body());s.advance(f['id'],'alice')
    claimed=s.repo.claim('test',stage_names=['story'])
    s.repo.finish(claimed,StageResult(status='failed',error_code='LOCAL_TEST_FAILURE'))
    s.advance(f['id'],'alice');failed=s.get(f['id'],'alice')
    assert failed['status']=='failed'
    resumed=s.control(f['id'],'alice','resume',1)
    assert resumed['revision']==2 and resumed['sound_job_id']==failed['sound_job_id']
    with pytest.raises(ProductionError,match='REVISION_CONFLICT'):s.control(f['id'],'alice','resume',1)


def completed_flow(env,tmp_path,monkeypatch):
    repo,store,source,saved=seed(env,tmp_path,monkeypatch)
    s=FlowService(repo,store)
    c=dict(catalog_sha256=saved['catalog_sha256'],bindings={a['role_id']:a['actor_id'] for a in saved['stage']['actors']},
           initial_outfits={a['role_id']:a['initial_outfit_id'] for a in saved['stage']['actors']},casting_confirmed=True)
    request=prepare_request(repo,store,'alice',source['id'],H5DeliveryRequest(preview_sha256=saved['preview_sha256'],idempotency_key='fixture-delivery')).model_dump()
    fid=s.insert('alice','fixture-flow',{'fixture':True},dict(title='作品',duration=90,kind='stage_saved',reuse_sound=source['id'],stage_request=request),c,source['work_id'])
    s.advance(fid,'alice');w=worker(repo,store);assert w.tick() and w.tick();s.advance(fid,'alice')
    return s,fid,source,saved


def test_full_linked_delivery_and_visual_revision_zero_audio(env,tmp_path,monkeypatch):
    s,fid,source,saved=completed_flow(env,tmp_path,monkeypatch)
    f=s.get(fid,'alice');assert f['status']=='succeeded' and 'h5' in f['available_outputs']
    b=FlowRevision(idempotency_key='saved-child-one',expected_revision=1,kind='stage_saved',preview_sha256=saved['preview_sha256'])
    child=s.revise(fid,'alice',b);s.advance(child['id'],'alice')
    w=worker(s.repo,s.store);assert w.tick() and w.tick();s.advance(child['id'],'alice')
    result=s.get(child['id'],'alice')
    assert result['parent_id']==fid and result['sound_job_id']==source['id'] and result['actual_calls']==0
    assert s.repo.get(source['id'],'alice')==source
    assert s.revise(fid,'alice',b)['id']==child['id']
    assert len(s.listing('alice')['items'])==1 and len(s.listing('alice',work_id=f['work_id'])['items'])==2
    assert s.repo.list_jobs('alice',exclude_flows=True)['items']==[]


@pytest.mark.parametrize('kind',['revise','continue','remix','audio_full'])
def test_revision_rebuilds_required_downstream(env,tmp_path,monkeypatch,kind):
    s,fid,source,_=completed_flow(env,tmp_path,monkeypatch)
    b=FlowRevision(idempotency_key='revision-'+kind,expected_revision=1,kind=kind,instruction='修改结尾',retain_elements=['人物'],transform_elements=['地点'])
    f=s.revise(fid,'alice',b);row=s.row(f['id'],'alice')
    assert 'reuse_sound' not in row['request'] and 'stage_request' not in row['request']
    assert row['request']['sound']['source']['source_version_id']==source['source_version_id']
    assert (row['work_id']!=source['work_id'])==(kind=='remix')
    assert row['casting']==s.row(fid,'alice')['casting']
    assert s.repo.get(source['id'],'alice')==source


def test_api_get_readonly_and_unauthorized_revision(env,monkeypatch):
    s=service(env,monkeypatch);app=FastAPI()
    caps={'h5_flow':{'configured':True}}
    register_production_routes(app,s.repo,s.store,lambda r:r.headers.get('x-owner','alice'),lambda:caps)
    client=TestClient(app)
    response=client.post('/api/v1/production-flows',json=body().model_dump());assert response.status_code==202,response.text
    fid=response.json()['data']['id']
    assert client.get('/api/v1/production-flows/'+fid).status_code==200
    assert client.get('/api/v1/production-flows/'+fid,headers={'x-owner':'bob'}).status_code==404
    caps['h5_flow']['configured']=False
    replay=client.post('/api/v1/production-flows',json=body().model_dump())
    assert replay.status_code==202 and replay.json()['data']['id']==fid
    assert not s.repo.list_jobs('alice')['items']


def test_casting_rejects_unselected_or_incomplete_roles(env,monkeypatch):
    s=service(env,monkeypatch);b=body();b.casting.bindings={'unknown':'actor'};b.casting.initial_outfits={'unknown':'outfit'}
    with pytest.raises(ProductionError,match='DIRECTING_CASTING_INVALID'):s.create('alice',b)
    with pytest.raises(ValidationError):FlowCasting(catalog_sha256='a'*64,bindings={'one':'actor'},initial_outfits={'two':'outfit'},casting_confirmed=True)


def test_unknown_provider_result_never_auto_replays(env,monkeypatch):
    s=service(env,monkeypatch);f=s.create('alice',body());s.advance(f['id'],'alice')
    stage=s.repo.claim('unknown-test',stage_names=['story'])
    s.repo.reserve_call(stage,'story:model-test','d'*64,1)
    s.repo.unknown_call(stage,'story:model-test')
    s.repo.finish(stage,StageResult(status='needs_input',error_code='EXTERNAL_RESULT_UNKNOWN'))
    s.advance(f['id'],'alice');before=s.get(f['id'],'alice')
    assert before['status']=='needs_input'
    s.tick();s.tick()
    assert s.get(f['id'],'alice')==before
    assert len(s.repo.list_jobs('alice')['items'])==1


def test_corrupt_audio_is_not_a_playable_flow(env,tmp_path,monkeypatch):
    s,fid,source,_=completed_flow(env,tmp_path,monkeypatch)
    from my_legend_production.production.contracts import ArtifactRef
    ref=ArtifactRef.model_validate(next(a for a in source['artifacts'] if a['kind']=='AudioMix'))
    s.store.verify(ref).write_bytes(b'corrupt')
    with pytest.raises(ProductionError):s.get(fid,'alice')


@pytest.mark.parametrize('status,error_code', [('needs_input','EXTERNAL_RESULT_UNKNOWN'),('cancelled',None),('failed','AUDIO_FAILED')])
def test_unknown_source_requires_explicit_new_audio_ack_and_preserves_ledger(env,tmp_path,monkeypatch,status,error_code):
    s,fid,source,_=completed_flow(env,tmp_path,monkeypatch)
    with s.repo.transaction() as db:
        db.execute("UPDATE jobs SET status=%s,error_code=%s WHERE id=%s",(status,error_code,source['id']))
        db.execute("INSERT INTO calls(job_id,call_key,stage_name,input_hash,state,reserved_units) VALUES(%s,'audio:unknown','audio','hash','unknown',1)",(source['id'],))
    before=s.repo.get(source['id'],'alice')
    b=FlowRevision(idempotency_key='explicit-new-audio',expected_revision=1,kind='audio_full')
    with pytest.raises(ProductionError,match='AUDIO_REMAKE_SOURCE_NOT_READY'):s.revise(fid,'alice',b)
    b.acknowledge_unknown_source=True
    child=s.revise(fid,'alice',b);s.advance(child['id'],'alice')
    assert s.get(child['id'],'alice')['sound_job_id']!=source['id']
    assert s.repo.get(source['id'],'alice')==before
    assert s.row(child['id'],'alice')['request']['unknown_source_acknowledgement']['new_paid_version']
    assert s.revise(fid,'alice',b)['id']==child['id']


def test_stage_create_link_crash_and_casting_snapshot(env,tmp_path,monkeypatch):
    s,fid,source,saved=completed_flow(env,tmp_path,monkeypatch)
    child=s.revise(fid,'alice',FlowRevision(idempotency_key='stage-crash-version',expected_revision=1,kind='stage_saved',preview_sha256=saved['preview_sha256']))
    original=s.link
    def fail_stage(flow_id,phase,jid):
        if phase=='stage':raise RuntimeError('simulated interrupted stage link')
        return original(flow_id,phase,jid)
    monkeypatch.setattr(s,'link',fail_stage)
    with pytest.raises(RuntimeError):s.advance(child['id'],'alice')
    before=len(s.repo.list_jobs('alice')['items'])
    monkeypatch.setattr(s,'link',original);s.advance(child['id'],'alice')
    assert len(s.repo.list_jobs('alice')['items'])==before
    assert s.get(child['id'],'alice')['casting']==s.get(fid,'alice')['casting']


def test_automatic_stage_uses_submission_snapshot_after_catalog_pointer_changes(env,tmp_path,monkeypatch):
    from test_spine_director import seeded
    repo,store,source,_,request=seeded(env,tmp_path,monkeypatch,asset_policy=True)
    c={k:getattr(request.source,k) for k in ('catalog_sha256','scene_catalog_sha256','capability_sha256','visual_policy','bindings','initial_outfits')}
    c['casting_confirmed']=True
    s=FlowService(repo,store)
    fid=s.insert('alice','frozen-catalog-flow',{'fixture':'catalog-switch'},dict(title='冻结目录',duration=90,kind='stage',reuse_sound=source['id']),c,source['work_id'])
    # A later catalog switch must not affect an already submitted version.
    (tmp_path/'commander-scene-current.json').write_text('{"catalog_sha256":"'+'f'*64+'"}')
    s.advance(fid,'alice')
    child=s.get(fid,'alice')['jobs']['stage']['request']['source']
    for k in ('catalog_sha256','scene_catalog_sha256','capability_sha256','visual_policy','bindings','initial_outfits'):
        assert child[k]==c[k]
