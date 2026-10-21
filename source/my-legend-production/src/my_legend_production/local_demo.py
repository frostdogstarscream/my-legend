"""Bounded local integration using labelled synthetic signals, zero model calls."""
from __future__ import annotations

from copy import deepcopy
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import wave

from .adapters import AdapterService
from .alignment import align
from .core import AdapterError, PROJECT, json_bytes, key, parse_json, require, sha, validate
from .storage import ArtifactStore


def write(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    data=json_bytes(value)
    if path.exists():
        require(path.read_bytes()==data,'RUN_INPUT_CHANGED',str(path.name))
    else:path.write_bytes(data)


def signal(path, seconds, pitch, rate=24000):
    frames=round(seconds*rate)
    data=bytearray()
    for n in range(frames):
        envelope=min(1,n/(rate*.03),(frames-1-n)/(rate*.03))
        sample=round(2500*max(0,envelope)*math.sin(2*math.pi*pitch*n/rate))
        data.extend(struct.pack('<h',sample))
    if Path(path).exists():return
    with wave.open(str(path),'wb') as out:
        out.setparams((1,2,rate,frames,'NONE','not compressed'));out.writeframes(data)


def worker(kind, paths_file, input_file, root, config_file=None):
    args=[sys.executable,'-X','utf8','-m','my_legend_production.worker',kind,'--paths',str(paths_file),
          '--input',str(input_file),'--root',str(root),'--thread','local-integration','--local-only']
    if config_file:args+=['--config',str(config_file)]
    env={k:os.environ[k] for k in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP') if k in os.environ}
    env.update(PYTHONPATH=str(PROJECT/'src'),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',DART_SUPPRESS_ANALYTICS='true',LANGSMITH_TRACING='false')
    # No credentials forwarded into this local-only child process.
    result=subprocess.run(args,env=env,capture_output=True,timeout=120)
    if result.returncode:
        error=Path(root)/'host-error.json'
        raise AdapterError(parse_json(error.read_bytes())['code'] if error.exists() else 'WORKFLOW_PROCESS_FAILED',kind)
    return parse_json((Path(root)/'host-result.json').read_bytes())


def padded_mix(source, destination, duration_ms):
    import soundfile as sf
    import numpy as np
    samples,rate=sf.read(source,always_2d=True)
    target=max(len(samples),math.ceil(duration_ms*rate/1000))
    padded=np.pad(samples,((0,target-len(samples)),(0,0)))
    if not destination.exists():sf.write(destination,padded,rate,subtype=sf.info(source).subtype)
    actual,actual_rate=sf.read(destination,always_2d=True)
    require(actual_rate==rate and np.array_equal(actual,padded),'DERIVED_MEDIA_CHANGED')
    return {'source_sha256':sha(Path(source).read_bytes()),'derived_sha256':sha(destination.read_bytes()),
            'source_frames':len(samples),'derived_frames':len(padded),'sample_rate':rate,
            'operation':'append_silence_for_explicit_visual_tail','audio_content_unchanged':True}


def preview_page(target, visual_root, synthetic=True):
    source=(visual_root/'app/visual_workflow/preview.html').read_text(encoding='utf-8')
    left=source.index('function tick(now)');right=source.index('}catch(e)',left)
    replacement="""const audio=document.getElementById('joint-audio');
function tick(){position=audio.currentTime*1000;playing=!audio.paused&&!audio.ended;draw();requestAnimationFrame(tick)}
document.getElementById('play').onclick=()=>{if(audio.ended)audio.currentTime=0;audio.play().catch(()=>{})};
document.getElementById('pause').onclick=()=>audio.pause();
document.getElementById('replay').onclick=()=>{audio.currentTime=0;audio.play().catch(()=>{})};
seek.oninput=()=>{audio.currentTime=+seek.value/1000;position=+seek.value;draw()};requestAnimationFrame(tick);
"""
    source=source[:left]+replacement+source[right:]
    source=source.replace('await document.fonts.ready;',"document.getElementById('mode').textContent='本地联合预览 · 审阅状态见制作记录';await document.fonts.ready;",1)
    banner='<aside style="padding:14px;background:#fff0cf;color:#73421b">我的传说 · 本地联调：音轨是合成测试信号，不是真实配音。声音听审、视觉确认与发布均待完成。 <a href="scene.mp4">查看测试视频</a></aside><audio id="joint-audio" src="mix.wav" preload="metadata" controls style="width:100%"></audio>'
    if not synthetic:banner=banner.replace('音轨是合成测试信号，不是真实配音。','音轨使用本次登记的本地素材，本次未调用模型。')
    require(source.count('<h1 id="title">')==1,'RENDERER_VERSION_UNSUPPORTED')
    source=source.replace('<h1 id="title">',banner+'<h1 id="title">',1)
    source=source.replace('<title>视觉演绎草案</title>','<title>我的传说 · 本地声画联调</title>',1)
    (target/'index.html').write_text(source,encoding='utf-8')


def run(root, paths_file, export_video=False, inputs=None):
    import sqlite3
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    connection=sqlite3.connect(root/'orchestration.sqlite',timeout=30)
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS process_lock (singleton INTEGER PRIMARY KEY)')
        connection.commit();connection.execute('BEGIN IMMEDIATE')
        result=_run(root,paths_file,export_video,inputs)
        connection.commit();return result
    finally:connection.close()


def _run(root, paths_file, export_video=False, inputs=None):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    paths_file=Path(paths_file).resolve();paths=parse_json(paths_file.read_bytes())
    lock=paths_file.with_name('dependency-lock.json')
    require(lock.is_file(),'DEPENDENCY_LOCK_REQUIRED')
    for rel,digest in parse_json(lock.read_bytes())['files'].items():
        require(sha((paths_file.parent/rel).read_bytes())==digest,'DEPENDENCY_CHANGED',rel)
    store=ArtifactStore(inputs['store']) if inputs else ArtifactStore(root/'prepared-store',PROJECT/'examples')
    service=AdapterService(store);requests={}
    prepared={};targets={};maps={}
    for name in ('audio','visual'):
        request=parse_json(Path(inputs[name+'_request']).read_bytes()) if inputs else parse_json((PROJECT/'examples'/f'{name}.request.json').read_bytes())
        require(request['adapter']==name,'REQUEST_ADAPTER_MISMATCH')
        requests[name]=request
        result=service.prepare(request)
        require(result['state']=='prepared','LOCAL_PREPARE_FAILED',name)
        prepared[name]=result;targets[name]=store.json(result['target']);maps[name]=store.json(result['sidecar'])
        write(root/'prepared'/f'{name}.result.json',result)
        write(root/'prepared'/f'{name}.target.json',targets[name])
        write(root/'prepared'/f'{name}.sidecar.json',maps[name])
    config=parse_json((Path(paths['audio_root'])/'config.example.json').read_bytes())
    context=store.json(requests['audio']['context'])
    settings=context['render_settings'];config.update(sample_rate=int(settings['sample_rate']),channels=1 if settings['channels']=='mono' else 2,
        gap_ms=int(settings['gap_ms']),tail_ms=int(settings['tail_ms']),text_model={'enabled':False},tts={'adapter':'local'},sfx={'adapter':'local'},
        visual={'enabled':False,'delivery_mode':'audio'},call_budget={'max_calls':0,'batch_max_calls':0,'max_by_kind':{'speech':0,'sfx':0,'text':0}})
    config['instruction_policy']['max_weight']=context['instruction_max_weight']
    voice_bindings={(b['identity_kind'],b['source_identity_id']):b for b in context['voice_bindings']}
    config['role_voices']={r['target_role_id']:voice_bindings[(r['identity_kind'],r['source_identity_id'])]['voice'] for r in maps['audio']['roles']}
    local_script=deepcopy(targets['audio']);media=root/'audio-input/media';media.mkdir(parents=True,exist_ok=True)
    media_bindings=parse_json(Path(inputs['media']).read_bytes()) if inputs else None
    if inputs:
        require(isinstance(media_bindings,dict),'MEDIA_BINDINGS_INVALID')
        routes={key(c['source']):c for c in context['cue_routes']}
        for coverage in maps['audio']['coverage']:
            route=routes.get(key(coverage['source']))
            if route and route['media_ref']:
                for bid in coverage['target_block_ids']:
                    require(bid not in media_bindings or media_bindings[bid]==route['media_ref'],'MEDIA_BINDING_CONFLICT')
                    media_bindings[bid]=route['media_ref']
        require(set(media_bindings)=={e['block_id'] for e in local_script['events']},'MEDIA_BINDINGS_INCOMPLETE')
        for reference in media_bindings.values():validate(reference,'adapter.schema.json','Artifact','MEDIA_BINDINGS_INVALID')
    local_script['source']['media_origin']='provided_local_assets' if inputs else 'synthetic_test_signals'
    for index,event in enumerate(local_script['events']):
        path=media/(event['block_id']+'.wav')
        if media_bindings:
            data=store.read(media_bindings[event['block_id']])
            require(not path.exists() or path.read_bytes()==data,'RUN_INPUT_CHANGED')
            path.write_bytes(data)
        else:signal(path,0.6 if event['kind']=='sfx' else max(.8,len(event['text'])*.12),300+index*140)
        event['media']={'strategy':'local','file':'media/'+path.name}
    write(root/'audio-input/script.json',local_script);write(root/'audio-input/config.json',config)
    write(root/'audio-input/provenance.json',{'synthetic_only':not bool(inputs),'source_target':prepared['audio']['target'],
          'operation':'attach registered local media; preserve text, IDs and performance','model_calls':0,
          'media':{p.name:sha(p.read_bytes()) for p in media.glob('*.wav')}})
    print('Running existing audio LangGraph...',flush=True)
    a=worker('audio',paths_file,root/'audio-input/script.json',root/'audio-workflow',root/'audio-input/config.json')
    require(a['status']=='awaiting_review' and a['technical_passed'],'AUDIO_WORKFLOW_NOT_READY')
    require(a['call_budget'].get('production_calls')==0,'LOCAL_MODE_CALLED_PROVIDER')
    print('Running existing visual LangGraph and Dart compiler...',flush=True)
    v=worker('visual',paths_file,root/'prepared/visual.target.json',root/'visual-workflow')
    require(v['status'] in ('awaiting_context','awaiting_review','succeeded'),'VISUAL_WORKFLOW_NOT_READY')
    plan=parse_json((root/'visual-workflow/compiled-plan.json').read_bytes())
    joined=align(plan,a['timeline'],maps['audio'],maps['visual'],targets['audio'])
    target=root/'preview';target.mkdir(exist_ok=True)
    (target/'preview-data.json').write_bytes(json_bytes(joined))
    assets=Path(paths['visual_root'])/'pixel-story-mobile/packages/story_player/assets'
    for rel,digest in joined['asset_bindings']['file_hashes'].items():
        asset=(assets/rel).resolve()
        require(asset.is_relative_to(assets.resolve()) and sha(asset.read_bytes())==digest,'VISUAL_ASSET_CHANGED')
    shutil.copytree(assets,target/'assets',dirs_exist_ok=True)
    draft=root/'audio-workflow'/a['preview_directory']/'draft.wav'
    derived=padded_mix(draft,target/'mix.wav',joined['manifest']['duration_ms'])
    preview_page(target,Path(paths['visual_root']),synthetic=not bool(inputs))
    source_delivery=store.json(requests['audio']['source']['delivery'])
    fixture_declared=any(isinstance(trace,dict) and trace.get('fixture_only') is True for trace in source_delivery.get('model_trace',[]))
    report={'schema_version':'my-legend.local-integration/0.1','status':'local_preview_ready',
        'source_version_id':joined['manifest']['source_version_id'],'plan_hash':joined['plan_hash'],
        'joint_profile':'my-legend.joint/0.3','duration_ms':joined['manifest']['duration_ms'],
        'audio_status':a['status'],'visual_status':v['status'],'technical_audio_passed':a['technical_passed'],
        'synthetic_media':None if inputs else True,'media_origin':'provided_local_assets' if inputs else 'synthetic_test_signals','model_calls':0,'human_listening':'pending','human_visual_approval':v['human_visual_approval'],
        'visual_workflow_review_status':v['workflow_review_status'],
        'source_fixture_declared':fixture_declared,
        'publication':'not_approved','preview':'preview/index.html','alignment':joined['alignment'],
        'audio_derivation':derived,'video':None}
    if export_video:
        from .render import render_video
        require(paths.get('ffmpeg'),'FFMPEG_REQUIRED')
        report['video']=render_video(joined,target/'mix.wav',target/'scene.mp4',assets,paths['ffmpeg'],synthetic_audio=None if inputs else True)
    (root/'integration-report.json').write_bytes(json_bytes(report))
    return report
