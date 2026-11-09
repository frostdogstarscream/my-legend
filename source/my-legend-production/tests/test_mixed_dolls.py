from copy import deepcopy
import json
import pytest
from test_commander_preview import commander, save_body
from my_legend_production.production.life_contracts import canonical_hash, validate_catalog, LifeValidationError
from my_legend_production.production.commander_preview import build_commander_preview, read_commander_preview
from my_legend_production.production.spine_preview import save_preview
from my_legend_production.production.creation_library import wardrobe_characters

def mixed(tmp_path):
    store,job,catalog,body=commander(tmp_path,'甲安静地看着乙。')
    actor=dict(actor_id='doll',revision='doll-r1',asset_mode='single_skeleton',asset_id='s',outfit_ids=['native'],default_outfit_id='native',
        action_map=dict(idle='wait',walk='move',sit='sit',pick='pick',lie='lying'),lifecycle='ready',asset_qa='pass',display_name='国风人形')
    cal={'full':dict(x=0,y=0,scale=1,rotation=0,flip=False,layer=0,actions={})}
    catalog['actors'].append(actor);catalog['outfits'].append(dict(outfit_id='native',actor_id='doll',parts={'full':'s'},calibration=cal,
        calibration_revision=canonical_hash(cal),allowed_actions=list(actor['action_map']),qa='pass',display_name='国风人形',style_category='国风'))
    for outfit in catalog['outfits']:outfit.setdefault('display_name',outfit['outfit_id'])
    catalog['actor_model']='mixed_life/1';catalog['catalog_sha256']=canonical_hash({k:v for k,v in catalog.items() if k!='catalog_sha256'})
    (tmp_path/'catalogs'/(catalog['catalog_sha256']+'.json')).write_text(json.dumps(catalog))
    body.bindings['a']='doll';body.initial_outfits={'a':'native','b':'female1'}
    return store,job,catalog,body

@pytest.mark.parametrize('action',['idle','walk','sit','pick','lie'])
def test_native_actions_roundtrip_with_commander(tmp_path,action):
    store,job,catalog,body=mixed(tmp_path);validate_catalog(catalog)
    value=build_commander_preview(job,body,store,catalog,tmp_path)
    assert value['mouth_enabled']=={'actor_1':False,'actor_2':True}
    assert value['face_profiles']['actor_1'] is None
    edit=save_body(value)
    edit.script['cues']=[dict(id='native-action',type='body.play',instance_id='actor_1',start_frame=0,end_frame=8000,enabled=True,
        params={'action':action,'mode':'loop'},source=None,source_text='',rule_id='manual',reason='素材动作演示')]
    saved=save_preview(tmp_path,build_commander_preview(job,edit,store,catalog,tmp_path))
    assert read_commander_preview(tmp_path,job,saved['preview_sha256'],store)==saved
    assert saved['stage']['cues'][0]['params']['action']==action
    assert any(c['stageBinding']['actor_id']=='doll' for c in wardrobe_characters(catalog))

def test_native_does_not_claim_commander_expressions(tmp_path):
    store,job,catalog,body=mixed(tmp_path);value=build_commander_preview(job,body,store,catalog,tmp_path)
    edit=save_body(value);edit.expression_track=[dict(id='bad',instance_id='actor_1',start_frame=0,end_frame=1000,channel='eyes',preset='smile')]
    with pytest.raises(LifeValidationError):build_commander_preview(job,edit,store,catalog,tmp_path)

def test_native_cannot_borrow_another_skeleton(tmp_path):
    _,_,catalog,_=mixed(tmp_path)
    catalog['outfits'][-1]['parts']['full']='other'
    catalog['catalog_sha256']=canonical_hash({k:v for k,v in catalog.items() if k!='catalog_sha256'})
    with pytest.raises(LifeValidationError):validate_catalog(catalog)

@pytest.mark.parametrize('action',['sit','pick','lie'])
def test_directing_can_compile_native_body_actions_without_fake_faces(tmp_path,action):
    from my_legend_production.production.contracts import DirectedPerformanceSource
    from my_legend_production.production.directing_compile import create_context, compile_plan
    from my_legend_production.production.directing_protocol import BodyAction
    store,job,catalog,body=mixed(tmp_path)
    roompath=tmp_path/'scene-catalogs'/(body.scene_bindings[0]['scene']['catalog_sha256']+'.json')
    rooms=json.loads(roompath.read_text());rooms['commander_catalog_sha256']=catalog['catalog_sha256']
    rooms.pop('catalog_sha256');rooms['catalog_sha256']=canonical_hash(rooms)
    (tmp_path/'scene-catalogs'/(rooms['catalog_sha256']+'.json')).write_text(json.dumps(rooms))
    source=DirectedPerformanceSource(source_job_id=job['id'],source_version_id='v1',inputs=job['artifacts'],
        catalog_sha256=catalog['catalog_sha256'],scene_catalog_sha256=rooms['catalog_sha256'],bindings=body.bindings,initial_outfits=body.initial_outfits)
    context=create_context(job,source,store,tmp_path);lines=context['model_input']['source_lines']
    assert context['model_input']['locked_cast'][0]['mouth']==['native']
    assert action in BodyAction.model_fields['kind'].annotation.__args__
    plan=dict(scenes=[dict(episode_id='ep',scene_id='scene',resource_scene_id='library',reason='动作测试',difference='',
        actors=[dict(role_id='a',x=.333,facing=1,visible=True),dict(role_id='b',x=.667,facing=-1,visible=True)])],
        actions=[dict(id='native',role_id='a',anchor={'source':lines[0]['source']},quote=lines[0]['text'],reason='原生动作编译测试',kind=action,duration_ms=1000)],
        expressions=[dict(id='face',role_id='a',anchor={'source':lines[0]['source']},quote=lines[0]['text'],reason='保留原生',channel='eyes',preset='native',duration_ms=1000)],
        assessments=[dict(source=l['source'],quote=l['text'],classification='performed' if i==0 else 'gap' if l['kind']=='action' else 'no_visible_action',
            cue_ids=['native'] if i==0 else [],reason='动作编译测试') for i,l in enumerate(lines)])
    preview,_=compile_plan(job,store,context,plan,tmp_path)
    assert preview['face_profiles']['actor_1'] is None
    assert preview['mouth_enabled']['actor_1'] is False
    assert preview['stage']['cues'][0]['params']['action']==action
