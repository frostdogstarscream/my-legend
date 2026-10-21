"""Real audio + source-preserving, reviewable storyboard batches. No automatic approval."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import time

from .adapters import AdapterService
from .alignment import align
from .core import json_bytes, key, locator, require, sha
from .storage import ArtifactStore
from .worker import save

VOICES = [
    {'id':'longanyang_v3.1','name':'龙安洋 · 阳光男声'},
    {'id':'longwan_v3.1','name':'龙婉 · 细腻女声'},
    {'id':'longsanshu_v3.1','name':'龙三叔 · 沉稳男声'},
    {'id':'anmingyuan_v3.1','name':'安明远 · 清亮男声'},
    {'id':'wenhuaiqing_v3.1','name':'温怀清 · 柔和女声'},
    {'id':'longyuan_v3.1','name':'龙媛 · 温暖女声'},
]
DEFAULT_VOICES = {'legend-moyu':'longanyang_v3.1','legend-lingmo':'longwan_v3.1','narrator':'longsanshu_v3.1'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def update(root, **values):
    file = Path(root)/'status.json'
    state = read(file) if file.exists() else {}
    state.update(values, updatedAt=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
    save(file,state)
    return state


def validate_voices(delivery, voices):
    required = {u['speaker_role_id'] if u['kind']=='dialogue' else u['narrator_id']
                for ep in delivery['episodes'] for sc in ep['scenes'] for u in sc['utterances'] if u['kind'] in ('dialogue','narration')}
    require(set(voices)==required and set(voices.values()) <= {v['id'] for v in VOICES}, 'VOICE_BINDING_REQUIRED')
    return required


def prepare_audio(root, delivery, manifest, voices, paths, *, instruction_limits=None, arrangement=None):
    """Validate all source mappings before any paid audio submission."""
    import numpy as np
    import soundfile as sf
    from .core import weighted_length
    limits = instruction_limits or {'soft': 240, 'hard': 240}
    require(set(limits) == {'soft', 'hard'} and all(isinstance(v, int) and not isinstance(v, bool) for v in limits.values())
            and 0 < limits['soft'] <= limits['hard'], 'INSTRUCTION_LIMITS_INVALID')
    length_warnings = []
    sound_decisions = {}
    compound = arrangement is not None and arrangement.schema_version=='audio_arrangement/2.0'
    if arrangement is not None:
        from .production.audio_arrangement import validate_arrangement_source, source_key
        validate_arrangement_source(arrangement, delivery)
        sound_decisions = {source_key(e.source): e.operations[0] if compound else e for e in arrangement.events}
    validate_voices(delivery, voices)
    store = ArtifactStore(Path(root)/'store')
    source = {'story_id':delivery['story_id'],'story_version_id':delivery['story_version_id'],
              'delivery':store.put_json(delivery),'render_manifest':store.put_json(manifest)}
    caps = {'contract_version':'my-legend.capabilities/1.0','profile_id':'qianwen-storyboard','revision':'1',
            'fixture_only':False,'provider_account_verified':False,'verification_ref':None,'controls':['instructions'],
            'exact_controls':[],'locked_performance_enrichment_validation':False,
            'visual':{'max_scenes':2,'max_events':100,'max_duration_ms':90000,'max_characters_basic':6,'max_scene_cast_basic':4,'max_characters_painted':3}}
    cap_ref = store.put_json(caps)
    rows=[]
    for ep in delivery['episodes']:
        for sc in ep['scenes']:
            rows.extend((ep,sc,u) for u in sc['utterances'])
    bindings={}
    for ep,sc,u in rows:
        if u['kind'] not in ('dialogue','narration'): continue
        kind = 'role' if u['kind']=='dialogue' else 'narrator'
        identity = u['speaker_role_id'] if kind=='role' else u['narrator_id']
        bindings[(kind,identity)]={'identity_kind':kind,'source_identity_id':identity,'performer_id':u['performer_id'],
            'provider_profile_id':'qianwen-ai-audio','model':'qwen-audio-3.1-tts-flash','voice':voices[identity],
            'voice_asset_id':voices[identity],'voice_asset_revision':'documented-20261019','verification':'configured','required_controls':['instructions']}
    routes=[];holds={}
    for ep,sc,u in rows:
        if u['kind'] in ('dialogue','narration'): continue
        require(u['kind'] in ('sfx','action'),'MUSIC_MEDIA_REQUIRED')
        loc=locator(ep['episode_id'],sc['scene_id'],u)
        ref=None
        if u['kind']=='action' and arrangement is None:
            # Explicit silent storyboard hold; never pretend this is performed animation or AI SFX.
            path=Path(root)/'input/holds'/(sha(json_bytes(loc))+'.wav');path.parent.mkdir(parents=True,exist_ok=True)
            sf.write(path,np.zeros(28800,dtype=np.float32),24000,subtype='PCM_16')
            ref=store.register(path);holds[key(loc)]=ref
        decision = sound_decisions.get((ep['episode_id'],sc['scene_id'],u['line_id']))
        route = 'audio_marker' if decision and u['kind']=='action' else 'ambience' if decision and decision.mode=='background' else 'sfx'
        if decision and (decision.mode in ('continue_background','change_background') or
                         decision.mode=='stop_sound' and arrangement.schema_version in ('audio_arrangement/1.1','audio_arrangement/2.0')):
            route='sound_control'
        routes.append({'source':loc,'route':route,'description':u['text'],'media_ref':ref,
                       'sound_requirements':{'origin':'source','presentation':'planned_silent_action_card' if ref else 'isolated_sfx_no_speech_no_music'},'timing_relations':[]})
    ctx={'contract_version':'my-legend.audio-context/1.0','profile_id':'real-audio','profile_revision':'1','performance_mode':'preserve',
         'missing_performance_policy':'block','default_instruction':'','voice_bindings':list(bindings.values()),'cue_routes':routes,
         'instruction_max_weight':limits['hard'],'required_exact_controls':[],'render_settings':{'sample_rate':48000,'channels':'mono','gap_ms':0,'tail_ms':0},'capability_profile':cap_ref}
    ctx_ref=store.put_json(ctx);source_ref=source
    batches=[];service=AdapterService(store)
    for n,(ep,sc) in enumerate((ep,sc) for ep in delivery['episodes'] for sc in ep['scenes']):
        scope={'scenes':[{'episode_id':ep['episode_id'],'scene_id':sc['scene_id']}]}
        local_ctx=deepcopy(ctx);local_ctx['cue_routes']=[r for r in routes if r['source']['scene_id']==sc['scene_id'] and r['source']['episode_id']==ep['episode_id']]
        request={'contract_version':'my-legend.adapter-request/1.0','adapter':'audio','request_id':f'audio-{n}',
                 'idempotency_key':f'audio-{n}','mode':'prepare_only','source':source_ref,'scope':scope,'context':store.put_json(local_ctx),'joint_profile':'independent'}
        result=service.prepare(request)
        require(result['state']=='prepared','AUDIO_PREPARE_FAILED:'+','.join(i['code'] for i in result['issues']))
        script=store.json(result['target']);mapping=store.json(result['sidecar'])
        operation_map=None
        if compound:
            from .production.compound_audio_prepare import expand_compound_script
            script,operation_map=expand_compound_script(script,mapping,arrangement)
        for event in script['events']:
            if event['kind'] == 'speech':
                weight = weighted_length(event['performance']['instructions'])
                if weight > limits['soft']:
                    length_warnings.append({'source': event['source_ref'], 'weight': weight,
                        'soft_limit': limits['soft'], 'hard_limit': limits['hard'], 'action': 'continue_without_rewrite'})
        directory=Path(root)/f'scenes/{n:03d}';directory.mkdir(parents=True,exist_ok=True)
        for cov in mapping['coverage']:
            ref=holds.get(key(cov['source']))
            if ref:
                bid=cov['target_block_ids'][0];path=directory/'media'/(bid+'.wav');path.parent.mkdir(exist_ok=True);path.write_bytes(store.read(ref))
                next(e for e in script['events'] if e['block_id']==bid)['media']={'strategy':'local','file':'media/'+path.name}
        script['source']['media_origin']='real_model_audio_for_arrangement' if arrangement else 'real_model_audio_with_planned_silent_action_cards'
        config=read(Path(paths['audio_root'])/'config.live.deepseek-qwen-audio-3.1.json')
        count_speech=sum(e['kind']=='speech' for e in script['events']);count_sfx=sum(e['kind'] in ('sfx','ambience') and not e['media'].get('file') for e in script['events'])
        config.update(gap_ms=0,tail_ms=0,context_window=0,text_model={'enabled':False},
                      role_voices={r['target_role_id']:voices[r['source_identity_id']] for r in mapping['roles']},voice_catalog=[v['id'] for v in VOICES],
                      call_budget={'max_calls':count_speech+count_sfx,'batch_max_calls':count_speech+count_sfx,'max_by_kind':{'text':0,'speech':count_speech,'sfx':count_sfx},'batch_max_by_kind':{'text':0}})
        config['tts'].update(concurrency=1,timeout_seconds=90)
        config['instruction_policy'] = {**config.get('instruction_policy', {}), 'max_weight': limits['hard']}
        config['sfx'].update(concurrency=1,timeout_seconds=120)
        save(directory/'script.json',script);save(directory/'config.json',config)
        batch={'index':n,'scope':scope,'source':source,'scene':sc,'episode_id':ep['episode_id'],'audio_map':mapping,'audio_script':script,'path':str(directory)}
        if operation_map is not None:
            batch['operation_map']=operation_map
            save(directory/'operation-map.json',{'schema_version':'audio_operation_map/1.0',
                 'source_version_id':delivery['story_version_id'],'operations':operation_map,
                 'original_adapter_sidecar':result['sidecar'],
                 'note':'Original adapter sidecar describes pre-expansion templates; use operations for generated blocks.'})
        batches.append(batch)
    save(Path(root)/'input/audio-context.json',ctx)
    save(Path(root)/'input/instruction-length-assessment.json', {'limits': limits,
         'basis': 'local_production_policy_not_provider_limit', 'warnings': length_warnings})
    save(Path(root)/'input/production-plan.json',{'source':source,'voices':voices,'scene_count':len(batches),'source_events':len(rows),
         'presentation':'arranged_audio' if arrangement else 'basic_storyboard_action_cards','human_context_review':'pending',
         'planned_action_hold_ms':0 if arrangement else 1200,
         'limitations':['音频编排不执行视觉动作；真实听感待人工审核。'] if arrangement else
             ['动作逐字显示为分镜卡，不声称执行修灯等精细动画。','基础像素外观为制作选择，人物及成片效果待人工审核。']})
    return store,cap_ref,batches


def run_audio(directory, paths_file):
    from types import SimpleNamespace
    from .worker import audio_run
    args=SimpleNamespace(root=str(Path(directory)/'audio-workflow'),input=str(Path(directory)/'script.json'),config=str(Path(directory)/'config.json'),thread='production',local_only=False)
    audio_run(args,read(paths_file))
    result=read(Path(args.root)/'host-result.json')
    if result['status']=='awaiting_review' and not result['technical_passed']:
        # A resampler can overshoot full scale even for a valid PCM input. Apply
        # one explicit processing revision to just those assets; never re-generate.
        from lingban_pipeline.graph import Pipeline
        from lingban_pipeline.media import read as read_media
        import numpy as np
        import math
        pipe=Pipeline(Path(args.root))
        try:
            state=pipe.graph.get_state(pipe.invocation(args.thread)).values
            failures=read(Path(args.root)/result['preview_directory']/'technical_checks.json')['failures']
            tasks={t['task_id']:t for t in state['tasks']};patches=[];evidence=[]
            for tid,failure in failures.items():
                raw=pipe.registry.get(failure['raw_task_id'])
                require(failure['error_code']=='ValueError' and raw and raw['status']=='success','AUDIO_REQUIRES_MANUAL_RECOVERY')
                data,_=read_media(Path(args.root)/raw['result']['file'],48000,1)
                peak=float(np.max(np.abs(data)))
                require(peak>1 and peak<1.3,'AUDIO_REQUIRES_MANUAL_RECOVERY')
                task=tasks[tid];gain=20*math.log10(.98/peak)
                media=deepcopy(next(e['media'] for e in state['script']['events'] if e['block_id']==task['block_id']))
                media['processing']={**media.get('processing',{}),'gain_db':gain}
                patches.append({'block_id':task['block_id'],'media':media})
                evidence.append({'block_id':task['block_id'],'raw_sha256':raw['result']['sha256'],'resampled_peak':peak,'gain_db':gain})
            require(bool(patches),'AUDIO_REQUIRES_MANUAL_RECOVERY')
            before=pipe.status(args.thread)['call_budget']['production_calls']
            pipe.resume(args.thread,{'decision':'changes','bundle_sha256':state['preview']['bundle']['bundle_sha256'],
               'reviewer':'automation:audio-processing','scope':[p['block_id'] for p in patches],
               'notes':[{'kind':'technical_processing','description':'Resampling overshoot correction; raw audio and source text preserved; human listening still pending.'}],
               'changes':{'events':patches}})
            require(pipe.status(args.thread)['call_budget']['production_calls']==before,'PROCESSING_REPAIR_SUBMITTED_MODEL')
            save(Path(directory)/'technical-processing-revision.json',{'corrections':evidence,'additional_model_calls':0,'human_listening':'pending'})
        finally:pipe.close()
        audio_run(args,read(paths_file));result=read(Path(args.root)/'host-result.json')
    require(result['status']=='awaiting_review' and result['technical_passed'],'AUDIO_GENERATION_OR_CHECK_FAILED')
    return result


def run(root, paths_file, request):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    delivery=read(request['delivery_path']);manifest=read(request['manifest_path']);voices=request['voices'];paths=read(paths_file)
    dependency_root=Path(paths_file).parent
    for relative,digest in read(dependency_root/'dependency-lock.json')['files'].items():
        require(sha((dependency_root/relative).read_bytes())==digest,'DEPENDENCY_CHANGED')
    binding={'delivery':sha(json_bytes(delivery)),'manifest':sha(json_bytes(manifest)),'voices':voices,'profile':'basic_storyboard_action_cards/1'}
    frozen=root/'input-binding.json'
    if frozen.exists(): require(read(frozen)==binding,'RUN_INPUT_CHANGED')
    else: save(frozen,binding)
    update(root,id=request['id'],storyId=request['story_id'],storyVersionId=delivery['story_version_id'],status='preparing',progress=3,
           message='正在校验制品、绑定音色与场景',audioReview='pending',visualReview='pending',publication='not_approved',voices=voices,errorCode=None,errorType=None)
    store,cap_ref,batches=prepare_audio(root,delivery,manifest,voices,paths)
    audio=[]
    for n,batch in enumerate(batches):
        update(root,status='audio_generating',progress=8+round(n/len(batches)*45),message=f'真实声音制作：第{n+1}/{len(batches)}场，逐句缓存并测量')
        audio.append(run_audio(batch['path'],paths_file))
    save(root/'audio-results.json',audio)
    update(root,status='visual_preparing',progress=58,message='配音和实测时间轴已生成，正在准备分镜')
    if request.get('audio_only'):
        update(root,status='audio_ready',progress=58,message='真实配音与实测时间轴已生成，等待分镜制作')
        return
    from .storyboard import produce_storyboard
    media=produce_storyboard(root,paths_file,delivery,store,cap_ref,batches,audio)
    update(root,status='awaiting_review',progress=100,message='真实声音与基础分镜预览已制作，请试听并审核',media=media,
           audioReview='pending',visualReview='pending',publication='not_approved')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--paths',required=True);parser.add_argument('--request',required=True)
    args=parser.parse_args()
    root=Path(args.root);root.mkdir(parents=True,exist_ok=True)
    lock=(root/'production.lock').open('a+b');lock.seek(0)
    import msvcrt
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        lock.close();print('This production job is already running.',flush=True);return
    try: run(args.root,args.paths,read(args.request))
    except Exception as error:
        issue=getattr(error,'issue',{})
        update(args.root,status='failed',message='制作停止，已保留成功素材；请核对错误后恢复，避免重复计费',errorCode=issue.get('code','PRODUCTION_FAILED'),errorType=type(error).__name__)
        print('Production stopped: '+type(error).__name__,flush=True)
        raise SystemExit(2) from None
    finally:lock.close()


if __name__=='__main__': main()
