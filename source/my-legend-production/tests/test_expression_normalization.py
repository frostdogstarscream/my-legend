from copy import deepcopy
from my_legend_production.production.directing_compile import trim_redundant_expressions


def cue(name,start,end,preset='open'):
    return dict(id=name,instance_id='actor',channel='eyes',preset=preset,start_frame=start,end_frame=end,enabled=True)


def test_identical_holds_keep_source_ids_and_visible_state_at_every_frame():
    values=[cue('first',100,500),cue('next',103,700)]
    original=deepcopy(values);execution=deepcopy(values)
    changes=trim_redundant_expressions(values,execution)
    assert changes and values[0]['end_frame']==103 and execution[0]['end_frame']==103
    assert [v['id'] for v in values]==['first','next']
    for frame in range(800):
        assert {v['preset'] for v in values if v['start_frame']<=frame<v['end_frame']}=={v['preset'] for v in original if v['start_frame']<=frame<v['end_frame']}
    assert trim_redundant_expressions(values,execution)==[]


def test_conflicting_equal_start_or_nested_cues_are_not_silently_repaired():
    for values in ([cue('a',100,500),cue('b',103,700,'closed')],
                   [cue('a',100,500),cue('b',100,700)],
                   [cue('a',100,700),cue('b',103,500)]):
        original=deepcopy(values)
        assert trim_redundant_expressions(values,deepcopy(values))==[]
        assert values==original
