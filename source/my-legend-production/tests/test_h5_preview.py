from copy import deepcopy
from hashlib import sha256
import json
import pytest
from test_life_preview import fixture
from my_legend_production.production.life_contracts import canonical_hash, LifeValidationError
from my_legend_production.production.h5_preview import build_h5_preview, prepare_context, read_h5_preview, measured_scenes
from my_legend_production.production.spine_preview import StagePreviewRequest, save_preview


def setup(tmp_path,text='甲停下，走到书架前并翻找。'):
    store,job,catalog=fixture(tmp_path,text,kind='action')
    catalog['profile']='life_v2';catalog['catalog_sha256']=canonical_hash({k:v for k,v in catalog.items() if k!='catalog_sha256'})
    for folder in ('catalogs','scene-catalogs'):(tmp_path/folder).mkdir(exist_ok=True)
    (tmp_path/'catalogs'/(catalog['catalog_sha256']+'.json')).write_text(json.dumps(catalog))
    ref={'sha256':'b'*64,'size_bytes':1,'media_type':'image/png'}
    room=dict(scene_id='library',display_name='图书馆',width=960,height=540,actor_scale=.64,layout_kind='authored_performance_sample',seats=[],
        objects=[dict(id='bookcase',image=ref,layer='depth',position=[150,400],anchor=[.5,1],width=120,height=200,depth_y=400,repeat=False)],
        blocking=dict(schema_version='legend_scene_blocking/1',purpose='authored_standing_layout',interaction_status='standing_targets_only',walk_lane=[.06,.8,.94,.86],foot_y=.825,
            dialogue_slots=[[.333,.825],[.667,.825]],ensemble_slots=[[.25,.825],[.5,.825],[.75,.825]],
            entrances=[dict(id='left',label='左侧',position=[.06,.825]),dict(id='right',label='右侧',position=[.94,.825])],
            targets=[dict(id='bookcase_front',object_id='bookcase',label='书架前',position=[.2,.825],action='approach')]))
    rooms=dict(schema_version='legend_scene_catalog/1',scenes=[room],files={'b'*64:ref});rooms['catalog_sha256']=canonical_hash(rooms)
    (tmp_path/'scene-catalogs'/(rooms['catalog_sha256']+'.json')).write_text(json.dumps(rooms))
    binding=dict(episode_id='ep',scene_id='scene',scene=dict(catalog_sha256=rooms['catalog_sha256'],scene_id='library'),confirmed=True,
        actors=[dict(instance_id='actor_1',position=[.333,.825],facing=1,visible=True),dict(instance_id='actor_2',position=[.667,.825],facing=-1,visible=True)])
    body=StagePreviewRequest(profile='life_v2',bindings={'a':'commander','b':'commander'},scene_bindings=[binding],dialogue_enabled=True)
    return store,job,catalog,body


def compile(tmp_path,text='甲停下，走到书架前并翻找。'):
    store,job,catalog,body=setup(tmp_path,text)
    return store,job,catalog,body,build_h5_preview(job,body,store,catalog,tmp_path)


def saved_body(value,**changes):
    return StagePreviewRequest(profile='life_v2',operation='validate_and_save',script=deepcopy(value['stage']),scene_bindings=value['scene_bindings'],
        dialogue_enabled=True,coverage_decisions=changes.pop('coverage_decisions',value['coverage_decisions']),**changes)


def test_object_action_and_compound_coverage(tmp_path):
    store,job,catalog,body,value=compile(tmp_path)
    assert [c['type'] for c in value['stage']['cues']]==['body.play','actor.move_to']
    assert value['stage']['cues'][1]['params']['to']==[.2,.825]
    assert [c['status'] for c in value['coverage']]==['compiled','compiled','unsupported']
    assert value['status']=='needs_attention'
    assert build_h5_preview(job,body,store,catalog,tmp_path)==value
    saved=save_preview(tmp_path,value)
    assert read_h5_preview(tmp_path,job,saved['preview_sha256'],store)==saved


def test_explicit_omission_is_kept_after_confirmation(tmp_path):
    store,job,catalog,body,value=compile(tmp_path)
    cid=value['coverage'][-1]['id'];decision=dict(coverage_id=cid,resolution='omitted',note='本版不演出翻找，保留原配音和字幕。',cue_ids=[])
    reviewed=build_h5_preview(job,saved_body(value,coverage_decisions=[decision],review_confirmed=True),store,catalog,tmp_path)
    assert reviewed['status']=='confirmed_with_omissions'
    assert reviewed['coverage'][-1]['status']=='omitted'
    assert reviewed['audio']==value['audio'] and reviewed['script']==value['script']
    assert read_h5_preview(tmp_path,job,save_preview(tmp_path,reviewed)['preview_sha256'],store)['status']==reviewed['status']


@pytest.mark.parametrize('case',['unconfirmed','missing','duplicate','invalid_actor','overlap','outside','revision','cue_range','false_confirmation','fake_coverage','unsupported_manual'])
def test_reject_invalid_bindings_and_reviews(tmp_path,case):
    store,job,catalog,body,value=compile(tmp_path)
    request=saved_body(value)
    if case=='unconfirmed':request.scene_bindings[0]['confirmed']=False
    if case=='missing':request.scene_bindings=[]
    if case=='duplicate':request.scene_bindings*=2
    if case=='invalid_actor':request.scene_bindings[0]['actors'][0]['instance_id']='unknown'
    if case=='overlap':request.scene_bindings[0]['actors'][1]['position']=[.333,.825]
    if case=='outside':request.scene_bindings[0]['actors'][0]['position']=[.3,.3]
    if case=='revision':request.script['actors'][0]['actor_revision']='invalid'
    if case=='cue_range':request.script['cues'][0]['end_frame']=1000000
    if case=='false_confirmation':request.review_confirmed=True
    if case=='fake_coverage':request.coverage_decisions=[dict(coverage_id='bad',resolution='omitted',note='skip')]
    if case=='unsupported_manual':request.coverage_decisions=[dict(coverage_id=value['coverage'][-1]['id'],resolution='manual',note='pretend',cue_ids=[value['stage']['cues'][0]['id']])]
    with pytest.raises(LifeValidationError):build_h5_preview(job,request,store,catalog,tmp_path)


@pytest.mark.parametrize('text,status',[('甲走到不存在的桌子前。','needs_correction'),('两人走到书架前。','needs_correction'),('甲不要走到书架前。','not_executed'),('甲翻书。','unsupported'),('甲轻抚衣角。','unsupported')])
def test_uncovered_actions_are_not_silent(tmp_path,text,status):
    *_,value=compile(tmp_path,text)
    assert not value['stage']['cues'];assert value['coverage'][0]['status']==status


def test_blocked_path_and_manual_deletion(tmp_path):
    store,job,catalog,body=setup(tmp_path,'甲向右走。')
    body.scene_bindings[0]['actors'][1]['position']=[.48,.825]
    value=build_h5_preview(job,body,store,catalog,tmp_path)
    assert not value['stage']['cues'];assert '占用' in value['coverage'][0]['message']
    body.scene_bindings[0]['actors'][1]['position']=[.8,.825]
    value=build_h5_preview(job,body,store,catalog,tmp_path);value['stage']['cues']=[]
    value=build_h5_preview(job,saved_body(value),store,catalog,tmp_path)
    assert value['status']=='needs_attention'


def test_scene_boundaries_use_starts_not_background_tails():
    delivery={'episodes':[{'episode_id':'ep','scenes':[{'scene_id':'a','utterances':[]},{'scene_id':'b','utterances':[]}]}]}
    timeline={'total_frames':100,'events':[
        {'episode_id':'ep','scene_id':'a','line_id':'1','kind':'sfx','start_frame':0,'end_frame':100,'render_role':'background'},
        {'episode_id':'ep','scene_id':'b','line_id':'2','kind':'dialogue','start_frame':50,'end_frame':90,'render_role':'speech'}]}
    scenes,issues=measured_scenes(delivery,timeline);assert not issues;assert scenes[0]['end_frame']==50
    timeline['events'][0]['kind']='dialogue';timeline['events'][0]['render_role']='speech'
    assert measured_scenes(delivery,timeline)[1][0]['code']=='SCENE_SPEECH_OVERLAP'


def test_prepare_does_not_save_preview(tmp_path):
    store,job,catalog,body=setup(tmp_path)
    value=prepare_context(job,store,catalog)
    assert value['scenes'][0]['start_frame']==0 and len(value['roles'])==2
    assert not (tmp_path/'previews').exists()


def test_target_identity_rejects_stale_coordinates(tmp_path):
    store,job,catalog,body,value=compile(tmp_path)
    request=saved_body(value)
    move=next(c for c in request.script['cues'] if c['type']=='actor.move_to')
    assert move['params']['target']==dict(kind='object',id='bookcase_front')
    move['params']['to'][0]=.25
    with pytest.raises(LifeValidationError,match='LIFE_VALIDATION_FAILED'):
        build_h5_preview(job,request,store,catalog,tmp_path)


def test_short_crossing_between_sample_points_is_blocked():
    from my_legend_production.production.h5_preview import movement_issue
    segment=dict(start_frame=0,end_frame=1000,actors=[dict(instance_id='a',position=[.2,.825],visible=True),dict(instance_id='b',position=[.8,.825],visible=True)])
    script=dict(actors=[dict(instance_id='a'),dict(instance_id='b')],cues=[dict(id='cross',type='actor.move_to',instance_id='b',start_frame=10,end_frame=20,enabled=True,params=dict(to=[.06,.825],visible_after=True))])
    room=dict(blocking=dict(walk_lane=[.06,.8,.94,.86],foot_y=.825))
    assert movement_issue(script,segment,'a',0,100,[.6,.825],room)


def test_h5_api_owner_prepare_save_and_reopen(tmp_path,monkeypatch):
    from fastapi import FastAPI,HTTPException
    from fastapi.testclient import TestClient
    from my_legend_production.production.api import register_production_routes
    from my_legend_production.production.repository import ProductionError
    store,job,catalog,body=setup(tmp_path)
    (tmp_path/'life-v2-current.json').write_text(json.dumps(dict(catalog_sha256=catalog['catalog_sha256'])))
    class Repository:
        def get(self,jid,uid):
            if jid!=job['id'] or uid!='alice':raise ProductionError('JOB_NOT_FOUND')
            return deepcopy(job)
    def owner(request):
        if not request.headers.get('authorization'):raise HTTPException(401,'UNAUTHORIZED')
        return request.headers['authorization']
    monkeypatch.setenv('MY_LEGEND_SPINE_ROOT',str(tmp_path))
    app=FastAPI();register_production_routes(app,Repository(),store,owner,lambda:{})
    client=TestClient(app);headers={'authorization':'alice'}
    endpoint=f"/api/v1/production-jobs/{job['id']}/stage-preview"
    assert client.post(endpoint,json=body.model_dump()).status_code==401
    assert client.post(endpoint,json=body.model_dump(),headers={'authorization':'bob'}).status_code==404
    prepared=client.post(endpoint,json=dict(profile='life_v2',operation='prepare'),headers=headers)
    assert prepared.status_code==200,prepared.text
    assert not (tmp_path/'previews').exists()
    saved=client.post(endpoint,json=body.model_dump(),headers=headers)
    assert saved.status_code==200,saved.text
    value=saved.json()['data']
    assert client.get(endpoint+'/'+value['preview_sha256'],headers=headers).json()['data']==value
    assert client.get(endpoint+'/'+value['preview_sha256'],headers={'authorization':'bob'}).status_code==404
    assert job['status']=='succeeded'


def test_enter_exit_and_person_target(tmp_path):
    store,job,catalog,body=setup(tmp_path,'甲入场，甲退场。')
    body.scene_bindings[0]['actors'][0]['visible']=False
    value=build_h5_preview(job,body,store,catalog,tmp_path)
    assert [c['status'] for c in value['coverage']]==['compiled','compiled']
    assert [c['type'] for c in value['stage']['cues']]==['actor.place','actor.move_to','actor.move_to']
    assert value['stage']['cues'][-1]['params']['visible_after'] is False
    other_path=tmp_path/'person';other_path.mkdir()
    *_,value=compile(other_path,'甲走向乙。')
    assert value['coverage'][0]['target']==dict(kind='actor',id='actor_2')
    assert value['stage']['cues'][0]['params']['to'][0]==pytest.approx(.487)


def test_manual_position_collision_and_unbound_role(tmp_path):
    store,job,catalog,body=setup(tmp_path,'甲停下。')
    body.bindings={'a':'commander'};body.scene_bindings[0]['actors']=body.scene_bindings[0]['actors'][:1]
    value=build_h5_preview(job,body,store,catalog,tmp_path)
    reviewed=build_h5_preview(job,saved_body(value,review_confirmed=True),store,catalog,tmp_path)
    assert reviewed['status']=='confirmed_with_omissions'
    assert any(d['code']=='UNBOUND_ROLE' for d in reviewed['diagnostics'])
    second=tmp_path/'collision';second.mkdir();store,job,catalog,body,value=compile(second,'甲停下。')
    request=saved_body(value);request.script['cues'].append(dict(id='overlap',type='actor.place',instance_id='actor_1',start_frame=1,end_frame=1,enabled=True,params=dict(position=[.667,.825],visible=True,facing=1),source=None,source_text='',rule_id='manual',reason='test'))
    with pytest.raises(LifeValidationError):build_h5_preview(job,request,store,catalog,second)
