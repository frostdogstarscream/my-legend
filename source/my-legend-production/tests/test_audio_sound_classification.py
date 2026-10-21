"""Validate semantic-review contracts; fixtures do not prove model classification accuracy."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from my_legend_production.production.audio_readiness import AudioDraftAudit, audio_audit_issues, bound_audio_audit_schema
from test_audio_readiness import sample, review, run_policy, IDEA


@pytest.mark.parametrize('classification,text,expected', [
    ('non_acoustic', '墨羽拍打衣服的动作停下。', 'NON_ACOUSTIC_SFX'),
])
def test_classified_bad_sound_cannot_pass_with_empty_problem_list(classification, text, expected):
    draft = sample(True)
    draft['scenes'][0]['lines'][2]['text'] = text
    raw = review(draft)
    raw['lines'][2]['sound'] = {'classification': classification, 'audible_quote': '',
                              'reason': '没有独立可听声源过程'}
    assert raw['lines'][2]['problems'] == []
    assert any(expected in x for x in audio_audit_issues(draft, AudioDraftAudit.model_validate(raw), IDEA))


@pytest.mark.parametrize('classification,text,quote', [
    ('audible_event', '脚步逐渐放慢，最后一步落在木地板上。', '最后一步落在木地板上'),
    ('ambience', '沉默片刻，窗外仍有雨点敲打玻璃。', '雨点敲打玻璃'),
    ('control_only', '墨羽的脚步声停下。', ''),
])
def test_audible_progression_and_ambience_are_not_keyword_blocked(classification, text, quote):
    draft = sample(True)
    draft['scenes'][0]['lines'][2]['text'] = text
    raw = review(draft)
    raw['lines'][2]['sound'] = {'classification': classification, 'audible_quote': quote, 'reason': '存在可听过程'}
    assert audio_audit_issues(draft, AudioDraftAudit.model_validate(raw), IDEA) == []


@pytest.mark.parametrize('defect', ['missing', 'null_sfx', 'sound_on_action', 'invented_quote', 'empty_quote', 'control_with_quote'])
def test_missing_or_unbound_sound_evidence_is_invalid_review(defect):
    draft = sample(True)
    raw = review(draft)
    sound = raw['lines'][2]['sound']
    if defect == 'missing': del raw['lines'][2]['sound']
    if defect == 'null_sfx': raw['lines'][2]['sound'] = None
    if defect == 'sound_on_action': raw['lines'][0]['sound'] = deepcopy(sound)
    if defect == 'invented_quote': sound['audible_quote'] = '汽车鸣笛'
    if defect == 'empty_quote': sound['audible_quote'] = ''
    if defect == 'control_with_quote': sound['classification'] = 'control_only'
    with pytest.raises(ValidationError):
        bound_audio_audit_schema(draft, IDEA).model_validate(raw)


def test_classification_defect_repaired_as_unspoken_action_and_reaudited():
    bad = sample(True)
    bad['scenes'][0]['lines'][2]['text'] = '她停止拍打衣服。'
    first = review(bad)
    first['lines'][2]['sound'] = {'classification': 'non_acoustic', 'audible_quote': '', 'reason': '纯动作说明'}
    good = deepcopy(bad)
    good['scenes'][0]['lines'][2]['kind'] = 'action'
    execute, records, calls, original = run_policy([first, {'replacements': [{'scene_index': 1, 'line_index': 3, 'original_text': bad['scenes'][0]['lines'][2]['text'], 'line': good['scenes'][0]['lines'][2]}], 'additions': []}, review(good)], draft=bad)
    result = execute()
    assert result.scenes[0].lines[2].kind == 'action'
    assert result.scenes[0].lines[2].text == original.scenes[0].lines[2].text
    assert records[0]['sound_checks'][0]['classification'] == 'non_acoustic'
    assert records[1]['sound_checks'] == []
    assert 'NON_ACOUSTIC_SFX' in calls[1][1]['audit_issues'][0]
    assert [c[0] for c in calls].count('production_draft_repair') == 1
    assert calls[0][1]['required_line_checks'][2]['source_kind'] == 'sfx'
    assert calls[-1][1]['required_line_checks'][2]['source_kind'] == 'action'
    assert len(calls[-1][1]['required_line_checks']) == len(good['scenes'][0]['lines'])


def test_reaudit_cannot_merge_historical_and_current_classifications():
    draft = sample(True)
    draft['scenes'][0]['lines'][2].update(kind='action', text='墨羽的动作突然停住。')
    raw = review(draft)
    stale = deepcopy(raw['lines'][2])
    stale.update(problems=['NON_ACOUSTIC_SFX'], sound={
        'classification': 'non_acoustic', 'audible_quote': '', 'reason': '旧版本错误分类'})
    raw['lines'].insert(2, stale)
    schema = bound_audio_audit_schema(draft, IDEA)
    spec = schema.model_json_schema()['properties']['lines']
    assert spec['minItems'] == spec['maxItems'] == 4
    with pytest.raises(ValidationError) as error:
        schema.model_validate(raw)
    assert error.value.errors()[0]['type'] == 'too_long'
    assert error.value.errors()[0]['loc'] == ('lines',)


def test_duplicate_misclassified_sound_removal_refreshes_current_review_slots():
    bad = sample(True)
    bad['scenes'][0]['lines'][2]['text'] = '墨羽的动作突然停住。'
    bad['scenes'][0]['lines'].insert(3, {'kind': 'action', 'text': '墨羽的动作突然停住。'})
    first = review(bad)
    first['lines'][2]['sound'] = {'classification': 'non_acoustic', 'audible_quote': '', 'reason': '动作已另有独立行'}
    good = deepcopy(bad)
    del good['scenes'][0]['lines'][2]
    execute, records, calls, original = run_policy([first, {'replacements': [], 'additions': [], 'remove_duplicates': [{'scene_index': 1, 'line_index': 3, 'keep_line_index': 4, 'original_text': bad['scenes'][0]['lines'][2]['text']}]}, review(good)], draft=bad)
    assert execute().scenes[0].lines[2].kind == 'action'
    assert len(calls[0][1]['required_line_checks']) == 5
    assert len(calls[-1][1]['required_line_checks']) == 4
    assert len(calls[-1][1]['original_draft']['scenes'][0]['lines']) == 5
