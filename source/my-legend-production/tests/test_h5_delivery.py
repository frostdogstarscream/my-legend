from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_h5_preview import setup
from test_commander_preview import commander
from test_production_queue import env
from test_production_contracts import production_request
from my_legend_production.production import performance_delivery as delivery
from my_legend_production.production.api import register_production_routes
from my_legend_production.production.contracts import H5DeliveryRequest, StageResult, stage_graph
from my_legend_production.production.h5_preview import build_h5_preview
from my_legend_production.production.commander_preview import build_commander_preview
from my_legend_production.production.spine_preview import save_preview
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.playback import playback_view
from my_legend_production.production.worker import ProductionWorker


def seed(env, tmp_path, monkeypatch, version='3.1'):
    repo, _ = env
    store, job, catalog, body = (commander if version=='3.1' else setup)(tmp_path)
    root = tmp_path
    monkeypatch.setenv('MY_LEGEND_SPINE_ROOT', str(root))
    # Tiny contract fixtures contain declared hashes, not production image bytes.
    monkeypatch.setattr(delivery, 'verify_resources', lambda value, **kw: {'character_catalog': value['catalog_sha256']})
    jobid = repo.create('alice', production_request(outputs=['script']))
    stage = repo.claim('seed')
    refs = job['artifacts'] + [store.put_json('RenderManifest', 'v1', {'fixture_only': True}).model_dump()]
    repo.finish(stage, StageResult(status='succeeded', source_version_id='v1', technical_quality='passed', artifacts=refs))
    job = repo.get(jobid, 'alice')
    value = (build_commander_preview if version=='3.1' else build_h5_preview)(job,body,store,catalog,root)
    saved = save_preview(root,value)
    return repo,store,job,saved


def worker(repo,store):
    return ProductionWorker(repo,store,{'performance_import':delivery.PerformanceImportStage(),
        'performance_package':delivery.PerformancePackageStage()},only_configured_stages=True)


@pytest.mark.parametrize('version',['3.0','3.1'])
def test_import_snapshot_roundtrip_no_generation(env,tmp_path,monkeypatch,version):
    repo,store,source,saved=seed(env,tmp_path,monkeypatch,version)
    body=H5DeliveryRequest(preview_sha256=saved['preview_sha256'],idempotency_key='delivery-one')
    request=delivery.prepare_request(repo,store,'alice',source['id'],body)
    assert set(stage_graph(request))==delivery.STAGES
    jid=repo.create('alice',request)
    w=worker(repo,store)
    assert w.tick() and w.tick() and not w.tick()
    job=repo.get(jid,'alice');assert job['status']=='succeeded' and job['actual_calls']==0
    result=delivery.delivered_preview(job,store)
    assert result['preview']==saved
    assert result['manifest']['human_review']=='pending'
    assert 'model_review' not in result['manifest'] and 'casting_confirmed' not in result['manifest']
    assert result['manifest']['status']==('needs_attention' if version=='3.0' else 'preview_ready')
    view=playback_view(job,store)
    assert view['available_outputs']==['script','audio','h5'] and 'video' not in view['manifest']
    assert view['manifest']['audio']['artifact']==saved['audio']['artifact']
    assert repo.get(source['id'],'alice')==source
    assert repo.create('alice',request)==jid
    with pytest.raises(ProductionError,match='JOB_NOT_FOUND'):repo.get(jid,'bob')
    # Delivery playback reads its own fixed artifact, not the original sidecar file.
    (tmp_path/'previews'/source['id']/(saved['preview_sha256']+'.json')).unlink()
    assert delivery.delivered_preview(job,store)==result


def test_api_idempotency_parent_branch_and_dirty_source(env,tmp_path,monkeypatch):
    repo,store,source,saved=seed(env,tmp_path,monkeypatch)
    app=FastAPI()
    register_production_routes(app,repo,store,lambda r:r.headers.get('x-owner','alice'),
        lambda:{'modules':{n:{'configured':True} for n in delivery.STAGES}})
    client=TestClient(app)
    url=f"/api/v1/production-jobs/{source['id']}/h5-deliveries"
    body={'preview_sha256':saved['preview_sha256'],'idempotency_key':'api-delivery'}
    response=client.post(url,json=body);assert response.status_code==202,response.text
    jid=response.json()['data']['id']
    assert client.post(url,json=body).json()['data']['id']==jid
    assert client.post(url,json=body,headers={'x-owner':'bob'}).status_code==404
    job=repo.get(jid,'alice')
    assert client.post('/api/v1/production-jobs',json=job['request']).status_code==409
    w=worker(repo,store);assert w.tick() and w.tick()
    assert client.get(f'/api/v1/production-jobs/{jid}/h5-performance').status_code==200
    def branch(i):
        req=delivery.prepare_request(repo,store,'alice',source['id'],H5DeliveryRequest(**(body|{'idempotency_key':f'branch-{i:03}','parent_job_id':jid})))
        return repo.create('alice',req)
    with ThreadPoolExecutor(2) as pool:ids=list(pool.map(branch,[1,2]))
    assert len(set(ids))==2
    assert all(repo.get(i,'alice')['request']['source']['parent_job_id']==jid for i in ids)
    assert client.post(url,json=body|{'parent_job_id':jid}).status_code==409
    bad=client.post(url,json=body|{'idempotency_key':'invalid-hash','preview_sha256':'0'*64})
    assert bad.status_code==409


def test_worker_does_not_claim_or_expire_other_jobs_and_cancel(env,tmp_path,monkeypatch):
    repo,store,source,saved=seed(env,tmp_path,monkeypatch)
    other=repo.create('alice',production_request(idempotency_key='unrelated-job'))
    with repo.transaction() as db:db.execute("UPDATE jobs SET deadline=now()-interval '1 second' WHERE id=%s",(other,))
    req=delivery.prepare_request(repo,store,'alice',source['id'],H5DeliveryRequest(preview_sha256=saved['preview_sha256'],idempotency_key='cancel-delivery'))
    jid=repo.create('alice',req);repo.cancel(jid,'alice')
    assert not worker(repo,store).tick()
    assert repo.get(other,'alice')['status']=='queued'
    assert repo.get(jid,'alice')['status']=='cancelled'


def test_corrupt_audio_fails_and_source_remains_unchanged(env,tmp_path,monkeypatch):
    repo,store,source,saved=seed(env,tmp_path,monkeypatch)
    req=delivery.prepare_request(repo,store,'alice',source['id'],H5DeliveryRequest(preview_sha256=saved['preview_sha256'],idempotency_key='corrupt-delivery'))
    jid=repo.create('alice',req)
    audio=store.root/saved['audio']['artifact']['sha256'];audio.write_bytes(b'corrupt')
    assert worker(repo,store).tick()
    assert repo.get(jid,'alice')['status']=='failed'
    assert repo.get(source['id'],'alice')==source


def test_missing_resources_are_rejected(tmp_path,monkeypatch):
    store,job,catalog,body=setup(tmp_path)
    value=build_h5_preview(job,body,store,catalog,tmp_path)
    with pytest.raises(ProductionError,match='SPINE_ASSET_HASH_MISMATCH'):
        delivery.verify_resources(value,root=tmp_path)


def test_lease_recovery_keeps_snapshot_and_no_calls(env,tmp_path,monkeypatch):
    repo,store,source,saved=seed(env,tmp_path,monkeypatch)
    req=delivery.prepare_request(repo,store,'alice',source['id'],H5DeliveryRequest(preview_sha256=saved['preview_sha256'],idempotency_key='recovery-delivery'))
    jid=repo.create('alice',req)
    old=repo.claim('dead-worker',stage_names=sorted(delivery.STAGES))
    from my_legend_production.production.worker import StageContext
    result=delivery.PerformanceImportStage()(StageContext(repo,store,old))
    with repo.transaction() as db:db.execute("UPDATE stages SET lease_until=now()-interval '1 second' WHERE job_id=%s",(jid,))
    with pytest.raises(ProductionError,match='LEASE_LOST'):repo.finish(old,result)
    w=worker(repo,store);assert w.tick() and w.tick()
    job=repo.get(jid,'alice')
    assert job['actual_calls']==0 and job['status']=='succeeded'
    assert delivery.delivered_preview(job,store)['preview']==saved
