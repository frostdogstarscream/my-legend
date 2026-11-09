"""Translate an explicitly directed group without changing its order or spacing."""
import math
from .life_contracts import fail


def group_trajectories(cue_id, states, destination, lane):
    """States are leader first, then followers, all sampled at the same time.

    The director chooses the leader, destination, and initial blocking. This
    helper only derives an equal displacement for every participant. It never
    reorders, teleports, clips, or moves a non-participating actor.
    """
    if not 2<=len(states)<=3 or len({s['instance_id'] for s in states})!=len(states):
        fail(cue_id,'协同行走必须包含一位领走者及一至两位不同的跟随者')
    if any(not s['visible'] for s in states):
        fail(cue_id,'协同行走的所有演员必须已经在场')
    delta=destination[0]-states[0]['position'][0]
    if abs(delta)<1e-6:fail(cue_id,'协同行走必须产生位移，不能原地行走')
    direction=1 if delta>0 else -1
    if any(direction*(states[0]['position'][0]-s['position'][0])<.10-1e-9 for s in states[1:]):
        fail(cue_id,'领走者必须位于行进方向最前方；向右时 x 最大，向左时 x 最小。请修正本场初始站位或有依据的领走人，不能穿过跟随者')
    for i,state in enumerate(states):
        if any(abs(state['position'][0]-other['position'][0])<.10-1e-9 for other in states[i+1:]):
            fail(cue_id,'协同行走的初始间距不足')
    result=[]
    for state in states:
        to=[state['position'][0]+delta,destination[1]]
        if not lane[0]-1e-9<=to[0]<=lane[2]+1e-9:
            fail(cue_id,'协同行走会使跟随者越出通道，须调整目的地或初始队形')
        result.append(dict(instance_id=state['instance_id'],before=state,to=to))
    # Preserve the exact catalog/model destination for the leader.
    result[0]['to']=destination[:]
    return result


def exit_trajectories(cue_id,states,destination,lane,total_frames):
    """Walk to one exit at a shared speed, hiding each actor on arrival.

    total_frames is the director's interval until the last follower exits.
    Earlier actors travel a shorter distance and therefore leave sooner. Frame
    rounding is upward; all generated trajectories still undergo the normal
    collision and timing validators.
    """
    group_trajectories(cue_id,states,destination,lane)
    distances=[abs(destination[0]-s['position'][0]) for s in states]
    furthest=max(distances)
    return [dict(instance_id=s['instance_id'],before=s,to=destination[:],
                 duration_frames=max(1,math.ceil(total_frames*(distance/furthest))))
            for s,distance in zip(states,distances)]
