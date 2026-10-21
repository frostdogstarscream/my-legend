"""Relax acoustic fidelity, without relaxing source/semantic/timing gates."""
import json

import pytest
from pydantic import ValidationError

from my_legend_production.production.audio_choices import RestrictedAudioPlanner
from my_legend_production.production.audio_review_choices import compile_review, review_slots
from my_legend_production.production.audio_sfx_policy import SFX_POLICY
from my_legend_production.production.compound_audio import CompoundArrangement
from my_legend_production.production.compound_audio_plan import review_operations
from test_audio_choices import ChoicesModel, passing_review
from test_audio_plan import Context
from test_compound_audio import compound_fixture


def review_with_issue(plan, index, rule, ids, reason='保留主要声音，简化精细声学表现'):
    data = json.loads(passing_review({'review_slots': review_slots(plan)})['content'])
    data['checks'][f'line_{index+1:04}']['issues'] = [dict(
        rule=rule, evidence=plan.events[index].text, reason=reason, operation_ids=ids)]
    return data


@pytest.mark.parametrize('text', [
    '脚步声由慢变快。', '敲门声时紧时缓，重复三次。',
    '汽车从远处驶近，又逐渐远去。', '屋内只剩下安稳的呼吸声和灯火的微响。',
])
def test_representative_material_allows_documented_acoustic_simplification(text):
    raw, _, _ = compound_fixture()
    event = raw['events'][4]
    event['text'] = text
    # A real representative sound operation is present; source text stays intact.
    event['operations'][-1]['cue_text'] = text
    event['operations'] = [event['operations'][-1]]
    event['operations'][0]['start'] = dict(operation_id='speech2', point='end', offset_ms=0)
    # Natural background endings avoid references to removed stop operations.
    raw['events'][0]['operations'][0]['end'] = dict(operation_id='speech2', point='end', offset_ms=0)
    raw['events'][2]['operations'][1]['end'] = dict(operation_id='speech2', point='end', offset_ms=0)
    plan = CompoundArrangement.model_validate(raw)
    response = review_with_issue(plan, 4, 'acoustic_detail_simplified', ['bird'])
    review = compile_review(plan, json.dumps(response))
    review_operations(review, plan)
    assert review.passed and review.checks[4].operation_checks[0].passed
    assert 'pragmatic_sfx/1' in review.checks[4].advisories[0]
    assert review.checks[4].text == text


@pytest.mark.parametrize('rule', [
    'missing_sound_component', 'wrong_sound_meaning', 'control_meaning_mismatch',
    'timing_contradiction', 'unsupported_acoustic_change',
])
def test_real_missing_or_wrong_sound_and_timing_are_still_blocking(rule):
    raw, _, _ = compound_fixture()
    plan = CompoundArrangement.model_validate(raw)
    data = review_with_issue(plan, 2, rule, ['fade_rain'], '降低雨声音量不能冒充新出现的滴水声')
    # A permitted simplification must not cancel a blocking issue on the same row.
    data['checks']['line_0003']['issues'].append(dict(
        rule='acoustic_detail_simplified', evidence=plan.events[2].text,
        reason='不要求精确淡出曲线', operation_ids=['fade_rain']))
    review = compile_review(plan, json.dumps(data))
    assert not review.passed and not review.checks[2].passed
    assert len(review.checks[2].issues) == 1 and len(review.checks[2].advisories) == 1
    with pytest.raises(ValueError, match='SEMANTIC_REVIEW_FAILED'):
        review_operations(review, plan)


@pytest.mark.parametrize('ids', [[], ['stop_rain']])
def test_simplification_cannot_claim_retained_sound_without_material(ids):
    raw, _, _ = compound_fixture()
    plan = CompoundArrangement.model_validate(raw)
    response = review_with_issue(plan, 4, 'acoustic_detail_simplified', ids)
    with pytest.raises(ValueError, match='SIMPLIFICATION_REQUIRES_RETAINED_SOUND'):
        compile_review(plan, json.dumps(response))


def test_speech_cannot_be_downgraded_to_sound_simplification():
    raw, _, _ = compound_fixture()
    plan = CompoundArrangement.model_validate(raw)
    response = review_with_issue(plan, 1, 'acoustic_detail_simplified', ['speech1'])
    with pytest.raises(ValidationError):
        compile_review(plan, json.dumps(response))


def test_policy_reaches_both_models_and_is_saved_with_evidence(tmp_path):
    _, raw, _ = compound_fixture()
    ctx = Context(tmp_path)
    ref = ctx.store.put('StoryDelivery', 'fixture', raw)
    model = ChoicesModel()
    result = RestrictedAudioPlanner(model)(ctx, ref)
    assert result.status == 'succeeded'
    assert len(model.calls) == 2
    assert all(call['sound_design_policy'] == SFX_POLICY for call in model.calls)
    evidence = ctx.store.json(next(a for a in result.artifacts if a.kind == 'AudioArrangementEvidence'))
    assert evidence['sound_design_policy'] == SFX_POLICY
    assert evidence['interface'] == 'audio_choices/1.3'
    assert evidence['review_interface'] == 'audio_review_choices/1.1'
