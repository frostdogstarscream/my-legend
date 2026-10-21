"""Explicit joint_v03 projection; preserves narration identity and audio-only layers."""
from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
import math

from .core import encoded, finite, key, require, sha, unique


def align(plan, audio_timeline, audio_map, visual_map, audio_script):
    require(plan['manifest']['source_version_id']==audio_timeline['source_version_id']==audio_map['source_version_id']==visual_map['source_version_id'],'SOURCE_VERSION_MISMATCH',scope='joint')
    require(audio_map['source']==visual_map['source'],'SOURCE_VERSION_MISMATCH',scope='joint')
    require(not audio_map['issues'] and not visual_map['issues'],'ADAPTER_NOT_PREPARED',scope='joint')
    a_cov={key(x['source']):x for x in audio_map['coverage']}
    v_cov={key(x['source']):x for x in visual_map['coverage']}
    require(len(a_cov)==len(audio_map['coverage']) and set(a_cov)==set(v_cov),'SOURCE_COVERAGE_MISMATCH',scope='joint')
    require(list(a_cov)==list(v_cov),'SOURCE_ORDER_MISMATCH',scope='joint')
    for loc,row in a_cov.items():
        other=v_cov[loc]
        require(row['text_sha256']==other['text_sha256'] and row['source_kind']==other['source_kind'],'SOURCE_CONTENT_MISMATCH',scope='joint')
        require(row['disposition']!='blocked' and other['disposition']!='blocked','ADAPTER_NOT_PREPARED',scope='joint')
        require(len(row['target_block_ids'])<=1 and len(other['target_block_ids'])<=1,'MULTI_MAPPING_UNSUPPORTED',scope='joint')
    placements=audio_timeline['placements'];rate=audio_timeline['sample_rate'];total=audio_timeline['total_frames']
    require(isinstance(rate,int) and not isinstance(rate,bool) and rate>0 and isinstance(total,int) and not isinstance(total,bool) and total>0,'INVALID_AUDIO_TIMELINE',scope='joint')
    unique((p['block_id'] for p in placements),'DUPLICATE_AUDIO_EVENT')
    p_lookup={p['block_id']:p for p in placements}
    script_lookup={e['block_id']:e for e in audio_script['events']}
    require(audio_script['source_version_id']==audio_timeline['source_version_id'],'SOURCE_VERSION_MISMATCH',scope='joint')
    expected_a={bid for c in a_cov.values() for bid in c['target_block_ids']}
    require(set(p_lookup)==expected_a,'EVENT_SET_MISMATCH',scope='joint')
    require(set(script_lookup)==expected_a,'EVENT_SET_MISMATCH',scope='joint')
    for p in placements:
        e=script_lookup[p['block_id']]
        require(all(p.get(f)==e.get(f) for f in ('role_id','text','scene_id','kind')),'EVENT_CONTENT_MISMATCH',scope='joint')
        require(all(isinstance(p[x],int) and not isinstance(p[x],bool) for x in ('start_frame','end_frame')) and 0<=p['start_frame']<p['end_frame']<=total,'AUDIO_TIMING_CONFLICT',scope='joint')
        require(p.get('duration_basis')=='measured','AUDIO_NOT_MEASURED',scope='joint')
    blocks=[(s['scene_id'],b) for s in plan['manifest']['scenes'] for b in s['blocks']]
    unique((b['block']['block_id'] for _,b in blocks),'DUPLICATE_VISUAL_EVENT')
    expected_v={bid for c in v_cov.values() for bid in c['target_block_ids']}
    require({b['block']['block_id'] for _,b in blocks}==expected_v,'EVENT_SET_MISMATCH',scope='joint')
    require([b['block']['block_id'] for _,b in blocks]==[bid for c in v_cov.values() for bid in c['target_block_ids']],'SOURCE_ORDER_MISMATCH',scope='joint')
    v_source={bid:loc for loc,c in v_cov.items() for bid in c['target_block_ids']}
    role_map={(r['identity_kind'],r['source_identity_id']):r for r in audio_map['roles']}
    mappings=[];audio_only=[];previous=0.0
    for scene,b in blocks:
        event=b['block'];bid=event['block_id'];loc=v_source[bid];ac=a_cov[loc];vc=v_cov[loc]
        if ac['target_block_ids']:
            p=p_lookup[ac['target_block_ids'][0]]
            require(p['scene_id']==scene and sha(p['text'].encode('utf-8'))==vc['text_sha256'] and p['text']==event.get('text',''),'EVENT_CONTENT_MISMATCH',scope='joint')
            if ac['source_kind']=='dialogue' and event['type']=='dialogue':
                require(p['kind']=='speech' and p.get('role_id')==event.get('role_id'),'EVENT_CONTENT_MISMATCH',scope='joint')
            elif ac['source_kind']=='narration':
                allowed={r['target_role_id'] for (kind,_),r in role_map.items() if kind=='narrator'}
                require(event['type']=='narration' and p['kind']=='speech' and p.get('role_id') in allowed,'NARRATOR_IDENTITY_INVALID',scope='joint')
            elif ac['source_kind'] in ('sfx','music'):
                require(event['type']=='narration' and p['kind'] in ('sfx','ambience','music'),'EVENT_CONTENT_MISMATCH',scope='joint')
            elif ac['source_kind']=='dialogue':
                require(event['type']=='narration' and p['kind']=='speech' and p.get('role_id') in {r['target_role_id'] for r in role_map.values()},'EVENT_CONTENT_MISMATCH',scope='joint')
            start,end=p['start_frame']*1000/rate,p['end_frame']*1000/rate
            basis='audio_measured'
        else:
            require(event['type']=='action' and ac['source_kind']=='action','MISSING_REQUIRED_AUDIO',scope='joint')
            duration=event.get('duration_ms')
            require(finite(duration) and duration>=100,'ACTION_DURATION_REQUIRED',scope='joint')
            # Prior audio-only cues also occupy their measured interval in source order.
            preceding=[]
            for other in a_cov.values():
                if other is ac:break
                preceding.extend(p_lookup[x]['end_frame']*1000/rate for x in other['target_block_ids'] if p_lookup[x]['kind'] not in ('ambience','music'))
            start=max([previous]+preceding);end=start+duration;basis='visual_planned'
        require(start>=previous and end>start,'VISUAL_GAP_INSUFFICIENT',scope='joint')
        mappings.append({'block_id':bid,'source':vc['source'],'source_kind':vc['source_kind'],
            'old_start':b['start_ms'],'old_end':b['end_ms'],'start_ms':start,'end_ms':end,
            'duration_basis':basis,'audio_role_id':p_lookup[ac['target_block_ids'][0]].get('role_id') if ac['target_block_ids'] else None,
            'visual_role_id':event.get('role_id')})
        previous=end
    used_audio={c['target_block_ids'][0] for loc,c in a_cov.items() if c['target_block_ids'] and v_cov[loc]['target_block_ids']}
    for loc,c in a_cov.items():
        for bid in c['target_block_ids']:
            if bid not in used_audio:
                require(c['source_kind'] in ('sfx','music','action'),'MISSING_REQUIRED_VISUAL',scope='joint')
                p=p_lookup[bid]
                require(sha(p['text'].encode('utf-8'))==c['text_sha256'],'EVENT_CONTENT_MISMATCH',scope='joint')
                audio_only.append({'source':c['source'],'block_id':bid,'kind':p['kind'],'start_ms':p['start_frame']*1000/rate,'end_ms':p['end_frame']*1000/rate})
    duration=max(total*1000/rate,previous)
    require(duration<=90000,'DURATION_UNSUPPORTED',scope='joint')
    require(plan.get('frames'),'VISUAL_FRAMES_MISSING',scope='joint')
    times=[f['time_ms'] for f in plan['frames']]
    frames=[]
    for t in range(0,math.ceil(duration/100)*100+1,100):
        actual=min(t,duration);active=next((m for m in mappings if m['start_ms']<=actual<m['end_ms']),None)
        if active:
            fraction=(actual-active['start_ms'])/(active['end_ms']-active['start_ms'])
            source=active['old_start']+fraction*(active['old_end']-active['old_start'])
        else:
            prior=[m for m in mappings if m['end_ms']<=actual]
            source=prior[-1]['old_end']-.001 if prior else mappings[0]['old_start']
        frame=deepcopy(plan['frames'][max(0,min(len(times)-1,bisect_right(times,source)-1))]);frame['time_ms']=t
        if not active:
            frame.update(subtitle='',speaker_id=None,block_id=None)
            for actor in frame['actors']:actor['speaking']=False
        frames.append(frame)
    result=deepcopy(plan)
    by_id={m['block_id']:m for m in mappings}
    for scene in result['manifest']['scenes']:
        for b in scene['blocks']:
            mapping=by_id[b['block']['block_id']];b['start_ms']=mapping['start_ms'];b['end_ms']=mapping['end_ms']
        scene['start_ms']=min(b['start_ms'] for b in scene['blocks']);scene['end_ms']=max(b['end_ms'] for b in scene['blocks'])
    result.update(frames=frames,source_plan_hash=plan['plan_hash'],timing_mode='audio_measured_visual_actions_planned')
    result['manifest']['duration_ms']=duration
    result['alignment']={'profile':'my-legend.joint/0.3','sample_rate':rate,'source_audio_total_frames':total,
        'mapping':mappings,'audio_only_layers':audio_only,'silence_tail_ms':max(0,duration-total*1000/rate),
        'word_alignment':'not_available','visual_execution':'compiled_not_human_approved'}
    result['plan_hash']=sha(encoded({k:v for k,v in result.items() if k!='plan_hash'}))
    return result
