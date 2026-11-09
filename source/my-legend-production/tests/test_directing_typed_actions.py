from copy import deepcopy
import json
import pytest
from pydantic import ValidationError
from test_spine_director import sample,seeded,Model,run_worker
from test_directing_protocol import group
from test_production_queue import env
from my_legend_production.production.directing_protocol import resolve_plan,grouped_schema
from my_legend_production.production.directing_compile import compile_plan
from my_legend_production.production.contracts import ArtifactRef


def structured(plan,context):
    wire=group(plan,context);wire['schema_version']='spine_directing_plan/1.2'
    for beat in wire['beats']:
        for a in beat['actions']:
            target_kind=a.pop('target_kind','none');target_id=a.pop('target_id',None);x=a.pop('target_x',None)
            if a['kind']=='move':
                a['target']={'kind':target_kind}
                a['target']['x' if target_kind=='position' else 'role_id' if target_kind=='actor' else 'id']=x if target_kind=='position' else target_id
            elif a['kind']=='enter':a.update(kind='walk_in',slot_id=target_id)
            elif a['kind']=='exit':a.update(kind='walk_out',entrance_id=target_id)
    return wire


@pytest.mark.parametrize('kind',['move','enter','exit','face','idle'])
def test_typed_actions_preserve_compiled_behavior(tmp_path,kind):
    store,job,_,context,plan=sample(tmp_path);a=plan['actions'][0]
    if kind=='enter':
        plan['scenes'][0]['actors'][0]['visible']=False
        a.update(kind=kind,target_kind='slot',target_id='slot_0')
    elif kind=='exit':a.update(kind=kind,target_kind='entrance',target_id='left')
    elif kind=='face':a.update(kind=kind,target_kind='none',target_id=None,facing=1)
    elif kind=='idle':a.update(kind=kind,target_kind='none',target_id=None)
    old=resolve_plan(group(plan,context),context);wire=structured(plan,context);before=deepcopy(wire)
    new=resolve_plan(wire,context)
    assert wire==before and new==old
    assert compile_plan(job,store,context,new,tmp_path)==compile_plan(job,store,context,old,tmp_path)


@pytest.mark.parametrize('action',[
    {'kind':'walk_in','target':{'kind':'position','x':.94}},
    {'kind':'walk_in','slot_id':'slot_0','target_x':.94},
    {'kind':'walk_out','slot_id':'slot_0'},
    {'kind':'move','target':{'kind':'position','id':'slot_0'}},
    {'kind':'move','target':{'kind':'actor','id':'b'}},
    {'kind':'enter','slot_id':'slot_0'},
])
def test_incompatible_target_fields_are_rejected_at_schema_boundary(tmp_path,action):
    _,_,_,context,plan=sample(tmp_path);wire=structured(plan,context)
    wire['beats'][-1]['actions']=[{'role_id':'a','reason':'测试非法组合',**action}]
    with pytest.raises(ValidationError):resolve_plan(wire,context)


def test_typed_schema_makes_entry_and_exit_targets_unambiguous(tmp_path):
    _,_,_,context,_=sample(tmp_path);defs=grouped_schema(context)['$defs']
    assert 'target' not in defs['WalkInAction']['properties']
    assert defs['WalkInAction']['properties']['slot_id']['enum']==['slot_0','slot_1']
    assert set(defs['WalkOutAction']['required'])=={'role_id','reason','kind','entrance_id'}


def test_typed_plan_and_structured_review_complete_real_worker_flow(env,tmp_path,monkeypatch):
    repo,store,source,old,request=seeded(env,tmp_path,monkeypatch)
    class TypedModel(Model):
        def complete(self,system,payload,schema,timeout):
            response=super().complete(system,payload,schema,timeout)
            if 'required_checks' not in payload:
                response['content']=json.dumps(structured(json.loads(response['content']),{'model_input':payload}))
            return response
    model=TypedModel();jid=repo.create('alice',request);w=run_worker(repo,store,model)
    for _ in range(4):assert w.tick()
    job=repo.get(jid,'alice');assert job['status']=='succeeded' and model.calls==2
    evidence=store.json(ArtifactRef.model_validate(next(a for a in job['artifacts'] if a['kind']=='SpineDirectingEvidence')))
    assert json.loads(evidence['responses'][0]['content'])['schema_version']=='spine_directing_plan/1.2'
    assert json.loads(evidence['responses'][1]['content'])['schema_version']=='spine_directing_review/1.2'
