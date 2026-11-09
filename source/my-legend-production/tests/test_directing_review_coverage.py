from copy import deepcopy
import json
import pytest
from test_spine_director import sample,seeded,Model,run_worker
from test_production_queue import env
from my_legend_production.production.directing_contracts import CoverageReview,VisualCoverageReview,parse_review
from my_legend_production.production.directing_compile import required_checks,validate_review
from my_legend_production.production.life_contracts import LifeValidationError
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.contracts import ArtifactRef


def review_for(context):
    quotes={'line:'+':'.join(l['source'][k] for k in ('episode_id','scene_id','line_id')):l['text'] for l in context['model_input']['source_lines']}
    return CoverageReview(schema_version='spine_directing_review/1.1',passed=True,checks=[dict(key=k,passed=True,
        quote=quotes.get(k,''),reason='移动已执行，翻找无法执行但总体可以接受',coverage_complete=True if k in quotes else None,
        unperformed_actions=[]) for k in required_checks(context)])


def test_review_cannot_approve_partial_search_as_performed(tmp_path):
    _,_,_,context,plan=sample(tmp_path);plan['assessments'][-1]['classification']='performed'
    review=review_for(context);c=review.checks[-1];c.coverage_complete=False;c.unperformed_actions=['翻找']
    with pytest.raises(LifeValidationError) as exc:validate_review(review,context,plan)
    error=exc.value.diagnostics[0]
    assert error['code']=='COVERAGE_CONTRADICTION' and error['unperformed_actions']==['翻找']
    assert error['source']==plan['assessments'][-1]['source']
    # Marking this line's missing action truthfully is acceptable, not full coverage.
    plan['assessments'][-1]['classification']='gap'
    validate_review(review,context,plan)


@pytest.mark.parametrize('case',['false_no_quote','true_with_gap','wrong_quote','nonline_coverage','missing_coverage'])
def test_structured_review_has_consistent_source_evidence(tmp_path,case):
    _,_,_,context,plan=sample(tmp_path);review=review_for(context);c=review.checks[-1]
    c.coverage_complete=False;c.unperformed_actions=['翻找']
    if case=='false_no_quote':c.unperformed_actions=[]
    if case=='true_with_gap':c.coverage_complete=True
    if case=='wrong_quote':c.unperformed_actions=['飞上天空']
    if case=='nonline_coverage':review.checks[0].coverage_complete=True
    if case=='missing_coverage':c.coverage_complete=None
    with pytest.raises(ProductionError):validate_review(review,context,plan)


def test_legacy_review_remains_readable(tmp_path):
    _,_,_,context,plan=sample(tmp_path)
    raw=review_for(context).model_dump();raw.pop('schema_version')
    for c in raw['checks']:c.pop('coverage_complete');c.pop('unperformed_actions')
    validate_review(parse_review(raw),context,plan)


def test_visible_prop_gap_is_separate_from_action_completion(tmp_path):
    _,_,_,context,plan=sample(tmp_path)
    line=context['model_input']['source_lines'][-1];line['text']='书页间露出一小截纸片的边缘。'
    plan['assessments'][-1]['quote']=line['text']
    raw=review_for(context).model_dump();raw['schema_version']='spine_directing_review/1.2'
    for check in raw['checks']:check['unrendered_details']=[]
    raw['checks'][-1]['unrendered_details']=['纸片的边缘']
    review=VisualCoverageReview.model_validate(raw)
    assert review.checks[-1].coverage_complete is True
    validate_review(review,context,plan)
    assert isinstance(parse_review(raw),VisualCoverageReview)
    review.checks[-1].unrendered_details=[]
    with pytest.raises(LifeValidationError):validate_review(review,context,plan)


def test_invalid_review_evidence_reports_exact_source_and_field(tmp_path):
    _,_,_,context,plan=sample(tmp_path)
    raw=review_for(context).model_dump();raw['schema_version']='spine_directing_review/1.2'
    for c in raw['checks']:c['unrendered_details']=[]
    raw['checks'][-1].update(coverage_complete=False,unperformed_actions=['翻找'],unrendered_details=['不存在的提灯'])
    with pytest.raises(ProductionError) as exc:validate_review(VisualCoverageReview.model_validate(raw),context,plan)
    assert exc.value.code=='DIRECTING_REVIEW_EVIDENCE_INVALID'
    assert exc.value.review_evidence['field']=='unrendered_details'
    assert exc.value.review_evidence['source_text']==context['model_input']['source_lines'][-1]['text']
    assert exc.value.review_evidence['evidence']==['不存在的提灯']


@pytest.mark.parametrize('case',['made_up_detail','missing_gap','nonline_detail'])
def test_visual_detail_evidence_cannot_bypass_source_or_classification(tmp_path,case):
    _,_,_,context,plan=sample(tmp_path)
    raw=review_for(context).model_dump();raw['schema_version']='spine_directing_review/1.2'
    for check in raw['checks']:check['unrendered_details']=[]
    raw['checks'][-1].update(coverage_complete=False,unperformed_actions=['翻找'])
    if case=='made_up_detail':raw['checks'][-1]['unrendered_details']=['不存在的提灯']
    if case=='missing_gap':plan['assessments'][-1]['classification']='performed'
    if case=='nonline_detail':raw['checks'][0]['unrendered_details']=['翻找']
    with pytest.raises((LifeValidationError,ProductionError)):
        validate_review(VisualCoverageReview.model_validate(raw),context,plan)


def test_preflight_and_semantic_review_share_one_repair_budget(env,tmp_path,monkeypatch):
    repo,store,source,old,request=seeded(env,tmp_path,monkeypatch)
    class DiagnosticModel(Model):
        saw_feedback=None
        def complete(self,system,payload,schema,timeout):
            first=self.calls==0
            if 'repair_feedback' in payload:self.saw_feedback=payload['repair_feedback']
            response=super().complete(system,payload,schema,timeout)
            raw=json.loads(response['content'])
            if first:
                raw['scenes'][0]['actors'][1]['x']=raw['scenes'][0]['actors'][0]['x']
                raw['assessments'][-1].update(classification='performed',cue_ids=[])
            elif 'required_checks' in payload and payload['execution'].get('preflight_passed') is False:
                # An independent diagnostic reveals semantic gaps as well as geometry.
                raw['checks'][-1].update(coverage_complete=False,unperformed_actions=[raw['checks'][-1]['quote']],passed=False)
            response['content']=json.dumps(raw,ensure_ascii=False)
            return response
    model=DiagnosticModel();jid=repo.create('alice',request);w=run_worker(repo,store,model)
    for _ in range(4):assert w.tick()
    job=repo.get(jid,'alice');assert job['status']=='succeeded'
    assert job['actual_calls']==model.calls==4
    assert '站位过近' in model.saw_feedback['details']
    assert model.saw_feedback['previous_review']['checks'][-1]['unperformed_actions']
    evidence=store.json(ArtifactRef.model_validate(next(a for a in job['artifacts'] if a['kind']=='SpineDirectingEvidence')))
    assert [(r['phase'],r['attempt']) for r in evidence['responses']]==[('plan',0),('diagnose',0),('plan',1),('review',1)]
    assert evidence['repairs']==1 and repo.get(source['id'],'alice')==source
