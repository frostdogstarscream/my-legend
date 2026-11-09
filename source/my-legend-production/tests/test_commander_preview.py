from copy import deepcopy
import json
import pytest
from test_h5_preview import setup
from my_legend_production.production.life_contracts import canonical_hash, LifeValidationError
from my_legend_production.production.commander_preview import build_commander_preview, read_commander_preview
from my_legend_production.production.spine_preview import StagePreviewRequest, save_preview
from my_legend_production.production.contracts import ArtifactRef


def commander(tmp_path,text='甲在沙发坐下。'):
    store,job,catalog,body=setup(tmp_path,text)
    base=deepcopy(catalog['actors'][1]);catalog['actors']=[];catalog['outfits']=[]
    cal={p:dict(x=0,y=0,scale=1,rotation=0,flip=False,layer=0,actions={}) for p in ('head','body','foot')}
    for gender in ('male','female'):
        aid='commander_'+gender;actor=deepcopy(base);actor.update(actor_id=aid,revision=gender,default_outfit_id=gender+'1',normalization_outfit_id=gender+'1',outfit_ids=[gender+'1',gender+'2'])
        face=dict(schema_version='commander_face/1',head_asset_id='s',head_sha256='a'*64,eyes=dict(open={'eyes':'open'},closed={'eyes':'closed'},smile={'eyes':'smile'},squint={'eyes':'squint'}),mouth=dict(slot='mouth',open='open',closed='closed',actions=['idle','walk','sit'],period_ms=280,open_start_ms=75,open_end_ms=215,lead_ms=70,tail_ms=75));face['profile_sha256']=canonical_hash(face);actor['face_profile']=face;catalog['actors'].append(actor)
        for oid in actor['outfit_ids']:catalog['outfits'].append(dict(outfit_id=oid,actor_id=aid,parts={p:'s' for p in cal},calibration=cal,calibration_revision=canonical_hash(cal),qa='pass',allowed_actions=['idle','walk','sit'],calibration_group=gender,face_presets=['native','open','closed','smile','squint']))
    catalog.update(actor_model='fixed_commander/1');catalog.pop('catalog_sha256');catalog['catalog_sha256']=canonical_hash(catalog)
    (tmp_path/'catalogs'/(catalog['catalog_sha256']+'.json')).write_text(json.dumps(catalog))
    old=body.scene_bindings[0]['scene']['catalog_sha256'];rooms=json.loads((tmp_path/'scene-catalogs'/(old+'.json')).read_text());room=rooms['scenes'][0]
    room['commander_seats']=[dict(seat_id='sofa',object_id='bookcase',label='沙发',position=[.2,.7],approach=[.2,.825],exit=[.32,.825],facing=1,calibration_revision='seat-rev',calibrated_groups=['male','female'])]
    rooms.update(commander_catalog_sha256=catalog['catalog_sha256'],commander_calibrations={g:dict(actor_id='commander_'+g,head_sha256='a'*64,normalization_outfit=g+'1',root_offset=[0,50]) for g in ('male','female')});rooms.pop('catalog_sha256');rooms['catalog_sha256']=canonical_hash(rooms)
    (tmp_path/'scene-catalogs'/(rooms['catalog_sha256']+'.json')).write_text(json.dumps(rooms))
    body.scene_bindings[0]['scene']['catalog_sha256']=rooms['catalog_sha256'];body.bindings={'a':'commander_male','b':'commander_female'}
    return store,job,catalog,body


def save_body(value):
    return StagePreviewRequest(profile='life_v2',operation='validate_and_save',script=deepcopy(value['stage']),scene_bindings=deepcopy(value['scene_bindings']),furniture_track=deepcopy(value['furniture_track']),expression_track=value['expression_track'],mouth_enabled=value['mouth_enabled'],dialogue_enabled=value['dialogue_enabled'])


def test_commander_roundtrip_and_separate_channels(tmp_path):
    store,job,catalog,body=commander(tmp_path)
    value=build_commander_preview(job,body,store,catalog,tmp_path)
    assert value['schema_version']=='spine_stage_preview/3.1'
    assert value['mouth_enabled']=={'actor_1':True,'actor_2':True}
    assert value['coverage'][0]['status']=='compiled'
    assert value['furniture_track']['cues'][0]['leave_frame'] is None
    body=save_body(value);body.expression_track=[dict(id='eyes',instance_id='actor_1',start_frame=40000,end_frame=45000,channel='eyes',preset='closed'),dict(id='mouth',instance_id='actor_1',start_frame=40000,end_frame=45000,channel='mouth',preset='open')]
    body.mouth_enabled['actor_2']=False
    result=build_commander_preview(job,body,store,catalog,tmp_path)
    saved=save_preview(tmp_path,result)
    assert read_commander_preview(tmp_path,job,saved['preview_sha256'],store)==saved
    assert saved['audio']==value['audio'] and saved['stage']['actors']==value['stage']['actors']


@pytest.mark.parametrize('text,status',[('甲坐下。','needs_correction'),('甲不要在沙发坐下。','not_executed'),('甲和乙在沙发坐下。','needs_correction'),('甲翻书。','unsupported')])
def test_conservative_rules(tmp_path,text,status):
    store,job,catalog,body=commander(tmp_path,text)
    value=build_commander_preview(job,body,store,catalog,tmp_path)
    assert not value['furniture_track']['cues'];assert value['coverage'][0]['status']==status


@pytest.mark.parametrize('case',['revision','outfit','cross_scene','hold_end','stand_time','seat_overlap','actor_overlap','body_overlap','change_outfit','source','path','eye_overlap','mouth_preset','unknown_mouth'])
def test_reject_invalid_commander_config(tmp_path,case):
    store,job,catalog,body=commander(tmp_path);value=build_commander_preview(job,body,store,catalog,tmp_path);body=save_body(value);c=body.furniture_track['cues'][0]
    if case=='revision':c['calibration_revision']='stale'
    if case=='outfit':c['outfit_id']='male2'
    if case=='cross_scene':c['end_frame']+=1
    if case=='hold_end':c['end_frame']-=1
    if case=='stand_time':c['leave_frame']=c['end_frame']-1
    if case in ('seat_overlap','actor_overlap'):body.furniture_track['cues'].append({**c,'id':'duplicate','instance_id':'actor_2' if case=='seat_overlap' else c['instance_id'],'outfit_id':'female1' if case=='seat_overlap' else c['outfit_id']})
    if case in ('body_overlap','change_outfit'):
        body.script['cues'].append(dict(id='conflict',instance_id=c['instance_id'],type='body.play' if case=='body_overlap' else 'actor.change_outfit',start_frame=45000,end_frame=46000 if case=='body_overlap' else 45000,enabled=True,params=dict(action='idle',mode='loop') if case=='body_overlap' else dict(outfit_id='male2'),source=None,source_text='',rule_id='manual',reason='test'))
    if case=='source':c['source']['line_id']='missing'
    if case=='path':body.scene_bindings[0]['actors'][0]['position']=[.6,.825];body.scene_bindings[0]['actors'][1]['position']=[.4,.825]
    if case=='eye_overlap':body.expression_track=[dict(id=str(i),instance_id='actor_1',start_frame=0,end_frame=10,channel='eyes',preset='closed') for i in range(2)]
    if case=='mouth_preset':body.expression_track=[dict(id='x',instance_id='actor_1',start_frame=0,end_frame=10,channel='mouth',preset='smile')]
    if case=='unknown_mouth':body.mouth_enabled['missing']=True
    with pytest.raises(LifeValidationError):build_commander_preview(job,body,store,catalog,tmp_path)


def test_manual_furniture_resolution_and_omission(tmp_path):
    store,job,catalog,body=commander(tmp_path,'甲坐下。');value=build_commander_preview(job,body,store,catalog,tmp_path);body=save_body(value)
    c=dict(id='manual-seat',instance_id='actor_1',episode_id='ep',scene_id='scene',seat_id='sofa',calibration_revision='seat-rev',outfit_id='male1',start_frame=32000,arrive_frame=43200,leave_frame=60000,end_frame=76000,source=value['coverage'][0]['source'])
    body.furniture_track['cues']=[c];body.coverage_decisions=[dict(coverage_id=value['coverage'][0]['id'],resolution='manual',cue_ids=['manual-seat'],note='人工选择座位')]
    result=build_commander_preview(job,body,store,catalog,tmp_path);assert result['coverage'][0]['status']=='manual'
    saved=save_preview(tmp_path,result);assert read_commander_preview(tmp_path,job,saved['preview_sha256'],store)==saved


def test_paired_rising_uses_later_source_time_and_survives_save(tmp_path):
    store,job,catalog,body=commander(tmp_path)
    for index,raw in enumerate(job['artifacts'][:]):
        ref=ArtifactRef.model_validate(raw)
        if ref.kind=='StoryDelivery':
            delivery=store.json(ref);delivery['episodes'][0]['scenes'][0]['utterances'].append(dict(line_id='rise',kind='action',text='甲起身。'))
            job['artifacts'][index]=store.put_json('StoryDelivery','v1',delivery).model_dump()
        elif ref.kind=='MeasuredTimeline':
            timeline=store.json(ref);timeline['events'].append(dict(sequence=4,episode_id='ep',scene_id='scene',line_id='rise',kind='action',text='甲起身。',start_frame=56000,end_frame=64000,duration_basis='planned_silent_hold',source_audio_sha256=timeline['audio_sha256']))
            job['artifacts'][index]=store.put_json('MeasuredTimeline','v1',timeline).model_dump()
    value=build_commander_preview(job,body,store,catalog,tmp_path)
    assert value['furniture_track']['cues'][0]['leave_frame']==56000
    assert [i['status'] for i in value['coverage']]==['compiled','compiled']
    saved=save_preview(tmp_path,value);assert read_commander_preview(tmp_path,job,saved['preview_sha256'],store)==saved


def test_stale_saved_resolution_is_not_trusted(tmp_path):
    store,job,catalog,body=commander(tmp_path);value=build_commander_preview(job,body,store,catalog,tmp_path);body=save_body(value)
    body.furniture_track['resolved'][value['furniture_track']['cues'][0]['id']]['root_offset']=[999,999]
    assert build_commander_preview(job,body,store,catalog,tmp_path)['furniture_track']['resolved']==value['furniture_track']['resolved']


def test_surprised_requires_matching_profile_and_roundtrips_with_mouth(tmp_path):
    store,job,catalog,body=commander(tmp_path)
    value=build_commander_preview(job,body,store,catalog,tmp_path);body=save_body(value)
    body.expression_track=[dict(id='surprise',instance_id='actor_1',start_frame=40000,end_frame=45000,channel='eyes',preset='surprised')]
    with pytest.raises(LifeValidationError):build_commander_preview(job,body,store,catalog,tmp_path)
    for actor in catalog['actors']:
        face=actor['face_profile'];face['eyes']['surprised']={'eyes':'round'};face.pop('profile_sha256');face['profile_sha256']=canonical_hash(face)
    catalog.pop('catalog_sha256');catalog['catalog_sha256']=canonical_hash(catalog)
    (tmp_path/'catalogs'/(catalog['catalog_sha256']+'.json')).write_text(json.dumps(catalog))
    body.script['catalog_sha256']=catalog['catalog_sha256']
    # No furniture is needed for this face capability check.
    body.furniture_track={'schema_version':'commander_furniture/1','cues':[]};body.coverage_decisions=[]
    body.expression_track.append(dict(id='mouth',instance_id='actor_1',start_frame=40000,end_frame=45000,channel='mouth',preset='open'))
    result=build_commander_preview(job,body,store,catalog,tmp_path)
    saved=save_preview(tmp_path,result)
    assert read_commander_preview(tmp_path,job,saved['preview_sha256'],store)==saved
    assert saved['expression_track'][0]['preset']=='surprised'
    body.expression_track[0]['channel']='mouth'
    with pytest.raises(LifeValidationError):build_commander_preview(job,body,store,catalog,tmp_path)


def test_scene_registry_preserves_previous_commander_catalog(tmp_path):
    from my_legend_production.production.commander_preview import commander_scene_digest
    store,job,catalog,body=commander(tmp_path)
    scene_hash=body.scene_bindings[0]['scene']['catalog_sha256']
    (tmp_path/'commander-scene-current.json').write_text(json.dumps({'catalog_sha256':'f'*64,'by_character_catalog':{catalog['catalog_sha256']:scene_hash}}))
    assert commander_scene_digest(tmp_path,catalog)==scene_hash
