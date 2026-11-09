from copy import deepcopy
import json
import pytest
from test_spine_director import sample, seeded, run_worker, Model
from test_production_queue import env
from my_legend_production.production.stage_policy import POLICY, StagePlan, StageReview
from my_legend_production.production.asset_capabilities import build_capabilities, save_capabilities, enrich_context
from my_legend_production.production.directing_compile import compile_plan, validate_review, required_checks
from my_legend_production.production.directing_protocol import resolve_plan, grouped_schema
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.life_contracts import LifeValidationError


def staged_sample(tmp_path):
    store,job,source,context,plan=sample(tmp_path)
    catalog=json.loads((tmp_path/'catalogs'/(context['catalog_sha256']+'.json')).read_text())
    rooms=json.loads((tmp_path/'scene-catalogs'/(context['scene_catalog_sha256']+'.json')).read_text())
    caps=build_capabilities(catalog,rooms);save_capabilities(tmp_path,caps);enrich_context(context,caps)
    value=plan.model_dump() if hasattr(plan,'model_dump') else deepcopy(plan)
    value['schema_version']='spine_directing_plan/2.0'
    for a in value['assessments']:
        a.update(classification='performed' if a.get('cue_ids') else 'no_visible_action',
                 presentation='adapted',intent='表达观察与寻找',simplification='翻找细节由原声音承载，使用已有走位表达探查。')
    return store,job,context,StagePlan.model_validate(value)


def review_for(context,passed=True):
    quotes={'line:'+':'.join(l['source'][k] for k in ('episode_id','scene_id','line_id')):l['text'] for l in context['model_input']['source_lines']}
    return StageReview(schema_version='spine_directing_review/2.0',passed=passed,
        checks=[dict(key=k,passed=passed,quote=quotes.get(k,''),reason='演绎合理且保持声音事实') for k in required_checks(context)])


def test_adaptation_is_nonblocking_and_capability_snapshot_is_bound(tmp_path):
    store,job,context,plan=staged_sample(tmp_path)
    value,summary=compile_plan(job,store,context,plan,tmp_path)
    validate_review(review_for(context),context,plan)
    assert value['status']=='preview_ready' and value['interpretation_notes']
    assert not any(c['status']=='needs_correction' for c in value['coverage'])
    assert value['coverage'][-1]['presentation']=='adapted'
    assert value['visual_policy']==POLICY and summary['visual_policy']==POLICY
    assert context['model_input']['scene_resources'][0]['objects'][0]['manipulation_supported'] is False
    corrupted=deepcopy(context);corrupted['capabilities']['scenes'][0]['description']='invented'
    with pytest.raises(ProductionError,match='CAPABILITY_HASH_MISMATCH'):compile_plan(job,store,corrupted,plan,tmp_path)


def test_new_policy_does_not_bypass_review_or_technical_errors(tmp_path):
    store,job,context,plan=staged_sample(tmp_path)
    with pytest.raises(ProductionError,match='DIRECTING_REVIEW_REJECTED'):validate_review(review_for(context,False),context,plan)
    legacy=deepcopy(context);legacy.pop('visual_policy')
    with pytest.raises(ProductionError,match='DIRECTING_POLICY_MISMATCH'):compile_plan(job,store,legacy,plan,tmp_path)
    bad=plan.model_copy(deep=True);bad.actions[0].target_id='invented_prop'
    with pytest.raises(LifeValidationError):compile_plan(job,store,context,bad,tmp_path)
    bad=plan.model_copy(deep=True);bad.scenes[0].actors[0].x=bad.scenes[0].actors[1].x
    with pytest.raises(LifeValidationError):compile_plan(job,store,context,bad,tmp_path)


def test_wire_schema_keeps_presentation_and_audio_led_silence(tmp_path):
    store,job,context,plan=staged_sample(tmp_path)
    wire=dict(schema_version='spine_directing_plan/1.5',scenes=[s.model_dump() for s in plan.scenes],
        beats=[dict(source_ref=f'src_{i:04d}',quote=l['text'],classification='no_visible_action',
            reason='安静倾听',presentation='audio_led',intent='由声音交代细节',simplification='无对应道具，维持自然姿态')
            for i,l in enumerate(context['model_input']['source_lines'],1)])
    resolved=resolve_plan(wire,context)
    assert isinstance(resolved,StagePlan) and resolved.assessments[0].presentation=='audio_led'
    assert grouped_schema(context)['properties']['schema_version']['const']=='spine_directing_plan/1.5'
    result,_=compile_plan(job,store,context,resolved,tmp_path)
    assert result['status']=='preview_ready'


def test_staged_timing_errors_cannot_be_published_as_simplifications(tmp_path):
    store,job,context,plan=staged_sample(tmp_path)
    bad=plan.model_copy(deep=True)
    bad.actions[0].duration_ms=30000
    with pytest.raises(LifeValidationError) as error:
        compile_plan(job,store,context,bad,tmp_path)
    assert any(d['code']=='DIRECTING_TIME_CONFLICT' for d in error.value.diagnostics)


class StageModel(Model):
    def complete(self,system,payload,schema,timeout):
        response=super().complete(system,payload,schema,timeout);value=json.loads(response['content'])
        if 'required_checks' in payload:
            assert '碎石' in system
            value['schema_version']='spine_directing_review/2.0'
            for c in value['checks']:
                for k in ('coverage_complete','unperformed_actions','unrendered_details'):c.pop(k)
        else:
            assert payload['visual_policy']==POLICY
            value['schema_version']='spine_directing_plan/1.5'
            value['beats']=[dict(source_ref=l['source_ref'],quote=l['text'],classification='no_visible_action',
                presentation='audio_led',reason='声音引导叙事',intent='倾听与停顿',simplification='精细交互由声音承载') for l in payload['source_lines']]
            value.pop('assessments')
        return {'content':json.dumps(value,ensure_ascii=False)}


def test_new_default_full_delivery_reload_and_manual_revision(env,tmp_path,monkeypatch):
    from my_legend_production.production.performance_delivery import delivered_preview
    from my_legend_production.production.spine_director import read_directed_preview
    from my_legend_production.production.directing_edit import edit_directed
    from my_legend_production.production.spine_preview import StagePreviewRequest,save_preview
    repo,store,source,old,request=seeded(env,tmp_path,monkeypatch,legacy_budget=False,asset_policy=True)
    assert request.source.visual_policy==POLICY
    jid=repo.create('alice',request);model=StageModel();worker=run_worker(repo,store,model)
    for _ in range(4):assert worker.tick()
    job=repo.get(jid,'alice');assert job['status']=='succeeded',job
    result=delivered_preview(job,store);value=result['preview']
    assert result['manifest']['status']=='preview_ready'
    assert result['manifest']['model_review']=='passed_with_adaptations'
    assert result['manifest']['interpretation_notes']==value['interpretation_notes']
    assert any(a['kind']=='StageCapabilityCatalog' for a in job['artifacts'])
    assert value['audio']==old['audio'] and repo.get(source['id'],'alice')==source
    assert read_directed_preview(tmp_path,source,value['preview_sha256'],store)==value
    body=StagePreviewRequest(profile='life_v2',operation='validate_and_save',directed_from=value['preview_sha256'],
        script=value['stage'],scene_bindings=value['scene_bindings'],furniture_track=value['furniture_track'],
        expression_track=value['expression_track'],mouth_enabled=value['mouth_enabled'])
    edited=save_preview(tmp_path,edit_directed(tmp_path,source,body,store))
    assert edited['visual_policy']==POLICY and edited['directing']['review_status']=='edited_pending'
    assert read_directed_preview(tmp_path,source,edited['preview_sha256'],store)==edited

