"""Compound native generation/cache path using isolated synthetic providers only."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
import time

import pytest

from my_legend_production.production.compound_audio import CompoundArrangement,compound_bytes


def test_compound_child_and_shared_sound_library_avoid_repeated_sound_calls(tmp_path,monkeypatch):
    sf=pytest.importorskip('soundfile');np=pytest.importorskip('numpy')
    workspace=Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(workspace/'runtime/my-legend-production/dependencies/audio/pipeline/src'))
    from lingban_pipeline.adapters import QianwenAudioTTS,QianwenSFX
    from my_legend_production.production.audio_job import run
    def deny(*args,**kwargs):raise AssertionError('Network is forbidden in this integration fixture')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket.socket,'connect_ex',deny)
    calls=[]
    def generate(self,payload,destination):
        self.budget();calls.append(('speech' if 'text' in payload else 'sfx',deepcopy(payload)))
        count=9600 if payload['media'].get('candidate_version',1)>1 else 4800
        destination.parent.mkdir(parents=True,exist_ok=True)
        sf.write(destination,.1*np.sin(np.arange(count)*.12),48000)
        # The isolated library publication branch is exercised; this is never a real provider result.
        return {'model':'isolated-test-provider'}
    monkeypatch.setattr(QianwenAudioTTS,'generate',generate);monkeypatch.setattr(QianwenSFX,'generate',generate)
    examples=workspace/'source/my-legend-production/examples/source'
    delivery=json.loads((examples/'story_delivery.json').read_bytes());manifest=json.loads((examples/'render_manifest.json').read_bytes())
    first=delivery['episodes'][0]['scenes'][0]['utterances'][0];first['text']='雨声持续，钟声响起。'
    next(t for t in manifest['tasks'] if t['line_id']==first['line_id'])['text']=first['text']
    delivery_path=tmp_path/'delivery.json';delivery_path.write_text(json.dumps(delivery,ensure_ascii=False),encoding='utf-8')
    manifest_path=tmp_path/'manifest.json';manifest_path.write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    events=[];previous=None;voices={}
    for ep in delivery['episodes']:
        for sc in ep['scenes']:
            for n,u in enumerate(sc['utterances']):
                mode='speech' if u['kind'] in ('dialogue','narration') else 'action_note' if u['kind']=='action' else 'background'
                oid=f'op{n}';start=dict(operation_id=previous,point='end' if previous else 'start')
                op=dict(operation_id=oid,mode=mode,cue_text=u['text'],reason='isolated fixture',start=start)
                identity=u.get('speaker_role_id') or u.get('narrator_id')
                if mode=='speech':previous=oid;voices[identity]='longanyang_v3.1'
                operations=[op]
                if mode=='background':
                    op.update(cue_text='雨声持续')
                    operations.append(dict(operation_id='chime',mode='one_shot',cue_text='钟声响起',reason='isolated fixture',start=start))
                events.append(dict(source=dict(episode_id=ep['episode_id'],scene_id=sc['scene_id'],line_id=u['line_id']),
                    kind=u['kind'],text=u['text'],identity_id=identity,operations=operations))
    events[0]['operations'][0]['end']=dict(operation_id=previous,point='end')
    plan=CompoundArrangement(source_version_id=delivery['story_version_id'],delivery_sha256=sha256(delivery_path.read_bytes()).hexdigest(),events=events)
    plan_path=tmp_path/'plan.json';plan_path.write_bytes(compound_bytes(plan))
    request=dict(source_version_id=plan.source_version_id,delivery_path=str(delivery_path),manifest_path=str(manifest_path),
        delivery_sha256=plan.delivery_sha256,manifest_sha256=sha256(manifest_path.read_bytes()).hexdigest(),
        arrangement_path=str(plan_path),arrangement_sha256=sha256(plan_path.read_bytes()).hexdigest(),voices=voices,
        instruction_limits={'soft':360,'hard':600},sound_library={'root':str(tmp_path/'shared-library'),'scope':'fixture-owner','allow_unreviewed':True})
    paths=workspace/'runtime/my-legend-production/workflows.json'
    def execute(name):
        root=tmp_path/name;root.mkdir();(root/'control.json').write_text(json.dumps({'deadline_epoch':time.time()+120}))
        run(root,paths,request)
        return root,json.loads((root/'result.json').read_bytes())
    old,first_result=execute('original')
    assert len(calls)==5 and first_result['provider_calls']==5
    timeline=json.loads((old/'output/timeline.json').read_bytes())
    assert timeline['schema_version']=='measured_timeline/2.0' and len(timeline['events'][0]['operations'])==2
    assert timeline['total_frames']==14400
    raw=(old/'output/mix.wav').read_bytes()
    run(old,paths,request)
    assert len(calls)==5 and (old/'output/mix.wav').read_bytes()==raw
    # A separate job without per-work cache still reuses both SFX; speech is generated normally.
    cross,cross_result=execute('cross-work')
    assert len(calls)==8 and cross_result['provider_calls']==3
    report=json.loads((cross/'output/compile-report.json').read_bytes())
    assert sum(x['rendered']['generation']['origin']=='sound_library' for x in report['asset_provenance'])==2
    # Intentional operation remake imports all raw work assets, replaces just chime and keeps original bytes.
    selected=dict(events[0]['source'],operation_id='chime')
    request.update(audio_revision={'cache':{'sha256':cross_result['raw_cache_sha256']},'regenerate':[selected]},
                   audio_cache_path=str(cross/'output/raw-cache.zip'),revision_nonce='remake-chime')
    revised,revised_result=execute('revision')
    assert len(calls)==9 and revised_result['provider_calls']==1
    assert calls[-1][0]=='sfx' and calls[-1][1]['media']['candidate_version']>1
    revised_timeline=json.loads((revised/'output/timeline.json').read_bytes())
    assert revised_timeline['events'][0]['operations'][1]['end_frame']==9600
    assert revised_timeline['events'][0]['operations'][0]['source_audio_sha256']==timeline['events'][0]['operations'][0]['source_audio_sha256']
    assert (old/'output/mix.wav').read_bytes()==raw
    # A measured-time failure happens after native generation. Recompile-only must
    # reuse those exact assets and must not even enter the provider path.
    request.pop('audio_revision'); request.pop('audio_cache_path'); request.pop('revision_nonce')
    broken=plan.model_dump();broken['events'][0]['operations'][0]['start']={'operation_id':previous,'point':'end','offset_ms':0}
    broken['events'][0]['operations'][0]['end']={'operation_id':None,'point':'start','offset_ms':10}
    broken_plan=CompoundArrangement.model_validate(broken)
    broken_path=tmp_path/'broken-plan.json';broken_path.write_bytes(compound_bytes(broken_plan))
    request.update(arrangement_path=str(broken_path),arrangement_sha256=sha256(broken_path.read_bytes()).hexdigest())
    failed=tmp_path/'timing-failure';failed.mkdir();(failed/'control.json').write_text(json.dumps({'deadline_epoch':time.time()+120}))
    with pytest.raises(ValueError,match='EMPTY_AUDIO_INTERVAL'):run(failed,paths,request)
    assert (failed/'native-complete.json').is_file()
    submitted=len(calls)
    fixed=deepcopy(broken);fixed['events'][0]['operations'][0]['end']={'operation_id':previous,'point':'end','offset_ms':200}
    fixed_path=tmp_path/'fixed-plan.json';fixed_path.write_bytes(compound_bytes(CompoundArrangement.model_validate(fixed)))
    request.update(arrangement_path=str(fixed_path),arrangement_sha256=sha256(fixed_path.read_bytes()).hexdigest(),timing_only=True)
    monkeypatch.setattr(QianwenAudioTTS,'generate',deny);monkeypatch.setattr(QianwenSFX,'generate',deny)
    run(failed,paths,request)
    assert len(calls)==submitted and json.loads((failed/'result.json').read_bytes())['timing_only_recompile']
    rebuilt=(failed/'output/mix.wav').read_bytes()
    run(failed,paths,request)
    assert (failed/'output/mix.wav').read_bytes()==rebuilt
