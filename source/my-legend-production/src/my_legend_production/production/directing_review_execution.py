"""Review-only runtime projection; never changes the plan or delivery evidence."""
from copy import deepcopy
from .h5_preview import position_at


def review_execution(preview, summary):
    result=deepcopy(summary)
    result['playback_semantics']={
        'before':'execution.before and members.before describe the state BEFORE movement, not the facing during movement.',
        'movement_facing':'actor.move_to automatically faces the horizontal travel direction from its start frame: right=1, left=-1. An explicit face action before walking is not required.',
        'sampling':'movement_runtime contains deterministic start, midpoint, last moving frame and end states from the same position/facing rules as playback. It is execution evidence, not a model claim.',
    }
    result['movement_runtime']=[]
    script=preview['stage'];segments=preview['scene_track']['segments']
    for cue in script['cues']:
        if not cue['enabled'] or cue['type']!='actor.move_to':continue
        start,end=cue['start_frame'],cue['end_frame']
        segment=next(s for s in segments if s['start_frame']<=start<s['end_frame'])
        samples=[]
        for label,frame in [('start',start),('midpoint',(start+end)//2),('last_moving_frame',end-1),('end',min(end,segment['end_frame']-1))]:
            state=position_at(script,segment,cue['instance_id'],frame)
            samples.append(dict(label=label,sample_frame=frame,position=state['position'],facing=state['facing'],visible=state['visible']))
        result['movement_runtime'].append(dict(cue_id=cue['id'],source=cue['source'],instance_id=cue['instance_id'],samples=samples))
    return result
