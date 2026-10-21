from copy import deepcopy

import pytest

from my_legend_production.alignment import align
from my_legend_production.core import AdapterError


@pytest.fixture
def inputs(prepared_pair):
    store,results=prepared_pair
    script=store.json(results['audio']['target']);visual=store.json(results['visual']['target'])
    maps={k:store.json(r['sidecar']) for k,r in results.items()}
    source=visual['script']['scenes'][0]
    blocks=[];clock=0
    for b in source['blocks']:
        end=clock+(500 if b['type']=='action' else 1000)
        blocks.append({'block':deepcopy(b),'start_ms':clock,'end_ms':end});clock=end
    plan={'plan_hash':'compiled-fixture','document':visual['script'],
          'manifest':{'source_version_id':script['source_version_id'],'duration_ms':clock,
                      'scenes':[{'scene_id':source['scene_id'],'start_ms':0,'end_ms':clock,'blocks':blocks}]},
          'frames':[{'time_ms':t,'scene_id':source['scene_id'],'background':'indoor','actors':[],
                     'subtitle':'fixture','speaker_id':None,'block_id':blocks[0]['block']['block_id']} for t in range(0,clock+1,100)]}
    placements=[];clock=0
    for event in script['events']:
        end=clock+(500 if event['kind']=='sfx' else 1000)
        placements.append({f:event[f] for f in ('block_id','kind','role_id','scene_id','text')}|
                          {'start_frame':clock,'end_frame':end,'duration_basis':'measured'})
        clock=end
    timeline={'source_version_id':script['source_version_id'],'sample_rate':1000,'total_frames':clock,'placements':placements}
    return plan,timeline,maps['audio'],maps['visual'],script


def test_join_narration_audio_only_and_visual_only(inputs):
    out=align(*inputs)
    assert out['alignment']['profile']=='my-legend.joint/0.3'
    assert out['manifest']['duration_ms']==4000
    assert len(out['alignment']['audio_only_layers'])==1
    nar=out['alignment']['mapping'][0]
    assert nar['source_kind']=='narration' and nar['audio_role_id'] and nar['visual_role_id'] is None
    assert out['alignment']['mapping'][-1]['duration_basis']=='visual_planned'
    assert out['alignment']['silence_tail_ms']==500
    assert inputs[0]['plan_hash']=='compiled-fixture'


@pytest.mark.parametrize('change,code',[
 (lambda p,a,am,vm,s:a.update(source_version_id='other'),'SOURCE_VERSION_MISMATCH'),
 (lambda p,a,am,vm,s:a.update(sample_rate=0),'INVALID_AUDIO_TIMELINE'),
 (lambda p,a,am,vm,s:a.update(sample_rate=True),'INVALID_AUDIO_TIMELINE'),
 (lambda p,a,am,vm,s:a['placements'][1].update(role_id='swapped-narrator'),'EVENT_CONTENT_MISMATCH'),
 (lambda p,a,am,vm,s:a['placements'][0].update(text='tampered'),'EVENT_CONTENT_MISMATCH'),
 (lambda p,a,am,vm,s:a['placements'][0].update(duration_basis='planned'),'AUDIO_NOT_MEASURED'),
 (lambda p,a,am,vm,s:a['placements'].append(deepcopy(a['placements'][0])),'DUPLICATE_AUDIO_EVENT'),
 (lambda p,a,am,vm,s:a['placements'].pop(),'EVENT_SET_MISMATCH'),
 (lambda p,a,am,vm,s:a['placements'][2].update(start_frame=1499),'VISUAL_GAP_INSUFFICIENT'),
 (lambda p,a,am,vm,s:a['placements'][0].update(start_frame=-1),'AUDIO_TIMING_CONFLICT'),
 (lambda p,a,am,vm,s:a.update(total_frames=90001),'DURATION_UNSUPPORTED'),
 (lambda p,a,am,vm,s:vm['coverage'].reverse(),'SOURCE_ORDER_MISMATCH'),
 (lambda p,a,am,vm,s:vm['coverage'][0].update(text_sha256='0'*64),'SOURCE_CONTENT_MISMATCH'),
 (lambda p,a,am,vm,s:am['coverage'][0].update(disposition='blocked'),'ADAPTER_NOT_PREPARED'),
 (lambda p,a,am,vm,s:vm['coverage'][0].update(target_block_ids=['a','b']),'MULTI_MAPPING_UNSUPPORTED'),
 (lambda p,a,am,vm,s:p['manifest']['scenes'][0]['blocks'][-1]['block'].pop('duration_ms'),'ACTION_DURATION_REQUIRED'),
 (lambda p,a,am,vm,s:p.update(frames=[]),'VISUAL_FRAMES_MISSING'),
])
def test_joint_refuses_invalid_and_mismatched_inputs(inputs,change,code):
    data=deepcopy(inputs);change(*data)
    with pytest.raises(AdapterError,match=code):align(*data)


def test_parallel_audio_only_cue_is_preserved(inputs):
    inputs[1]['placements'][0]['end_frame']=1000
    out=align(*inputs)
    assert out['alignment']['audio_only_layers'][0]['end_ms']==1000
    assert out['alignment']['mapping'][0]['start_ms']==500


def test_no_audio_identity_is_mutated(inputs):
    old=deepcopy(inputs[1]);align(*inputs)
    assert inputs[1]==old
