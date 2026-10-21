"""按稳定事件对齐两边产物。声音实测时间为权威，不裁切或加速音频。"""
from copy import deepcopy
import math
from .common import IntegrationError, digest

def align(plan, audio):
    if audio.get('source_version_id') != plan['manifest']['source_version_id']:
        raise IntegrationError('SOURCE_VERSION_MISMATCH','声音与视觉源版本不一致')
    timeline=audio.get('timeline') or {}
    rate=timeline.get('sample_rate',0);total=timeline.get('total_frames',0)
    if not isinstance(rate,int) or rate<=0 or not isinstance(total,int) or total<=0:
        raise IntegrationError('INVALID_AUDIO_TIMELINE','声音时间基准无效')
    placements=timeline.get('placements',[])
    ids=[p['block_id'] for p in placements]
    if len(ids)!=len(set(ids)):
        raise IntegrationError('DUPLICATE_AUDIO_EVENT','声音事件 ID 重复')
    lookup={p['block_id']:p for p in placements}
    original=[(s['scene_id'],b) for s in plan['manifest']['scenes'] for b in s['blocks']]
    if set(lookup)!= {b['block']['block_id'] for _,b in original}:
        raise IntegrationError('EVENT_SET_MISMATCH','两边事件集合不一致；需在剧情交付端明确对应关系')
    mapping=[];previous=0
    for scene,b in original:
        event=b['block'];p=lookup[event['block_id']]
        if p['scene_id']!=scene or p.get('role_id')!=event.get('role_id') or p.get('text')!=event.get('text'):
            raise IntegrationError('EVENT_CONTENT_MISMATCH',f"{event['block_id']} 的场景、角色或原文不一致")
        if event['type']=='dialogue' and p['kind']!='speech':
            raise IntegrationError('AUDIO_KIND_MISMATCH','对白没有对应语音事件')
        left,right=p['start_frame'],p['end_frame']
        if not isinstance(left,int) or not isinstance(right,int) or left<previous or right<=left or right>total:
            raise IntegrationError('AUDIO_TIMING_CONFLICT','简单演绎要求事件顺序一致、非重叠并在混音内')
        previous=right
        mapping.append({'block_id':event['block_id'],'old_start':b['start_ms'],'old_end':b['end_ms'],
                        'start_ms':left*1000/rate,'end_ms':right*1000/rate})
    duration=total*1000/rate
    if duration>90000:
        raise IntegrationError('DURATION_UNSUPPORTED','当前视觉 MVP 最长 90 秒')
    old_duration=plan['manifest']['duration_ms']
    def source_time(t):
        old_left=new_left=0
        for m in mapping:
            if t<m['start_ms']:
                gap=m['start_ms']-new_left
                # 原时间轴没有空隙时，新增间隙保持上一事件末帧，避免提前入场。
                if m['old_start']==old_left and old_left>0:
                    return old_left-.001,False
                return old_left + (m['old_start']-old_left)*(t-new_left)/gap if gap else old_left,False
            if t<m['end_ms']:
                return m['old_start']+(m['old_end']-m['old_start'])*(t-m['start_ms'])/(m['end_ms']-m['start_ms']),True
            old_left,new_left=m['old_end'],m['end_ms']
        gap=duration-new_left
        return old_left+(old_duration-old_left)*(t-new_left)/gap if gap else old_left,False
    out=deepcopy(plan);frames=[]
    for t in range(0,math.ceil(duration/100)*100+1,100):
        source,active=source_time(min(t,duration))
        index=min(len(plan['frames'])-1,max(0,math.floor((source+1e-6)/100)))
        frame=deepcopy(plan['frames'][index]);frame['time_ms']=t
        # 新增音频间隙不沿用上一句或提前展示下一句；空隙中角色保持已有状态。
        if not active:
            frame.update(subtitle='',speaker_id=None,block_id=None)
            for actor in frame['actors']:actor['speaking']=False
        frames.append(frame)
    def target_time(t):
        old_left=new_left=0
        for m in mapping:
            if t<=m['old_start']:
                gap=m['old_start']-old_left
                return new_left+(m['start_ms']-new_left)*(t-old_left)/gap if gap else m['start_ms']
            if t<=m['old_end']:
                return m['start_ms']+(m['end_ms']-m['start_ms'])*(t-m['old_start'])/(m['old_end']-m['old_start'])
            old_left,new_left=m['old_end'],m['end_ms']
        return new_left+(duration-new_left)*(t-old_left)/(old_duration-old_left) if old_duration>old_left else duration
    for scene in out['manifest']['scenes']:
        scene['start_ms']=target_time(scene['start_ms']);scene['end_ms']=target_time(scene['end_ms'])
        for b in scene['blocks']:
            m=next(m for m in mapping if m['block_id']==b['block']['block_id'])
            b['start_ms'],b['end_ms']=m['start_ms'],m['end_ms']
    out['frames']=frames;out['manifest']['duration_ms']=duration
    out['source_plan_hash']=plan['plan_hash'];out['timing_mode']='audio_measured_event_aligned'
    out['alignment']={'sample_rate':rate,'mapping':mapping,'word_alignment':'not_available'}
    out['plan_hash']=digest({k:v for k,v in out.items() if k!='plan_hash'})
    return out
