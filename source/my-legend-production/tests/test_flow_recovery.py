"""Recovery queries do not enqueue generation; remakes preserve original records."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_production_queue import env
from test_production_flows import service, body, completed_flow
from my_legend_production.production.contracts import StageResult
from my_legend_production.production.flows import FlowRevision
from my_legend_production.production.reconciliation import ProductionReconciler
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.recovery import flow_recovery


def unknown_story(env, monkeypatch):
    s=service(env,monkeypatch);f=s.create('alice',body());s.advance(f['id'],'alice')
    stage=s.repo.claim('recovery-test',stage_names=['story'])
    s.repo.reserve_call(stage,'story:writing-budget','test-input',12)
    s.repo.unknown_call(stage,'story:writing-budget')
    s.repo.finish(stage,StageResult(status='needs_input',error_code='EXTERNAL_RESULT_UNKNOWN',
                                  checkpoint={'phase':'writing','upstream_id':'original-job'}))
    s.advance(f['id'],'alice')
    return s,s.get(f['id'],'alice')


@pytest.mark.parametrize('upstream,usage,expected',[
    ('RUNNING',{},'unknown'),
    ('FAILED',{'calls':2,'unknown_usage_calls':1},'unknown'),
    ('FAILED',{'calls':2},'remake'),
    ('SUCCEEDED',{'calls':2},'resumable'),
])
def test_query_never_submits_or_resumes(env,monkeypatch,upstream,usage,expected):
    s,f=unknown_story(env,monkeypatch)
    requests=[]
    class Story:
        def call(self,method,path):
            requests.append((method,path))
            assert method=='GET' and path=='/v1/jobs/original-job'
            return dict(status=upstream,usage=usage)
    s.reconciler=ProductionReconciler(s.repo,Story())
    report=s.reconcile(f['id'],'alice',f['revision'])
    assert requests==[('GET','/v1/jobs/original-job')]
    assert report['recovery']['state']==expected
    assert report['status']=='needs_input' and report['sound_job_id']==f['sound_job_id']
    assert len(s.repo.list_jobs('alice')['items'])==1
    assert not s.repo.claim('must-not-start',stage_names=['story'])
    if expected=='resumable':
        again=s.reconcile(f['id'],'alice',report['revision'])
        assert again['recovery']['state']=='resumable'
        resumed=s.control(f['id'],'alice','resume',again['revision'])
        assert resumed['status']=='queued'
    else:
        with pytest.raises(ProductionError,match='RESUME_CONFLICT'):
            s.control(f['id'],'alice','resume',report['revision'])


def test_unavailable_query_survives_refresh_and_owner_checks(env,monkeypatch):
    s,f=unknown_story(env,monkeypatch)
    result=s.reconcile(f['id'],'alice',f['revision'])
    assert '暂时无法查询结果' in result['recovery']['message']
    assert s.get(f['id'],'alice')['recovery']==result['recovery']
    with pytest.raises(ProductionError,match='JOB_NOT_FOUND'):s.reconcile(f['id'],'bob',1)
    with pytest.raises(ProductionError,match='REVISION_CONFLICT'):s.reconcile(f['id'],'alice',1)


def test_frontend_upstream_disconnect_is_a_saved_query_result(env,monkeypatch):
    from fastapi import HTTPException
    s,f=unknown_story(env,monkeypatch)
    class Offline:
        def call(self,method,path):raise HTTPException(503,'offline')
    s.reconciler=ProductionReconciler(s.repo,Offline())
    result=s.reconcile(f['id'],'alice',f['revision'])
    assert '暂时无法查询结果' in result['recovery']['message']
    assert result['recovery']['actions']==['reconcile','remake']
    assert s.repo.get(f['sound_job_id'],'alice')['unknown_calls']==1


def test_reconcile_public_endpoint_returns_same_flow_without_enqueuing(env,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from my_legend_production.production.api import register_production_routes
    s,f=unknown_story(env,monkeypatch)
    app=FastAPI()
    register_production_routes(app,s.repo,s.store,lambda r:r.headers.get('x-owner','alice'),lambda:{})
    client=TestClient(app)
    url='/api/v1/production-flows/'+f['id']
    before=client.get(url).json()['data']
    response=client.post(url+'/reconcile',json={'expected_revision':before['revision']})
    assert response.status_code==200
    result=response.json()['data']
    assert result['id']==f['id'] and result['status']=='needs_input'
    assert result['recovery']['last_query']['generation_submissions']==0
    assert len(s.repo.list_jobs('alice')['items'])==1
    assert client.post(url+'/reconcile',json={'expected_revision':result['revision']},headers={'x-owner':'bob'}).status_code==404
    assert client.get('/api/v1/works').json()['data']['items'][0]['recovery']==result['recovery']


def test_unknown_restart_is_idempotent_and_preserves_ledger(env,monkeypatch):
    s,f=unknown_story(env,monkeypatch);before=s.repo.get(f['sound_job_id'],'alice')
    revision=FlowRevision(idempotency_key='restart-unknown-once',expected_revision=f['revision'],kind='restart')
    with ThreadPoolExecutor(2) as pool:children=list(pool.map(lambda _:s.revise(f['id'],'alice',revision),range(2)))
    assert children[0]['id']==children[1]['id']
    assert children[0]['work_id']==f['work_id'] and children[0]['parent_id']==f['id']
    s.advance(children[0]['id'],'alice')
    child=s.get(children[0]['id'],'alice')
    assert child['sound_job_id']!=f['sound_job_id']
    assert s.repo.get(f['sound_job_id'],'alice')==before
    assert s.get(f['id'],'alice')['recovery']['state']=='unknown'


def test_stage_remake_reuses_successful_sound(env,tmp_path,monkeypatch):
    s,fid,source,_=completed_flow(env,tmp_path,monkeypatch)
    with s.repo.transaction() as db:
        db.execute("UPDATE production_flows SET status='failed',error_code='STAGE_FAILED' WHERE id=%s",(fid,))
    f=s.get(fid,'alice');assert f['recovery']['remake_kind']=='stage_basic'
    child=s.revise(fid,'alice',FlowRevision(idempotency_key='recover-stage',expected_revision=f['revision'],kind='stage_basic'))
    assert s.row(child['id'],'alice')['request']['reuse_sound']==source['id']
    assert child['work_id']==f['work_id']


def test_active_stage_names_and_long_running_never_imply_failure():
    row=dict(status='running',request={'output':'h5'})
    assert flow_recovery(row,{},[])['message']=='正在写故事'
    sound=dict(stages=[{'name':'story','status':'succeeded'}],status='running')
    assert flow_recovery(row,{'sound':sound},[])['message']=='正在配音'
    sound['status']='succeeded'
    assert flow_recovery(row,{'sound':sound},[])['message']=='正在准备舞台'


def test_old_successful_plan_query_cannot_resume_a_failed_writer():
    calls=[dict(call_key='story:writing-budget',state='completed',stage_name='story',stage_status='failed',checkpoint={'phase':'writing'})]
    prior=dict(terminal=[{'call_key':'story:planning-budget','status':'SUCCEEDED'}])
    result=flow_recovery(dict(status='failed',request={}),{},calls,prior)
    assert result['state']=='remake' and result['actions']==['remake']
