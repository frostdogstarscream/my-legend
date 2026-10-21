"""Whole audio child + native caches and mixer. Synthetic providers; network denied."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
import time

import pytest

from my_legend_production.production.audio_arrangement import AudioArrangement, arrangement_bytes


@pytest.mark.parametrize('control_source_kind',[None,'action','sfx'])
def test_arranged_child_runs_marker_only_scene_and_reuses_assets_on_remake(tmp_path,monkeypatch,control_source_kind):
    sf=pytest.importorskip('soundfile');np=pytest.importorskip('numpy')
    workspace=Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(workspace/'runtime/my-legend-production/dependencies/audio/pipeline/src'))
    from lingban_pipeline.adapters import QianwenAudioTTS,QianwenSFX
    from my_legend_production.production.audio_job import run
    def denied(*a,**kw):raise AssertionError('No network allowed in native integration test')
    monkeypatch.setattr(socket.socket,'connect',denied);monkeypatch.setattr(socket.socket,'connect_ex',denied)
    calls=[]
    def generate(self,payload,path):
        self.budget();calls.append(payload)
        n=9600 if payload['media'].get('candidate_version',1)!=1 else 4800
        path.parent.mkdir(parents=True,exist_ok=True);sf.write(path,.1*np.sin(np.arange(n)*.12),48000)
        return {'fixture_only':True}
    monkeypatch.setattr(QianwenAudioTTS,'generate',generate);monkeypatch.setattr(QianwenSFX,'generate',generate)
    examples=workspace/'source/my-legend-production/examples/source'
    delivery=json.loads((examples/'story_delivery.json').read_bytes())
    manifest=json.loads((examples/'render_manifest.json').read_bytes())
    scene=delivery['episodes'][0]['scenes'][0]
    action=scene['utterances'].pop()
    if control_source_kind:
        action.update(kind=control_source_kind,text='雨声渐渐变小。')
    new_scene={**deepcopy(scene),'scene_id':'marker-scene','scene_index':2,'utterances':[action]}
    delivery['episodes'][0]['scenes'].append(new_scene)
    task=next(t for t in manifest['tasks'] if t['line_id']==action['line_id'])
    task['scene_id']='marker-scene'
    task.update(source_kind=action['kind'],text=action['text'])
    task['task_id']=sha256(f"{delivery['story_version_id']}:{task['episode_id']}:marker-scene:{action['line_id']}".encode()).hexdigest()
    delivery_path=tmp_path/'delivery.json';delivery_path.write_text(json.dumps(delivery,ensure_ascii=False),encoding='utf-8')
    manifest_path=tmp_path/'manifest.json';manifest_path.write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    events=[];previous=None
    for ep in delivery['episodes']:
        for sc in ep['scenes']:
            for u in sc['utterances']:
                ref=dict(episode_id=ep['episode_id'],scene_id=sc['scene_id'],line_id=u['line_id'])
                mode=('change_background' if control_source_kind and u['line_id']==action['line_id'] else
                      'background' if u['kind']=='sfx' else 'action_note' if u['kind']=='action' else 'speech')
                event=dict(source=ref,kind=u['kind'],text=u['text'],identity_id=u.get('speaker_role_id') or u.get('narrator_id'),
                    mode=mode,evidence_quote=u['text'],reason='Synthetic integration arrangement',
                    start=dict(event=previous,point='end' if previous else 'start'))
                events.append(event)
                if mode=='change_background':
                    event.update(start=dict(event=previous,point='start'),transition_end=dict(event=previous,point='end'),
                                 control_target=events[0]['source'],target_gain_db=-12.0)
                if mode=='speech':previous=ref
    events[0]['end']=dict(event=previous,point='end')
    plan=AudioArrangement(schema_version='audio_arrangement/1.1' if control_source_kind else 'audio_arrangement/1.0',
        source_version_id=delivery['story_version_id'],delivery_sha256=sha256(delivery_path.read_bytes()).hexdigest(),events=events)
    plan_path=tmp_path/'plan.json';plan_path.write_bytes(arrangement_bytes(plan))
    request=dict(source_version_id=plan.source_version_id,delivery_path=str(delivery_path),manifest_path=str(manifest_path),
        delivery_sha256=plan.delivery_sha256,manifest_sha256=sha256(manifest_path.read_bytes()).hexdigest(),
        arrangement_path=str(plan_path),arrangement_sha256=sha256(plan_path.read_bytes()).hexdigest(),instruction_limits={'soft':360,'hard':600},
        voices={e.identity_id:'longanyang_v3.1' for e in plan.events if e.mode=='speech'})
    paths=workspace/'runtime/my-legend-production/workflows.json'
    def execute(name):
        root=tmp_path/name;root.mkdir();(root/'control.json').write_text(json.dumps({'deadline_epoch':time.time()+120}))
        run(root,paths,request)
        return root,json.loads((root/'result.json').read_bytes())
    old,first=execute('old')
    assert len(calls)==4 and first['provider_calls']==4 and first['unsettled_tasks']==0
    assert first['total_frames']==14400 # 3 speech clips; background overlaps, action has no hold.
    script=json.loads((old/'scenes/000/script.json').read_bytes())
    assert script['events'][0]['kind']=='ambience'
    marker=json.loads((old/'scenes/001/script.json').read_bytes())['events'][0]
    assert marker['kind']=='action' and marker['duration_ms']==0
    assert not (old/'input/holds').exists()
    assert not (old/'scenes/001/audio-workflow').exists()
    timeline=json.loads((old/'output/timeline.json').read_bytes())
    assert timeline['schema_version']==('measured_timeline/1.2' if control_source_kind else 'measured_timeline/1.1')
    assert timeline['events'][0]['end_frame']==14400
    assert timeline['events'][-1]['start_frame']==(9600 if control_source_kind else 14400)
    if control_source_kind:
        assert timeline['events'][-1]['transition_end_frame']==14400
        assert timeline['events'][-1]['kind']==control_source_kind
    assert timeline['events'][-1]['source_audio_sha256'] is None
    original=(old/'output/mix.wav').read_bytes()
    run(old,paths,request) # Replay must use native completed requests and identical compiled bytes.
    assert len(calls)==4 and (old/'output/mix.wav').read_bytes()==original
    selected=next(e.source.model_dump() for e in plan.events if e.mode=='speech')
    request.update(audio_revision={'cache':{'sha256':first['raw_cache_sha256']},'regenerate':[selected]},
        audio_cache_path=str(old/'output/raw-cache.zip'),revision_nonce='intentional-remake')
    new,second=execute('new')
    assert len(calls)==5 and second['provider_calls']==1 and second['total_frames']==19200
    assert (old/'output/mix.wav').read_bytes()==original
    new_timeline=json.loads((new/'output/timeline.json').read_bytes())
    assert new_timeline['events'][0]['end_frame']==19200
    assert first['arrangement_sha256']==second['arrangement_sha256']
    assert first['audio_sha256']!=second['audio_sha256']
