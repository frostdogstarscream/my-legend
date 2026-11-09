from copy import deepcopy
import json
import pytest
from pydantic import ValidationError
from test_spine_director import sample,seeded,Model,run_worker
from test_production_queue import env
from my_legend_production.production.directing_protocol import resolve_plan,source_refs,grouped_schema,planner_input
from my_legend_production.production.directing_compile import compile_plan
from my_legend_production.production.life_contracts import LifeValidationError
from my_legend_production.production.contracts import ArtifactRef


def group(plan,context):
    refs=source_refs(context)
    value={'schema_version':'spine_directing_plan/1.1','scenes':deepcopy(plan['scenes']),'beats':[]}
    for ref,line in refs.items():
        old=next(a for a in plan['assessments'] if a['source']==line['source'])
        beat={k:deepcopy(old[k]) for k in ('quote','classification','reason')};beat['source_ref']=ref
        for track in ('actions','expressions','furniture'):
            beat[track]=[]
            for cue in plan.get(track,[]):
                if cue['anchor']['source']!=line['source']:continue
                local={k:deepcopy(v) for k,v in cue.items() if k not in ('id','quote')}
                local['anchor'].pop('source');beat[track].append(local)
        value['beats'].append(beat)
    return value


def test_grouped_sources_compile_and_do_not_mutate_input(tmp_path):
    store,job,source,context,plan=sample(tmp_path);wire=group(plan,context);before=deepcopy(wire)
    resolved=resolve_plan(wire,context)
    value,summary=compile_plan(job,store,context,resolved,tmp_path)
    assert wire==before and value['review_confirmed'] is False
    for a in resolved.assessments:
        for cid in a.cue_ids:
            cue=next(c for c in [*resolved.actions,*resolved.expressions,*resolved.furniture] if c.id==cid)
            assert cue.anchor.source==a.source
    assert value['audio']['artifact'] and len(summary['execution'])==2
    schema=grouped_schema(context)
    assert schema['$defs']['CoordinatedExitBeat']['properties']['source_ref']['enum']==list(source_refs(context))
    assert 'id' not in schema['$defs']['MoveAction']['properties']
    assert 'source' not in schema['$defs']['LocalAnchor']['properties']


@pytest.mark.parametrize('case',['missing','duplicate','unknown','injected_source','injected_cue_ids','bad_quote','performed_empty','bad_json'])
def test_grouped_protocol_rejects_bad_coverage_without_repairing_it(tmp_path,case):
    store,job,source,context,plan=sample(tmp_path);wire=group(plan,context)
    if case=='missing':wire['beats'].pop()
    if case=='duplicate':wire['beats'].append(deepcopy(wire['beats'][0]))
    if case=='unknown':wire['beats'][0]['source_ref']='src_9999'
    if case=='injected_source':wire['beats'][-1]['actions'][0]['anchor']['source']=plan['assessments'][0]['source']
    if case=='injected_cue_ids':wire['beats'][0]['cue_ids']=['src_0003_a1']
    if case=='bad_quote':wire['beats'][0]['quote']='这是原文里没有的话'
    if case=='performed_empty':wire['beats'][0]['classification']='performed'
    if case=='bad_json':wire='{"schema_version":'
    with pytest.raises((LifeValidationError,ValidationError)):
        compile_plan(job,store,context,resolve_plan(wire,context),tmp_path)


@pytest.mark.parametrize('target,error',[(.4,None),(.01,'通道'),(.6,'占用')])
def test_explicit_following_endpoints_preserve_geometry_checks(tmp_path,target,error):
    store,job,source,context,plan=sample(tmp_path)
    actors=plan['scenes'][0]['actors'];actors[0]['x']=.4;actors[1]['x']=.2
    cue=plan['actions'][0]
    cue.update(id='leader',target_kind='position',target_id=None,target_x=.6,duration_ms=1000)
    other=deepcopy(cue);other.update(id='follower',role_id='b',target_x=target)
    plan['actions'].append(other);plan['assessments'][-1]['cue_ids']=['leader','follower']
    if error:
        with pytest.raises(LifeValidationError) as exc:compile_plan(job,store,context,plan,tmp_path)
        assert any(error in d['message'] for d in exc.value.diagnostics)
    else:
        value,summary=compile_plan(job,store,context,plan,tmp_path)
        moves=[c for c in value['stage']['cues'] if c['type']=='actor.move_to']
        assert [c['params']['to'][0] for c in moves]==[.4,.6] or [c['params']['to'][0] for c in moves]==[.6,.4]


def test_grouped_model_worker_keeps_raw_response_and_review(env,tmp_path,monkeypatch):
    from my_legend_production.production.performance_delivery import delivered_preview
    repo,store,source,old,request=seeded(env,tmp_path,monkeypatch)
    class GroupedModel(Model):
        def complete(self,system,payload,schema,timeout):
            response=super().complete(system,payload,schema,timeout)
            if 'required_checks' not in payload:
                assert schema['title']=='CoordinatedExitPlan'
                assert payload['source_lines'][0]['source_ref']=='src_0001'
                response['content']=json.dumps(group(json.loads(response['content']),{'model_input':payload}))
            return response
    model=GroupedModel();jid=repo.create('alice',request);w=run_worker(repo,store,model)
    for _ in range(4):assert w.tick()
    job=repo.get(jid,'alice');assert job['status']=='succeeded' and model.calls==2
    result=delivered_preview(job,store)
    assert result['preview']['audio']==old['audio'] and result['manifest']['human_review']=='pending'
    evidence=store.json(ArtifactRef.model_validate(next(a for a in job['artifacts'] if a['kind']=='SpineDirectingEvidence')))
    assert json.loads(evidence['responses'][0]['content'])['schema_version']=='spine_directing_plan/1.1'
    assert evidence['responses'][1]['phase']=='review'


def test_same_line_id_in_other_scene_has_distinct_identity(tmp_path):
    _,_,_,context,plan=sample(tmp_path)
    context=deepcopy(context);line=deepcopy(context['model_input']['source_lines'][-1])
    line['source']['scene_id']='second-scene';context['model_input']['source_lines'].append(line)
    first_context=deepcopy(context);first_context['model_input']['source_lines'].pop()
    wire=group(plan,first_context);beat=deepcopy(wire['beats'][-1]);beat['source_ref']='src_0004';wire['beats'].append(beat)
    resolved=resolve_plan(wire,context)
    assert resolved.actions[0].anchor.source.line_id==resolved.actions[1].anchor.source.line_id
    assert resolved.actions[0].anchor.source.scene_id!=resolved.actions[1].anchor.source.scene_id
    assert resolved.assessments[-1].cue_ids==['src_0004_a1']


@pytest.mark.parametrize('visible,target_kind,target_id,message',[(True,'slot','slot_0','当前已经可见'),(False,'entrance','left','终点必须是 slot')])
def test_enter_diagnostics_distinguish_visibility_and_target(tmp_path,visible,target_kind,target_id,message):
    store,job,_,context,plan=sample(tmp_path)
    plan['scenes'][0]['actors'][0]['visible']=visible
    plan['actions'][0].update(kind='enter',target_kind=target_kind,target_id=target_id)
    with pytest.raises(LifeValidationError) as exc:compile_plan(job,store,context,plan,tmp_path)
    assert message in exc.value.diagnostics[0]['message']
