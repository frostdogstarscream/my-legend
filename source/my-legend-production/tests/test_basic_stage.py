from copy import deepcopy
import pytest
from test_commander_preview import commander
from test_production_queue import env
from test_production_flows import completed_flow
from test_h5_delivery import worker
from my_legend_production.production.basic_stage import build_basic_preview
from my_legend_production.production.spine_preview import save_preview
from my_legend_production.production.commander_preview import read_commander_preview
from my_legend_production.production.flows import FlowRevision
from my_legend_production.production.repository import ProductionError


def test_model_scene_reaches_compiled_background_with_original_audio(tmp_path):
    import json
    from my_legend_production.production.life_contracts import canonical_hash
    from my_legend_production.production.contracts import ArtifactRef
    store,job,catalog,body=commander(tmp_path,'甲与乙交谈。')
    c=casting(catalog,body)
    rooms=json.loads((tmp_path/'scene-catalogs'/(c['scene_catalog_sha256']+'.json')).read_text())
    cafe=deepcopy(rooms['scenes'][0]);cafe.update(scene_id='cafe',display_name='咖啡吧')
    rooms['scenes'].append(cafe);rooms.pop('catalog_sha256');rooms['catalog_sha256']=canonical_hash(rooms)
    (tmp_path/'scene-catalogs'/(rooms['catalog_sha256']+'.json')).write_text(json.dumps(rooms))
    c.update(scene_catalog_sha256=rooms['catalog_sha256'],scene_choices=[dict(id='cafe',name='咖啡吧')])
    for i,raw in enumerate(job['artifacts']):
        ref=ArtifactRef.model_validate(raw)
        if ref.kind=='StoryDelivery':
            delivery=store.json(ref);delivery['episodes'][0]['scenes'][0]['location']='咖啡吧'
            job['artifacts'][i]=store.put_json('StoryDelivery','v1',delivery).model_dump()
    value=build_basic_preview(job,store,c,tmp_path)
    assert value['scene_bindings'][0]['scene']['scene_id']=='cafe'
    assert '故事模型选择' in value['scene_bindings'][0]['note']
    assert value['audio']['artifact']['sha256'] in {a['sha256'] for a in job['artifacts']}

def casting(catalog,body):
    return dict(catalog_sha256=catalog['catalog_sha256'],scene_catalog_sha256=body.scene_bindings[0]['scene']['catalog_sha256'],
        bindings=body.bindings,initial_outfits={'a':'male1','b':'female2'})

@pytest.mark.parametrize('text',['甲在沙发坐下。','甲拉着乙走到书架前，然后闭眼。','甲翻书。'])
def test_basic_stage_preserves_audio_and_cast_without_complex_cues(tmp_path,text):
    store,job,catalog,body=commander(tmp_path,text)
    c=casting(catalog,body);value=build_basic_preview(job,store,c,tmp_path)
    assert not value['stage']['cues'] and not value['furniture_track']['cues'] and not value['expression_track']
    assert all(v['status'] in ('omitted','not_executed') for v in value['coverage'])
    assert {a['role_id']:a['initial_outfit_id'] for a in value['stage']['actors']}==c['initial_outfits']
    assert value['review_confirmed'] is False and value['dialogue_enabled'] is True
    saved=save_preview(tmp_path,value)
    assert read_commander_preview(tmp_path,job,saved['preview_sha256'],store)==saved
    assert build_basic_preview(job,store,c,tmp_path)==value

def test_basic_recovery_is_new_version_and_never_recreates_sound(env,tmp_path,monkeypatch):
    s,fid,source,saved=completed_flow(env,tmp_path,monkeypatch)
    row=s.row(fid,'alice');c={**row['casting'],'scene_catalog_sha256':saved['scene_bindings'][0]['scene']['catalog_sha256']}
    from psycopg.types.json import Jsonb
    with s.repo.transaction() as db:db.execute('UPDATE production_flows SET casting=%s WHERE id=%s',(Jsonb(c),fid))
    s.update(fid,'failed','DIRECTING_PLAN_INVALID')
    b=FlowRevision(kind='stage_basic',expected_revision=1,idempotency_key='basic-recovery-one')
    child=s.revise(fid,'alice',b);assert s.revise(fid,'alice',b)['id']==child['id']
    s.advance(child['id'],'alice');w=worker(s.repo,s.store);assert w.tick() and w.tick();s.advance(child['id'],'alice')
    f=s.get(child['id'],'alice');assert f['status']=='succeeded' and 'h5' in f['available_outputs']
    assert f['stage_mode']=='basic_stage/1' and f['parent_id']==fid and f['sound_job_id']==source['id']
    assert f['jobs']['stage']['actual_calls']==0 and s.repo.get(source['id'],'alice')==source
    assert s.get(fid,'alice')['status']=='failed'
    with pytest.raises(ProductionError,match='JOB_NOT_FOUND'):s.revise(fid,'bob',b)

def test_new_creation_automatically_delivers_basic_stage_after_audio(env,tmp_path,monkeypatch):
    from my_legend_production.production.flows import FlowRequest,FlowCasting
    from my_legend_production.production import flows
    s,old,source,saved=completed_flow(env,tmp_path,monkeypatch)
    c={**s.row(old,'alice')['casting'],'scene_catalog_sha256':saved['scene_bindings'][0]['scene']['catalog_sha256']}
    monkeypatch.setattr(flows,'freeze_casting',lambda _:deepcopy(c))
    body=FlowRequest(idempotency_key='new-basic-create',work_id=source['work_id'],
        source=dict(kind='creative',title='基础舞台新创作',idea='两位角色在书房里谈论一本书，约定下次继续。',genre='生活治愈',character_ids=list(c['bindings']),target_duration_seconds=90),
        casting=FlowCasting(**{k:c[k] for k in ('catalog_sha256','bindings','initial_outfits','casting_confirmed')}))
    f=s.create('alice',body);assert s.row(f['id'],'alice')['request']['stage_mode']=='basic_stage/1'
    # Sound generation is covered separately; supply an immutable completed source here.
    s.link(f['id'],'sound',source['id']);s.advance(f['id'],'alice')
    w=worker(s.repo,s.store);assert w.tick() and w.tick();s.advance(f['id'],'alice')
    final=s.get(f['id'],'alice');assert final['status']=='succeeded' and 'h5' in final['available_outputs']
    assert {x['name'] for x in final['jobs']['stage']['stages']}=={'performance_import','performance_package'}
    assert final['jobs']['stage']['actual_calls']==0
