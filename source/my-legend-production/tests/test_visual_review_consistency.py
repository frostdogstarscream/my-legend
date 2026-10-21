"""Regression cases for contradictory reviews and unnecessary replanning."""
from copy import deepcopy
import json
import pytest
from pydantic import ValidationError

from my_legend_production.production.visual_plan import SemanticReview, validate_review
from my_legend_production.production.visual_contracts import VisualContext
from my_legend_production.production.repository import ProductionError
from test_production_visual_context import sample
from test_production_visual_plan import Model, setup
from test_production_queue import env


def review_fixture():
    delivery, context=sample()
    model=Model(context)
    from my_legend_production.production.visual_plan import required_checks
    raw=json.loads(model.complete('',{'required_checks':required_checks(VisualContext.model_validate(context))},{},1)['content'])
    for check in raw['checks']:
        check.update(blocking_issues=[],advisories=[])
    return delivery,context,raw


def test_advisory_does_not_block_an_executable_source_faithful_plan():
    delivery,context,raw=review_fixture()
    raw['checks'][0]['advisories']=['可以加入更丰富的目送表情，但原剧本没有要求。']
    review=SemanticReview.model_validate(raw)
    assert validate_review(review,VisualContext.model_validate(context),delivery).passed


def test_review_cannot_reject_without_an_explicit_blocking_finding():
    _,_,raw=review_fixture()
    raw.update(passed=False)
    raw['checks'][0].update(passed=False,reason='原文没有要求面向对象，方向选择成立。')
    with pytest.raises(ValidationError,match='REVIEW_VERDICT_CONTRADICTS_FINDINGS'):
        SemanticReview.model_validate(raw)


def test_review_cannot_pass_while_reporting_missing_required_action():
    _,_,raw=review_fixture()
    raw['checks'][0]['blocking_issues']=[{'code':'SOURCE_ACTION_OMITTED','source_requirement':'甲走进院子。',
        'actual_result':'没有入场动作', 'evidence':raw['checks'][0]['evidence']}]
    with pytest.raises(ValidationError,match='REVIEW_VERDICT_CONTRADICTS_FINDINGS'):
        SemanticReview.model_validate(raw)


def test_blocking_finding_cannot_cite_another_story():
    delivery,context,raw=review_fixture()
    check=raw['checks'][0]
    check.update(passed=False,blocking_issues=[{'code':'SOURCE_FACT_CHANGED','source_requirement':'甲修理车轮。',
        'actual_result':'原地等待','evidence':[{**check['evidence'][0],'quote':'甲修理车轮。'}]}])
    raw['passed']=False
    with pytest.raises(ProductionError,match='VISUAL_REVIEW_EVIDENCE_INVALID'):
        validate_review(SemanticReview.model_validate(raw),VisualContext.model_validate(context),delivery)


def test_malformed_review_retries_review_without_changing_valid_plan(env):
    _,context=sample()
    class ContradictoryReviewer(Model):
        def complete(self,system,payload,schema,timeout):
            result=super().complete(system,payload,schema,timeout)
            if 'required_checks' in payload and len(self.calls)==2:
                raw=json.loads(result['content'])
                raw['passed']=False
                raw['checks'][0]['passed']=False
                raw['checks'][0]['blocking_issues']=[]
                result['content']=json.dumps(raw)
            return result
    model=ContradictoryReviewer(context)
    job,ctx,handler,_=setup(env,model)
    outcome=handler(ctx)
    assert outcome.status=='succeeded'
    ctx.repository.finish(ctx.stage,outcome)
    assert len(model.calls)==3
    assert sum('required_checks' not in call for call in model.calls)==1
    assert model.calls[1]['context']==model.calls[2]['context']
    assert model.calls[2]['previous_review_attempt']['error_code']=='VISUAL_MODEL_SCHEMA_INVALID'
    assert ctx.repository.execution_evidence(job,'alice')['policy_repairs']==1


def test_uncertain_visual_submission_is_never_a_schema_repair(env):
    _,context=sample()
    class UncertainModel(Model):
        def complete(self,system,payload,schema,timeout):
            self.calls.append(payload)
            raise ProductionError('VISUAL_MODEL_RESPONSE_UNCERTAIN')
    model=UncertainModel(context)
    _,ctx,handler,_=setup(env,model,repairs=2)
    # StageContext persists the ambiguous submission and exposes its canonical code.
    with pytest.raises(ProductionError,match='EXTERNAL_RESULT_UNKNOWN'):
        handler(ctx)
    assert len(model.calls)==1
