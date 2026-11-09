from copy import deepcopy
import json

import pytest

from test_spine_director import sample, seeded, Model, run_worker
from test_production_queue import env
from test_directing_typed_actions import structured
from my_legend_production.production.coordinated_move import group_trajectories
from my_legend_production.production.directing_compile import compile_plan
from my_legend_production.production.directing_protocol import resolve_plan, grouped_schema
from my_legend_production.production.life_contracts import LifeValidationError
from my_legend_production.production.h5_preview import position_at, validate_cues_in_scenes
from my_legend_production.production.scene_preview import load_scene_catalog


def group_plan(tmp_path):
    store,job,_,context,plan=sample(tmp_path)
    plan['scenes'][0]['actors'][0]['x']=.5
    plan['scenes'][0]['actors'][1]['x']=.3
    plan['actions'][0].update(kind='move_group',followers=['b'],target_kind='position',target_id=None,target_x=.75,duration_ms=1000)
    return store,job,context,plan


def test_followers_keep_order_spacing_and_speed_without_changing_director_choices(tmp_path):
    store,job,context,plan=group_plan(tmp_path);before=deepcopy(plan)
    preview,summary=compile_plan(job,store,context,plan,tmp_path)
    group=next(e for e in summary['execution'] if e['kind']=='move_group')
    assert len(group['members'])==2 and group['members'][0]['role_id']=='a'
    segment=preview['scene_track']['segments'][0]
    for fraction in (0,.1,.5,.9,1):
        frame=group['start_frame']+fraction*(group['end_frame']-group['start_frame'])
        a=position_at(preview['stage'],segment,'actor_1',frame)
        b=position_at(preview['stage'],segment,'actor_2',frame)
        assert a['position'][0]-b['position'][0]==pytest.approx(.2)
        assert a['position'][0]==pytest.approx(.5+.25*fraction)
        assert a['visible'] and b['visible']
    assert plan==before and preview['coverage'][-1]['cue_ids']==['approach']
    assert preview['coverage'][-1]['status']=='needs_correction'  # Search remains unsupported.
    for member in group['members']:
        assert preview['cue_evidence'][member['cue_id']]['source']==plan['actions'][0]['anchor']['source']


@pytest.mark.parametrize('case,fragment',[
    ('wrong_leader','领走者'),('hidden','已经在场'),('duplicate','不同'),
    ('outside','通道'),('stationary','位移'),('unknown','未确认')])
def test_group_does_not_reorder_teleport_or_clip_invalid_plans(tmp_path,case,fragment):
    store,job,context,plan=group_plan(tmp_path)
    if case=='wrong_leader':plan['scenes'][0]['actors'][0]['x']=.1
    if case=='hidden':plan['scenes'][0]['actors'][1]['visible']=False
    if case=='duplicate':plan['actions'][0]['followers']=['a']
    if case=='outside':plan['actions'][0]['target_x']=.99
    if case=='stationary':plan['actions'][0]['target_x']=.5
    if case=='unknown':plan['actions'][0]['followers']=['new_actor']
    with pytest.raises(LifeValidationError) as exc:compile_plan(job,store,context,plan,tmp_path)
    assert any(fragment in d['message'] for d in exc.value.diagnostics)


def test_leftward_group_leader_and_trailing_endpoint():
    states=[dict(instance_id='a',position=[.4,.8],visible=True),dict(instance_id='b',position=[.6,.8],visible=True)]
    result=group_trajectories('left',states,[.1,.8],[.06,.8,.94,.8])
    assert result[0]['to'][0]==.1 and result[1]['to'][0]==pytest.approx(.3)
    with pytest.raises(LifeValidationError):group_trajectories('bad',states,[.8,.8],[.06,.8,.94,.8])


def test_group_cues_still_collide_with_non_participating_actor(tmp_path):
    store,job,context,plan=group_plan(tmp_path)
    preview,_=compile_plan(job,store,context,plan,tmp_path)
    third=deepcopy(preview['stage']['actors'][0]);third.update(instance_id='actor_3',role_id='c')
    preview['stage']['actors'].append(third)
    preview['scene_track']['segments'][0]['actors'].append(dict(instance_id='actor_3',position=[.6,.825],visible=True,facing=1))
    catalog=load_scene_catalog(tmp_path,context['scene_catalog_sha256'])
    with pytest.raises(LifeValidationError) as exc:
        validate_cues_in_scenes(preview['stage'],preview['scene_track']['segments'],{context['scene_catalog_sha256']:catalog})
    assert any('占用' in d['message'] for d in exc.value.diagnostics)


def test_protocol_13_expands_only_the_directed_group(tmp_path):
    store,job,context,plan=group_plan(tmp_path)
    solo=deepcopy(plan);solo['actions'][0].update(kind='move');solo['actions'][0].pop('followers')
    wire=structured(solo,context);wire['schema_version']='spine_directing_plan/1.3'
    action=wire['beats'][-1]['actions'][0];action['kind']='move_group';action['followers']=['b']
    resolved=resolve_plan(wire,context);preview,summary=compile_plan(job,store,context,resolved,tmp_path)
    assert len(next(e for e in summary['execution'] if e['kind']=='move_group')['members'])==2
    schema=grouped_schema(context)
    assert schema['properties']['schema_version']['const']=='spine_directing_plan/1.4'
    assert schema['$defs']['GroupMoveAction']['properties']['followers']['items']['enum']==['a','b']


@pytest.mark.parametrize('group_kind',['move_group','walk_out_group'])
def test_group_proof_survives_complete_worker_delivery(env,tmp_path,monkeypatch,group_kind):
    from my_legend_production.production.performance_delivery import delivered_preview
    from my_legend_production.production.spine_director import read_directed_preview
    repo,store,source,old,request=seeded(env,tmp_path,monkeypatch)
    class GroupModel(Model):
        def complete(self,system,payload,schema,timeout):
            response=super().complete(system,payload,schema,timeout)
            if 'required_checks' not in payload:
                value=json.loads(response['content'])
                value['scenes'][0]['actors'][0]['x']=.5;value['scenes'][0]['actors'][1]['x']=.3
                wire=structured(value,{'model_input':payload});wire['schema_version']='spine_directing_plan/1.4'
                action=dict(kind=group_kind,role_id='a',followers=['b'],duration_ms=1000,reason='两人同行的制作选择')
                if group_kind=='walk_out_group':action['entrance_id']='right'
                else:action['target']={'kind':'position','x':.75}
                wire['beats'][-1]['actions']=[action]
                response['content']=json.dumps(wire)
            return response
    model=GroupModel();jid=repo.create('alice',request);worker=run_worker(repo,store,model)
    for _ in range(4):assert worker.tick()
    job=repo.get(jid,'alice');assert job['status']=='succeeded' and model.calls==2
    value=delivered_preview(job,store)['preview']
    assert read_directed_preview(tmp_path,source,value['preview_sha256'],store)==value
    assert value['audio']==old['audio'] and repo.get(source['id'],'alice')==source


def test_group_exit_solves_real_catch_up_failure_without_relaxing_collision(tmp_path):
    store,job,context,plan=group_plan(tmp_path)
    plan['scenes'][0]['actors'][0]['x']=.82
    plan['scenes'][0]['actors'][1]['x']=.48599999999999993
    action=plan['actions'][0]
    action.update(kind='exit_group',target_kind='entrance',target_id='right',target_x=None,duration_ms=1400)
    original=deepcopy(plan)
    preview,summary=compile_plan(job,store,context,plan,tmp_path)
    group=next(e for e in summary['execution'] if e['kind']=='exit_group')
    lead,follow=group['members'];start=group['start_frame'];rate=preview['stage']['sample_rate']
    assert lead['end_frame']<follow['end_frame']==group['end_frame']
    segment=preview['scene_track']['segments'][0]
    for seconds in (0,.1,.2,.35):
        a=position_at(preview['stage'],segment,'actor_1',start+seconds*rate)
        b=position_at(preview['stage'],segment,'actor_2',start+seconds*rate)
        assert a['visible'] and b['visible']
        one_frame_distance=(.94-.486)/(group['end_frame']-group['start_frame'])
        assert a['position'][0]-b['position'][0]==pytest.approx(.334,abs=one_frame_distance+1e-9)
    assert not position_at(preview['stage'],segment,'actor_1',lead['end_frame'])['visible']
    assert position_at(preview['stage'],segment,'actor_2',lead['end_frame'])['visible']
    assert not position_at(preview['stage'],segment,'actor_2',follow['end_frame'])['visible']
    assert plan==original
    # The previous independent-duration plan still fails the unchanged margin.
    action.update(kind='exit',followers=[])
    other=deepcopy(action);other.update(id='follower-exit',role_id='b');other['anchor']['offset_ms']=300
    plan['actions'].append(other);plan['assessments'][-1]['cue_ids'].append(other['id'])
    with pytest.raises(LifeValidationError) as exc:compile_plan(job,store,context,plan,tmp_path)
    assert any('占用' in d['message'] for d in exc.value.diagnostics)


def test_protocol_14_group_exit_maps_to_real_entrance_and_retains_source(tmp_path):
    store,job,context,plan=group_plan(tmp_path)
    solo=deepcopy(plan);solo['actions'][0].update(kind='exit',followers=[],target_kind='entrance',target_id='right',target_x=None)
    solo['actions'][0].pop('followers')
    wire=structured(solo,context);wire['schema_version']='spine_directing_plan/1.4'
    wire['beats'][-1]['actions'][0].update(kind='walk_out_group',followers=['b'])
    resolved=resolve_plan(wire,context)
    assert resolved.actions[0].kind=='exit_group' and resolved.actions[0].target_id=='right'
    preview,summary=compile_plan(job,store,context,resolved,tmp_path)
    group=next(e for e in summary['execution'] if e['kind']=='exit_group')
    assert len(group['members'])==2
    assert all(c['source']==resolved.actions[0].anchor.source.model_dump() for c in preview['stage']['cues'] if c['type']=='actor.move_to')
