"""Batch the original visual compiler, retain every source event, render explicit action cards."""
from copy import deepcopy
from bisect import bisect_right
import math
from pathlib import Path
from types import SimpleNamespace

from .adapters import AdapterService
from .alignment import align
from .core import encoded, json_bytes, key, require, sha
from .worker import save, visual_run
from .performance_production import read, update


def partition_coverage(coverage, visual_coverage, size=8):
    visible={key(c['source']) for c in visual_coverage if c['target_block_ids']}
    groups=[];pending=[]
    for c in coverage:
        pending.append(c)
        if len(pending)>=size and any(key(x['source']) in visible for x in pending):
            groups.append(pending);pending=[]
    if pending:
        if any(key(x['source']) in visible for x in pending): groups.append(pending)
        elif groups: groups[-1].extend(pending)
        else: raise ValueError('NO_VISUAL_EVENT')
    require([key(c['source']) for g in groups for c in g]==[key(c['source']) for c in coverage],'SOURCE_COVERAGE_MISMATCH')
    return groups


def project_segment(plan_input, audio_map, visual_map, script, timeline, coverage):
    keys={key(c['source']) for c in coverage}
    am=deepcopy(audio_map);vm=deepcopy(visual_map)
    am['coverage']=[c for c in am['coverage'] if key(c['source']) in keys]
    vm['coverage']=[c for c in vm['coverage'] if key(c['source']) in keys]
    aids={x for c in am['coverage'] for x in c['target_block_ids']}
    vids={x for c in vm['coverage'] for x in c['target_block_ids']}
    positions=[p for p in timeline['placements'] if p['block_id'] in aids]
    start=min(p['start_frame'] for p in positions);end=max(p['end_frame'] for p in positions)
    tl=deepcopy(timeline);tl['placements']=deepcopy(positions);tl['total_frames']=end-start
    for p in tl['placements']:p['start_frame']-=start;p['end_frame']-=start
    target=deepcopy(plan_input)
    for sc in target['script']['scenes']:sc['blocks']=[b for b in sc['blocks'] if b['block_id'] in vids]
    target['script']['scenes']=[sc for sc in target['script']['scenes'] if sc['blocks']]
    target['script']['document_id']='d_'+sha(json_bytes(sorted(vids)))[:32]
    audio=deepcopy(script);audio['events']=[e for e in audio['events'] if e['block_id'] in aids]
    return target,am,vm,audio,tl,start,end


def visual_preparation(root, store, cap_ref, batch, delivery):
    sc=batch['scene'];roles=[c['role_id'] for c in delivery['character_snapshot']]
    names={c['role_id']:c['canon']['name'] for c in delivery['character_snapshot']}
    colors=['blue','green','red','brown']
    context={'contract_version':'my-legend.visual-context/1.0','profile_id':'storyboard-technical-context','profile_revision':'1',
       'target_profile':'web_basic_v1','presentation_profile':'standing_dialogue_v1','preview_skin':'basic','generation_mode':'reviewed','deliverable':'preview',
       'character_bindings':[{'source_role_id':rid,'presence':'onstage','name':names[rid],
          'description':'基础像素分镜外观，为制作选择；精细动作使用原文动作卡，人物效果待人工审核。',
          'appearance':{'template_type':'adult','color_key':colors[i%4],'accessory':'none'},'asset_revision':'basic_pixel/1.0.0'} for i,rid in enumerate(roles)],
       'scene_bindings':[{'source':batch['scope']['scenes'][0],'background_type':'indoor','cast_source_role_ids':roles,'initial_cast_source_role_ids':roles,
          'continuity_group_id':'same-source-scene-'+sc['scene_id'],'locked_facts':['原人物、事件、台词与场次顺序逐项保留。','此预览以基础像素演员和原文动作卡呈现，不声称完成精细动作动画。']}],
       'event_bindings':[{'source':c['source'],'action':'wait','actor_source_role_id':roles[0],'target_source_role_id':None,'target_anchor':None,
          'direction':None,'emotion':None,'intensity':None,'duration_ms':1200,'provenance':'provided'} for c in batch['audio_map']['coverage'] if c['source_kind']=='action'],
       'sound_caption_policy':'audio_only','caption_sources':[],'voice_only_policy':'block',
       'emotion_map':{'neutral':'neutral','happy':'happy','sad':'sad','angry':'angry','surprised':'surprised','serious':'serious',
          'worried':'serious','fearful':'surprised','reassuring':'neutral','playful':'happy','mysterious':'serious','tender':'happy','calm':'neutral','excited':'happy'},
       'context_confirmed':False,'context_approval_ref':None,'capability_profile':cap_ref}
    # This is a technical mapping audit, explicitly separate from the human review.
    approved={k:v for k,v in context.items() if k not in ('context_confirmed','context_approval_ref')}
    record={'story_version_id':delivery['story_version_id'],'context_content_sha256':sha(encoded(approved)),
            'scope':batch['scope'],'reviewer':'automation:source-mapping-audit','human_approval':False,
            'presentation':'basic_storyboard_action_cards','audit':'source IDs, literal text, finite poses and explicit action-card limitation checked; no human effect approval'}
    context.update(context_confirmed=True,context_approval_ref=store.put_json(record))
    context_ref=store.put_json(context)
    request_id=f"visual-{batch['index']}-{context_ref['sha256'][:12]}"
    request={'contract_version':'my-legend.adapter-request/1.0','adapter':'visual','request_id':request_id,
             'idempotency_key':request_id,'mode':'prepare_only','source':batch['source'],'scope':batch['scope'],'context':context_ref,'joint_profile':'independent'}
    result=AdapterService(store).prepare(request)
    require(result['state']=='prepared','VISUAL_PREPARE_FAILED:'+','.join(i['code'] for i in result['issues']))
    save(Path(root)/f"scenes/{batch['index']:03d}/visual-context.json",context)
    return store.json(result['target']),store.json(result['sidecar'])


def produce_storyboard(root, paths_file, delivery, store, cap_ref, batches, audio_results):
    import numpy as np
    import soundfile as sf
    from .render import render_video
    root=Path(root);paths=read(paths_file);preview=root/'preview';preview.mkdir(exist_ok=True)
    assets=Path(paths['visual_root'])/'pixel-story-mobile/packages/story_player/assets'
    all_audio=[];all_cues=[];segments=[];offset_frames=0;rate=48000;source_order=[];model_calls=0
    for batch,audio in zip(batches,audio_results):
        directory=Path(batch['path']);workflow=directory/'audio-workflow'
        samples,sample_rate=sf.read(workflow/audio['preview_directory']/'draft.wav',always_2d=True)
        require(sample_rate==rate and len(samples)==audio['timeline']['total_frames'],'AUDIO_TIMELINE_MISMATCH')
        target,vm=visual_preparation(root,store,cap_ref,batch,delivery)
        groups=partition_coverage(batch['audio_map']['coverage'],vm['coverage'])
        pmap={p['block_id']:p for p in audio['timeline']['placements']}
        for c in batch['audio_map']['coverage']:
            p=pmap[c['target_block_ids'][0]]
            u=next(u for u in batch['scene']['utterances'] if u['line_id']==c['source']['line_id'])
            kind=u['kind'];speaker=u.get('speaker_role_id') or u.get('narrator_id') or ('__action__' if kind=='action' else '__sfx__')
            all_cues.append({'speakerId':speaker,'text':u['text'],'start':(offset_frames+p['start_frame'])/rate,'end':(offset_frames+p['end_frame'])/rate,
                             'kind':kind,'sourceLineId':u['line_id'],'sceneId':batch['scene']['scene_id'],'durationBasis':'planned_silent_hold' if kind=='action' else 'measured_audio'})
            source_order.append(c['source'])
        for group in groups:
            i=len(segments);out=root/f'segments/{i:03d}';out.mkdir(parents=True,exist_ok=True)
            t,am,segment_vm,script,tl,start,end=project_segment(target,batch['audio_map'],vm,batch['audio_script'],audio['timeline'],group)
            save(out/'visual-request.json',t)
            visual_run(SimpleNamespace(root=str(out/'visual-workflow'),input=str(out/'visual-request.json'),thread='production'),paths)
            result=read(out/'visual-workflow/host-result.json')
            require(result.get('plan_hash') is not None,'VISUAL_COMPILE_FAILED')
            plan=read(out/'visual-workflow/compiled-plan.json')
            for relative,digest in plan['asset_bindings']['file_hashes'].items():
                asset=(assets/relative).resolve()
                require(asset.is_relative_to(assets.resolve()) and sha(asset.read_bytes())==digest,'VISUAL_ASSET_CHANGED')
            joined=align(plan,tl,am,segment_vm,script)
            require(abs(joined['manifest']['duration_ms']-(end-start)*1000/rate)<1,'UNEXPECTED_SILENT_TAIL')
            sf.write(out/'mix.wav',samples[start:end],rate,subtype='PCM_24')
            captions=[{'start':p['start_frame']/rate,'end':p['end_frame']/rate,'text':p['text'],'kind':next(c['source_kind'] for c in group if p['block_id'] in c['target_block_ids'])} for p in tl['placements']]
            update(root,status='rendering',progress=min(95,65+i*4),message=f'正在渲染第{i+1}个基础分镜片段；动作原文保留')
            save(out/'aligned-plan.json',joined)
            save(out/'projection.json',{'source':batch['source'],'scope':group,'source_audio_frame_range':[start,end],
                 'original_prepared_visual_target_sha256':sha(json_bytes(target)),'technical_context_review':'automation:source-mapping-audit','human_approval':False})
            segments.append({'plan':joined,'start_frame':offset_frames+start,'end_frame':offset_frames+end,'source':group})
        all_audio.append(samples);offset_frames+=len(samples);model_calls+=audio['call_budget']['production_calls']
    expected=[{'episode_id':ep['episode_id'],'scene_id':sc['scene_id'],'line_id':u['line_id']} for ep in delivery['episodes'] for sc in ep['scenes'] for u in sc['utterances']]
    require(source_order==expected,'SOURCE_COVERAGE_MISMATCH')
    sf.write(preview/'mix.wav',np.concatenate(all_audio),rate,subtype='PCM_24')
    # Sample every final frame against the global measured audio clock. Rendering
    # once avoids accumulating per-segment video/AAC rounding or encoder padding.
    starts=[s['start_frame'] for s in segments];frames=[]
    for frame_number in range(math.ceil(offset_frames/rate*10)+1):
        sample=min(offset_frames-1,round(frame_number*rate/10))
        segment=segments[max(0,bisect_right(starts,sample)-1)]
        index=max(0,min(len(segment['plan']['frames'])-1,(sample-segment['start_frame'])*10//rate))
        frame=deepcopy(segment['plan']['frames'][index]);frame['time_ms']=frame_number*100;frames.append(frame)
    combined={'document':segments[0]['plan']['document'],'manifest':{'duration_ms':offset_frames*1000/rate},'frames':frames}
    save(preview/'joint-plan.json',combined)
    render_video(combined,preview/'mix.wav',preview/'scene.mp4',assets,paths['ffmpeg'],synthetic_audio=False,
                 captions=all_cues,watermark='基础分镜预览 · 动作以文字呈现 · 待审核')
    actors=[{'id':c['role_id'],'name':c['canon']['name'],'portraitUrl':'/static/figma/avatar.png' if n==0 else '/static/figma/ai-avatar.png'} for n,c in enumerate(delivery['character_snapshot'])]
    actors.append({'id':'narrator','name':'旁白','portraitUrl':'/static/figma/ai-avatar.png'})
    media={'posterUrl':'poster.png','videoUrl':'scene.mp4','audioUrl':'mix.wav','duration':offset_frames/rate,'actors':actors,'cues':all_cues,
           'sourceVersionId':delivery['story_version_id'],'videoSha256':sha((preview/'scene.mp4').read_bytes()),
           'audioSha256':sha((preview/'mix.wav').read_bytes()),'posterSha256':sha((preview/'poster.png').read_bytes()),
           'presentation':'basic_storyboard_action_cards','humanApproval':'pending'}
    save(preview/'media.json',media)
    save(root/'production-report.json',{'source_version_id':delivery['story_version_id'],'source_events':len(source_order),'source_order_and_coverage_verified':True,
         'duration_seconds':offset_frames/rate,'sample_rate':rate,'total_frames':offset_frames,'timing':'decoded_audio_sample_frames',
         'word_alignment':'not_available','synthetic_speech':False,'synthetic_sfx':False,'planned_silent_action_holds':sum(c['kind']=='action' for c in all_cues),
         'model_calls':model_calls,'segments':len(segments),'video_sha256':media['videoSha256'],'audio_review':'pending','visual_review':'pending','publication':'not_approved'})
    return media
