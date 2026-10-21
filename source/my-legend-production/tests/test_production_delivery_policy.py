from copy import deepcopy

import pytest

from my_legend_production.adapters import compact_instruction
from my_legend_production.core import weighted_length
from my_legend_production.production.story_guard import production_guidance
from my_legend_production.production.visual_contracts import VisualContext
from my_legend_production.production.visual_plan import action_slots, action_coverage_feedback
from test_production_visual_context import sample


def test_compact_instruction_preserves_original_values_and_repeated_emphasis():
    line = {'emotion': 'reassuring', 'tone_instruction': '先平稳，后半句放轻',
            'emphasis': [{'span_text':'别走','occurrence':2}], 'pauses':[{'position':2}],
            'speech_rate':0.93, 'pitch':'略低', 'volume':'轻声但清楚',
            'delivery_note':'收尾留一点气，不要叹气', 'text':'别走，请别走。'}
    before = deepcopy(line)
    result = compact_instruction(line, '默认提示')
    assert line == before
    for value in ['安抚','先平稳，后半句放轻','重音：别走（第2次）','衔接处短停',
                  '语速参考0.93倍','音高：略低','音量：轻声但清楚','收尾留一点气，不要叹气','默认提示']:
        assert value in result
    assert line['text'] not in result


def test_compaction_never_truncates_long_annotation_to_make_it_pass():
    note = '保留完整的表演要求。' * 100
    result = compact_instruction({'delivery_note': note})
    assert note == result
    assert weighted_length(result) > 240


def test_production_policy_is_scoped_and_never_promotes_untrusted_story_text():
    assert '原始创作要求' in production_guidance({'outputs':['script']})
    assert production_guidance({'outputs':[]}) == ''
    audio = production_guidance({'outputs':['audio'], 'source':{'idea':'INJECT_SENTINEL'}})
    visual = production_guidance({'outputs':['visual'],'visual_profile':'standing_performance/1'})
    assert '360' in audio and '600' in audio and 'kind=action' not in audio and 'INJECT_SENTINEL' not in audio
    assert 'kind=action' in visual and 'enter、exit、move、wait' in visual
    assert 'shake_head' not in visual


def test_visual_feedback_locates_missing_extra_duplicates_and_preserves_order():
    delivery, raw = sample()
    slots = action_slots(delivery)
    context = VisualContext.model_validate(raw)
    assert action_coverage_feedback(context, slots) == []
    original = context.scenes[0].actions
    first_id = original[0].line_id
    original[0].line_id = 'not-an-action'
    original.append(deepcopy(original[0]))
    issues = action_coverage_feedback(context, slots)
    assert issues[0]['missing'] == [first_id]
    assert issues[0]['extra'] == ['not-an-action','not-an-action']
    assert issues[0]['duplicates'] == ['not-an-action']
    assert slots[0]['line_ids'][0] == first_id
