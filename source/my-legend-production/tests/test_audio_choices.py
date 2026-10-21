from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from my_legend_production.production.audio_choices import (RestrictedAudioPlanner, choice_model, slots_for,
    compile_choices, validate_relative_ends, repair_slots)
from my_legend_production.production.compound_audio import CompoundArrangement, resolve_compound_frames, compound_bytes
from my_legend_production.production.audio_arrangement import ArrangementTimingError
from my_legend_production.production.audio_recompile import assert_timing_only
from test_audio_plan import Context
from test_compound_audio import compound_fixture
from my_legend_production.production.audio_review_choices import compile_review


def passing_review(payload):
    return {'content':json.dumps({'checks':{s['slot_id']:{'issues':[],'advisories':[]}
                                          for s in payload['review_slots']}})}


def decisions(slots):
    out = {}; previous = None
    for s in slots:
        start = dict(operation_id=previous, point='end' if previous else 'start', offset_ms=0)
        if s['kind'] in ('dialogue', 'narration'):
            out[s['slot_id']] = dict(start=start)
            previous = s['speech_id']
        else:
            oid = 'sound_' + s['slot_id']
            out[s['slot_id']] = {'operations': [dict(operation_id=oid,
                mode='action_note' if s['kind']=='action' else 'one_shot', cue_text=s['text'], reason='fixture', start=start)]}
            previous = oid
    return out


class ChoicesModel:
    def __init__(self, break_speech=False):
        self.calls = []; self.break_speech = break_speech
    def complete(self, system, payload, schema, timeout):
        self.calls.append(deepcopy(payload))
        if 'arrangement' in payload:
            return passing_review(payload)
        out = decisions(payload['required_events'])
        value = {k:out[k] for k in payload['editable_slots']}
        if self.break_speech and len(self.calls)==1:
            key = next(s['slot_id'] for s in payload['required_events'] if s['kind']=='dialogue')
            value[key]['operations'] = [{'mode':'stop_sound'}]
        return dict(content=json.dumps({'choices':value}), usage={'total_tokens':1})


def test_fixed_source_speech_and_local_patch_replay(tmp_path):
    _,raw,_ = compound_fixture();ctx=Context(tmp_path);ref=ctx.store.put('StoryDelivery','fixture',raw)
    model=ChoicesModel(break_speech=True);planner=RestrictedAudioPlanner(model)
    result=planner(ctx,ref)
    assert result.status=='succeeded'
    assert len(model.calls)==3
    assert len(model.calls[1]['editable_slots'])==1
    plan=ctx.store.json(next(a for a in result.artifacts if a.kind=='AudioArrangement'))
    original=slots_for(json.loads(raw))
    assert [e['text'] for e in plan['events']]==[e['text'] for e in original]
    for s,e in zip(original,plan['events'],strict=True):
        if s['kind'] in ('dialogue','narration'):
            assert len(e['operations'])==1 and e['operations'][0]['operation_id']==s['speech_id']
    assert planner(ctx,ref)==result and len(model.calls)==3


def test_original_failed_plans_are_rejected_and_real_frames_explain_failure():
    f=json.loads((Path(__file__).parent/'fixtures/stability-20261019.json').read_bytes())
    plan=CompoundArrangement.model_validate(f['sa01']['plan'])
    with pytest.raises(ValueError,match='BACKGROUND_ABSOLUTE_END'):validate_relative_ends(plan)
    with pytest.raises(ArrangementTimingError) as failure:resolve_compound_frames(plan,f['sa01']['operation_frames'])
    assert failure.value.details['start_frame']/48000==91.64
    assert failure.value.details['end_frame']/48000==55
    for raw in f['sa03']:
        with pytest.raises(ValidationError):CompoundArrangement.model_validate(raw)


def test_measured_repair_is_timing_only_reaudited_and_does_not_regenerate(tmp_path):
    f=json.loads((Path(__file__).parent/'fixtures/stability-20261019.json').read_bytes())['sa01']
    plan=CompoundArrangement.model_validate(f['plan']);ctx=Context(tmp_path)
    op=plan.events[-1].operations[0]
    class Model:
        calls=0
        def complete(self,system,payload,schema,timeout):
            self.calls+=1
            if 'measured' in payload and 'failure' in payload:
                return {'content':json.dumps({'changes':[dict(operation_id=op.operation_id,start=op.start.model_dump(),
                    end=dict(operation_id=op.start.operation_id,point='end',offset_ms=3000),fade_out_ms=800)]})}
            return passing_review(payload)
    model=Model(); measured=dict(arrangement_sha256=sha256(compound_bytes(plan)).hexdigest(),operation_frames=f['operation_frames'],sample_rate=48000)
    revised,evidence=RestrictedAudioPlanner(model).repair_timing(ctx,plan,measured,{'error_code':'ARRANGEMENT_EMPTY_AUDIO_INTERVAL'},0)
    assert revised is not None and model.calls==2 and evidence['review']['passed']
    assert_timing_only(plan,revised)
    assert resolve_compound_frames(revised,f['operation_frames'])['bgLaugh','end']/48000==94.64
    changed=revised.model_dump();changed['events'][-1]['operations'][0]['gain_db']=-12
    with pytest.raises(Exception,match='AUDIO_TIMING_REPAIR_CONTENT_CHANGED'):
        assert_timing_only(plan,CompoundArrangement.model_validate(changed))


def test_speech_schema_cannot_accept_stop_operation_or_source_fields():
    _,raw,_=compound_fixture();slots=slots_for(json.loads(raw));slot=next(s for s in slots if s['kind']=='dialogue')
    schema=choice_model(slots,[slot['slot_id']])
    for extra in ({'operations':[]},{'identity_id':'invented'},{'text':'changed'}):
        with pytest.raises(ValidationError):schema.model_validate({'choices':{slot['slot_id']:{'start':{'operation_id':None},**extra}}})


def test_nonpositive_background_same_anchor_rejected_before_generating_materials():
    f=json.loads((Path(__file__).parent/'fixtures/stability-20261019.json').read_bytes())['sa01']['plan']
    for offset in (0,-1000):
        raw=deepcopy(f);op=raw['events'][-1]['operations'][0]
        op['end']=dict(op['start'],offset_ms=op['start']['offset_ms']+offset)
        with pytest.raises(ValueError,match='NONPOSITIVE_ANCHOR_INTERVAL'):
            validate_relative_ends(CompoundArrangement.model_validate(raw))


def test_review_cannot_demand_extra_sound_on_speech_and_retries_only_review(tmp_path):
    _,raw,_=compound_fixture();ctx=Context(tmp_path);ref=ctx.store.put('StoryDelivery','fixture',raw)
    class Model(ChoicesModel):
        def complete(self,system,payload,schema,timeout):
            response=super().complete(system,payload,schema,timeout)
            if 'review_slots' in payload and len(self.calls)==2:
                checks=json.loads(response['content'])['checks']
                row=next(s for s in payload['review_slots'] if s['kind']=='dialogue')
                checks[row['slot_id']]['issues']=[dict(rule='missing_sound_component',evidence=row['text'],
                    reason='invalid reviewer request to add an effect under speech',operation_ids=[])]
                return {'content':json.dumps({'checks':checks})}
            return response
    model=Model();result=RestrictedAudioPlanner(model)(ctx,ref)
    assert result.status=='succeeded' and len(model.calls)==3
    assert 'arrangement' in model.calls[1] and 'arrangement' in model.calls[2]
    assert model.calls[1]['arrangement']==model.calls[2]['arrangement']


def test_review_requires_source_evidence_and_preserves_real_missing_sound_rejection():
    from my_legend_production.production.audio_review_choices import review_slots
    _,raw,_=compound_fixture();slots=slots_for(json.loads(raw));plan=compile_choices(slots,decisions(slots),'fixture',sha256(raw).hexdigest())
    data=json.loads(passing_review({'review_slots':review_slots(plan)})['content'])
    slot=next(s for s in review_slots(plan) if s['kind']=='sfx')
    issue=dict(rule='missing_sound_component',evidence=slot['text'],reason='required component is absent',operation_ids=[])
    data['checks'][slot['slot_id']]['issues']=[issue]
    review=compile_review(plan,json.dumps(data))
    assert review.passed is False and len(review.checks)==len(plan.events)
    issue['evidence']='a fabricated quote not in the original source'
    with pytest.raises(ValueError,match='EVIDENCE_NOT_IN_SOURCE'):compile_review(plan,json.dumps(data))


def test_returned_schema_receives_instance_feedback_and_can_be_repaired(tmp_path):
    _,raw,_=compound_fixture();ctx=Context(tmp_path);ref=ctx.store.put('StoryDelivery','fixture',raw)
    class Model(ChoicesModel):
        def complete(self,system,payload,schema,timeout):
            response=super().complete(system,payload,schema,timeout)
            return {'content':json.dumps(schema)} if len(self.calls)==1 else response
    model=Model();result=RestrictedAudioPlanner(model)(ctx,ref)
    assert result.status=='succeeded' and len(model.calls)==3
    assert 'AUDIO_INSTANCE_REQUIRED_NOT_SCHEMA' in model.calls[1]['feedback']['issues'][0]['message']


def test_actual_compound_errors_target_source_rows_not_flattened_operation_indices():
    f=json.loads((Path(__file__).parent/'fixtures/stability-20261020-operation-locations.json').read_bytes())
    with pytest.raises(ValidationError) as original:
        compile_choices(f['slots'],f['choices'],f['source_version_id'],f['delivery_sha256'])
    assert repair_slots(original.value,f['slots'],f['choices'])==['line_0011','line_0016']
    schema=choice_model(f['slots'],[s['slot_id'] for s in f['slots']])
    with pytest.raises(ValidationError) as early:
        schema.model_validate({'choices':f['choices']})
    assert repair_slots(early.value,f['slots'],f['choices'])==['line_0011','line_0016']
    assert all(e['loc'][0]=='choices' for e in early.value.errors())


def test_malformed_choice_values_do_not_break_failure_localization():
    _,raw,_=compound_fixture();slots=slots_for(json.loads(raw));values=decisions(slots)
    target=slots[0]['slot_id'];values[target]=['not','an','object']
    schema=choice_model(slots,[s['slot_id'] for s in slots])
    with pytest.raises(ValidationError) as error:schema.model_validate({'choices':values})
    assert repair_slots(error.value,slots,values)==[target]


def test_actual_missing_references_report_exact_rows_ids_and_valid_targets():
    from my_legend_production.production.audio_choices import ChoiceConstraintError, safe_issues
    fixture=json.loads((Path(__file__).parent/'fixtures/stability-20261020-pragmatic-references.json').read_bytes())
    with pytest.raises(ChoiceConstraintError) as failure:
        compile_choices(fixture['slots'],fixture['choices'],fixture['source_version_id'],fixture['delivery_sha256'])
    issues=safe_issues(failure.value)
    assert issues and all(i['referenced_operation_id'] not in i['available_operation_ids'] for i in issues)
    assert repair_slots(failure.value,fixture['slots'],fixture['choices'])==sorted({i['path'][1] for i in issues})
    # A correction to only the diagnosed references should remove this failure,
    # without renaming operations or rewriting source text.
    choices=deepcopy(fixture['choices'])
    for issue in issues:
        _,slot,_,index,field=issue['path']
        op=choices[slot]['operations'][index]
        assert field in ('start','end','transition_end')
        op[field]['operation_id']=next(oid for oid in issue['available_operation_ids'] if oid.startswith('speech_'))
    try:
        compile_choices(fixture['slots'],choices,fixture['source_version_id'],fixture['delivery_sha256'])
    except (ValidationError,ValueError) as other:
        assert not isinstance(other,ChoiceConstraintError)


@pytest.mark.parametrize('end', [dict(operation_id=None,point='start',offset_ms=0),
                               dict(operation_id='speech1',point='end',offset_ms=-1)])
def test_invalid_transition_ends_fail_before_native_generation(end):
    raw,_,_=compound_fixture();plan=CompoundArrangement.model_validate(raw)
    plan.events[2].operations[0].transition_end=type(plan.events[2].operations[0].start).model_validate(end)
    with pytest.raises(ValueError,match='COMPOUND_TRANSITION_'):
        validate_relative_ends(plan)
