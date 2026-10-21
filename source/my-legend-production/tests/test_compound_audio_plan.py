from copy import deepcopy
import json

import pytest

from my_legend_production.production.audio_plan import AudioPlanner
from my_legend_production.production.compound_audio import CompoundArrangement
from test_audio_plan import Context
from test_compound_audio import compound_fixture


def simple_compound_plan(payload):
    rows = []; previous = None
    for n, slot in enumerate(payload['required_events']):
        mode = 'speech' if slot['kind'] in ('dialogue','narration') else 'action_note' if slot['kind']=='action' else 'one_shot'
        oid = f'op{n}'
        rows.append(dict(**slot, operations=[dict(operation_id=oid, mode=mode, cue_text=slot['text'], reason='isolated fixture',
            start=dict(operation_id=previous,point='end' if previous else 'start'))]))
        previous = oid
    return CompoundArrangement(source_version_id=payload['source_version_id'],delivery_sha256=payload['delivery_sha256'],events=rows).model_dump()


class CompoundModel:
    def __init__(self, fault=None, fixture=True):
        self.calls = []; self.fault = fault; self.fixture = fixture

    def complete(self, system, payload, schema, timeout):
        self.calls.append((system, deepcopy(payload), schema))
        if 'arrangement' not in payload:
            data = CompoundArrangement.model_validate(compound_fixture()[0]).model_dump() if self.fixture else simple_compound_plan(payload)
            if self.fault == 'plan_schema': data['events'].append(deepcopy(data['events'][0]))
        else:
            data = dict(schema_version='audio_arrangement_review/2.0',source_version_id=payload['source_version_id'],
                delivery_sha256=payload['delivery_sha256'],arrangement_sha256=payload['arrangement_sha256'],passed=True,
                checks=[dict(source=e['source'],text=e['text'],passed=True,issues=[],advisories=[],
                    operation_checks=[dict(operation_id=o['operation_id'],cue_text=o['cue_text'],passed=True,issues=[],advisories=[])
                                      for o in e['operations']]) for e in payload['arrangement']['events']])
            if self.fault == 'missing_operation': data['checks'][2]['operation_checks'].pop()
            if self.fault == 'wrong_cue': data['checks'][0]['operation_checks'][0]['cue_text']='伪造声音'
            if self.fault == 'semantic':
                data['passed']=False;data['checks'][2].update(passed=False,issues=['遗漏独立的滴水声'])
            if self.fault == 'failed_operation':
                data['checks'][2]['operation_checks'][0].update(passed=False,issues=['方向错误'])
        return dict(content=json.dumps(data,ensure_ascii=False),usage={'total_tokens':1},requested_model='fixture')


@pytest.mark.parametrize('fault,calls,success',[ (None,2,True),('plan_schema',3,False),
    ('missing_operation',4,False),('wrong_cue',4,False),('semantic',6,False),('failed_operation',4,False)])
def test_compound_planning_review_and_replay_are_bounded(tmp_path,fault,calls,success):
    ctx=Context(tmp_path);_,raw,_=compound_fixture();ref=ctx.store.put('StoryDelivery','fixture',raw)
    model=CompoundModel(fault);planner=AudioPlanner(model,compound=True)
    result=planner(ctx,ref)
    assert (result.status=='succeeded')==success
    assert len(model.calls)==ctx.units==calls
    assert planner(ctx,ref)==result and len(model.calls)==calls
    if success:
        refs={a.kind:a for a in result.artifacts}
        plan=ctx.store.json(refs['AudioArrangement']);review=ctx.store.json(refs['AudioArrangementReview'])
        assert len(plan['events'])==6 and len(plan['events'][2]['operations'])==2
        assert review['arrangement_sha256']==refs['AudioArrangement'].sha256
        assert model.calls[0][2]['properties']['schema_version']['const']=='audio_arrangement/2.0'
        assert model.calls[1][2]['properties']['checks']['maxItems']==6
    elif fault in ('missing_operation','wrong_cue','failed_operation'):
        assert all('arrangement' in p for _,p,_ in model.calls[1:])
