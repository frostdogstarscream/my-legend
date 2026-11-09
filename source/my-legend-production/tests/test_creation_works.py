from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_production_queue import env
from test_production_flows import service,body,completed_flow
from my_legend_production.production.works import CreationService,DraftCreate,DraftUpdate
from my_legend_production.production.flows import FlowRequest,FlowRevision
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.api import register_production_routes

def setup(env,monkeypatch):
    f=service(env,monkeypatch);s=CreationService(f);b=body()
    d=DraftCreate(idempotency_key='draft-create-01',source=b.source.model_dump(),casting=b.casting)
    return s,d

def submission(d,key='draft-submit-01'):
    return FlowRequest(**d['payload'],draft_id=d['id'],expected_draft_revision=d['revision'],idempotency_key=key)

def test_unnamed_partial_draft_and_automatic_title_preserve_request(env,monkeypatch):
    from pydantic import ValidationError
    s,b=setup(env,monkeypatch);b.source.title='';b.source.idea=''
    draft=s.create('alice',b)
    assert s.works('alice')['items'][0]['title']=='未命名故事'
    with pytest.raises(ValidationError):submission(draft)
    update=DraftUpdate(**{**draft['payload'],'source':{**draft['payload']['source'],'idea':'两个人在桥边相遇'}},expected_revision=1)
    draft=s.update('alice',draft['id'],update);flow=s.submit('alice',submission(draft))
    assert flow['title']=='故事制作中'
    before=s.flows.row(flow['id'],'alice')
    ref=s.store.put_json('StoryDelivery','test-auto-v1',{'title':'桥边的灯'})
    s.flows.capture_title(before,{'artifacts':[ref.model_dump()]})
    after=s.flows.row(flow['id'],'alice')
    assert after['request']==before['request'] and after['request_hash']==before['request_hash']
    assert s.flows.get(flow['id'],'alice')['title']=='桥边的灯'
    assert s.works('alice')['items'][0]['title']=='桥边的灯'
    assert s.submit('alice',submission(draft))['id']==flow['id']
    other=s.store.put_json('StoryDelivery','test-auto-v2',{'title':'不能覆盖首次标题'})
    s.flows.capture_title(after,{'artifacts':[other.model_dump()]})
    assert s.flows.get(flow['id'],'alice')['title']=='桥边的灯'

def test_user_title_has_priority(env,monkeypatch):
    s,b=setup(env,monkeypatch);d=s.create('alice',b);f=s.submit('alice',submission(d))
    ref=s.store.put_json('StoryDelivery','test-auto-v1',{'title':'模型给出的标题'})
    s.flows.capture_title(s.flows.row(f['id'],'alice'),{'artifacts':[ref.model_dump()]})
    assert s.flows.get(f['id'],'alice')['title']==b.source.title

def test_complete_draft_roundtrip_cas_and_ownership(env,monkeypatch):
    s,b=setup(env,monkeypatch);d=s.create('alice',b)
    assert s.create('alice',b)==d
    assert d['payload']['casting']==b.casting.model_dump()
    assert d['payload']['source']['target_duration_seconds']==90
    update=DraftUpdate(**{**d['payload'],'source':{**d['payload']['source'],'title':'新标题'}},expected_revision=1)
    changed=s.update('alice',d['id'],update);assert changed['revision']==2
    with pytest.raises(ProductionError,match='DRAFT_REVISION_CONFLICT'):s.update('alice',d['id'],update)
    with pytest.raises(ProductionError,match='JOB_NOT_FOUND'):s.draft('bob',d['id'])
    with pytest.raises(ProductionError,match='JOB_NOT_FOUND'):s.update('bob',d['id'],update)
    with pytest.raises(ProductionError,match='JOB_NOT_FOUND'):s.submit('bob',submission(changed))
    assert s.works('bob')['items']==[]


def test_archived_work_leaves_list_but_keeps_owned_direct_link(env,monkeypatch):
    s,b=setup(env,monkeypatch);d=s.create('alice',b)
    with s.repo.transaction() as db:
        db.execute('INSERT INTO work_archives(owner_id,work_id,reason) VALUES(%s,%s,%s)',('bob',d['work_id'],'test'))
    assert len(s.works('alice')['items'])==1
    with s.repo.transaction() as db:
        db.execute('INSERT INTO work_archives(owner_id,work_id,reason) VALUES(%s,%s,%s)',('alice',d['work_id'],'test'))
    assert s.works('alice')['items']==[]
    assert s.work('alice',d['work_id'])['open']['query']['draftId']==d['id']
    assert s.draft('alice',d['id'])==d
    with pytest.raises(ProductionError,match='JOB_NOT_FOUND'):s.work('bob',d['work_id'])

def test_submit_concurrent_different_keys_same_revision_one_flow_no_jobs(env,monkeypatch):
    s,b=setup(env,monkeypatch);d=s.create('alice',b)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda key:s.submit('alice',submission(d,key)),['draft-submit-01','draft-submit-02']))
    assert results[0]['id']==results[1]['id']
    assert s.repo.list_jobs('alice')['items']==[]
    assert s.drafts('alice')['items']==[]
    works=s.works('alice')['items'];assert len(works)==1 and works[0]['id']==d['work_id']
    with pytest.raises(ProductionError,match='DRAFT_ALREADY_SUBMITTED'):s.update('alice',d['id'],DraftUpdate(**d['payload'],expected_revision=1))
    mismatch=submission(d);mismatch.source.title='changed'
    with pytest.raises(ProductionError,match='DRAFT_SNAPSHOT_MISMATCH'):s.submit('alice',mismatch)

def test_flow_and_draft_link_rollback_together(env,monkeypatch):
    s,b=setup(env,monkeypatch);d=s.create('alice',b);insert=s.flows.insert
    def crash(*a,**kw):insert(*a,**kw);raise RuntimeError('crash before draft link')
    monkeypatch.setattr(s.flows,'insert',crash)
    with pytest.raises(RuntimeError):s.submit('alice',submission(d))
    assert s.flows.listing('alice')['items']==[] and s.draft('alice',d['id'])['flow_id'] is None
    monkeypatch.setattr(s.flows,'insert',insert)
    assert s.submit('alice',submission(d))['work_id']==d['work_id']

def test_failed_new_version_keeps_old_playable_and_read_only(env,tmp_path,monkeypatch):
    f,fid,source,_=completed_flow(env,tmp_path,monkeypatch);s=CreationService(f)
    newer=f.revise(fid,'alice',FlowRevision(idempotency_key='new-revision',expected_revision=1,kind='revise',instruction='调整结尾'))
    f.update(newer['id'],'failed','TEST_FAILURE')
    before=f.repo.get(source['id'],'alice');w=s.works('alice')['items']
    assert len(w)==1 and w[0]['latest_flow_id']==newer['id'] and w[0]['playable_flow_id']==fid
    assert w[0]['status']=='failed' and w[0]['play']['query']['flowId']==fid
    assert s.work('alice',source['work_id'])==w[0]
    assert f.repo.get(source['id'],'alice')==before
    assert s.works('alice',search='不匹配')['items']==[]
    assert len(s.works('alice',status='attention')['items'])==1
    with pytest.raises(ProductionError,match='JOB_NOT_FOUND'):s.work('bob',source['work_id'])

def test_legacy_import_deduplicates_and_remains_owner_scoped(env,monkeypatch):
    s,b=setup(env,monkeypatch)
    s.legacy=lambda owner:[dict(id='legacy-1',title='旧草稿',status='draft')] if owner=='alice' else []
    assert len(s.works('alice')['items'])==1
    b.legacy_story_id='legacy-1';d=s.create('alice',b)
    assert s.works('alice')['items'][0]['kind']=='draft' and len(s.works('alice')['items'])==1
    b.idempotency_key='another-draft-key';assert s.create('alice',b)['id']==d['id']
    with pytest.raises(ProductionError,match='SOURCE_ARTIFACT_NOT_OWNED'):s.create('bob',b)

def test_public_routes_and_draft_replay_when_services_offline(env,monkeypatch):
    s,b=setup(env,monkeypatch);app=FastAPI();ready={'h5_flow':{'configured':True}}
    register_production_routes(app,s.repo,s.store,lambda r:r.headers.get('x-owner','alice'),lambda:ready)
    c=TestClient(app);response=c.post('/api/v1/creation-drafts',json=b.model_dump());assert response.status_code==201
    d=response.json()['data'];request=submission(d).model_dump()
    flow=c.post('/api/v1/production-flows',json=request);assert flow.status_code==202
    ready['h5_flow']['configured']=False;request['idempotency_key']='another-submit-key'
    assert c.post('/api/v1/production-flows',json=request).json()['data']['id']==flow.json()['data']['id']
    assert c.get('/api/v1/creation-drafts/'+d['id'],headers={'x-owner':'bob'}).status_code==404
    assert len(c.get('/api/v1/works').json()['data']['items'])==1

def test_cancel_before_child_can_restart_as_idempotent_new_version(env,monkeypatch):
    s,b=setup(env,monkeypatch);d=s.create('alice',b);f=s.submit('alice',submission(d))
    stopped=s.flows.control(f['id'],'alice','cancel',f['revision'])
    body=FlowRevision(kind='restart',expected_revision=stopped['revision'],idempotency_key='restart-cancelled')
    restarted=s.flows.revise(f['id'],'alice',body)
    assert restarted['id']!=f['id'] and restarted['parent_id']==f['id'] and restarted['work_id']==f['work_id']
    assert s.flows.revise(f['id'],'alice',body)['id']==restarted['id']
    assert s.flows.get(f['id'],'alice')['status']=='cancelled'
    assert s.repo.list_jobs('alice')['items']==[]

def test_cancel_unknown_call_can_restart_without_changing_ledger(env,monkeypatch):
    from my_legend_production.production.contracts import StageResult
    s,b=setup(env,monkeypatch);d=s.create('alice',b);f=s.submit('alice',submission(d))
    s.flows.advance(f['id'],'alice');stage=s.repo.claim('cancel-unknown',stage_names=['story'])
    s.repo.reserve_call(stage,'story:unknown','d'*64,1);s.repo.unknown_call(stage,'story:unknown')
    s.repo.finish(stage,StageResult(status='needs_input',error_code='EXTERNAL_RESULT_UNKNOWN'))
    stopped=s.flows.control(f['id'],'alice','cancel',f['revision']);before=s.repo.get(stage['job_id'],'alice')
    child=s.flows.revise(f['id'],'alice',FlowRevision(kind='restart',expected_revision=stopped['revision'],idempotency_key='restart-unknown'))
    assert child['parent_id']==f['id'] and child['work_id']==f['work_id']
    assert s.repo.get(stage['job_id'],'alice')==before and len(s.flows.listing('alice')['items'])==1
