"""Explicit degraded delivery: preserve speech, never fabricate successful sound assets."""
from copy import deepcopy
from hashlib import sha256

from .compound_audio import CompoundArrangement, compound_bytes


def omit_sounds(plan, operation_ids, reason):
    value=plan.model_dump()
    known={o.operation_id for e in plan.events for o in e.operations if o.mode in ('one_shot','background')}
    if not set(operation_ids)<=known:
        raise ValueError('DELIVERY_OMISSION_MUST_BE_SOUND')
    omissions=[]
    for event in value['events']:
        changed=False
        for op in event['operations']:
            if op['operation_id'] not in operation_ids and (op.get('control_target') or op.get('stop_target')) not in operation_ids:
                continue
            omissions.append(dict(source=deepcopy(event['source']),operation_id=op['operation_id'],
                text=op['cue_text'],original_mode=op['mode'],reason=reason,importance='not_human_reviewed'))
            op.update(mode='action_note',reason=reason,end=None,stop_target=None,control_target=None,
                transition_end=None,target_gain_db=None,gain_db=0,fade_out_ms=0)
            changed=True
        if changed:
            event['sound_intent']='omitted_for_delivery'
    return CompoundArrangement.model_validate(value),omissions


def speech_only_plan(delivery, delivery_sha256, reason):
    events=[];previous=None;omissions=[]
    for ep in delivery['episodes']:
        for sc in ep['scenes']:
            for line in sc['utterances']:
                spoken=line['kind'] in ('dialogue','narration')
                oid=f'delivery_{len(events)+1:04}'
                source=dict(episode_id=ep['episode_id'],scene_id=sc['scene_id'],line_id=line['line_id'])
                event=dict(source=source,kind=line['kind'],text=line['text'],
                    identity_id=line.get('speaker_role_id') or line.get('narrator_id') if spoken else None,
                    operations=[dict(operation_id=oid,mode='speech' if spoken else 'action_note',
                        cue_text=line['text'],reason=reason,start=dict(operation_id=previous,point='end' if previous else 'start',offset_ms=0))])
                if line['kind']=='sfx':
                    event['sound_intent']='omitted_for_delivery'
                    omissions.append(dict(source=source,operation_id=oid,text=line['text'],original_mode='unplanned_sound',
                                          reason=reason,importance='not_human_reviewed'))
                if spoken:previous=oid
                events.append(event)
    return CompoundArrangement(source_version_id=delivery['story_version_id'],delivery_sha256=delivery_sha256,events=events),omissions


def delivery_review(plan, *, listen_first=False):
    """Technical delivery authorization, explicitly not a model semantic approval."""
    from .compound_audio_plan import CompoundArrangementReview
    note=('listen_first：仅授权技术可执行方案进入试听制作，未执行语义审核，听感待确认。'
          if listen_first else 'audio_first：声音已降级省略，未宣称语义或听感通过；完整对白与旁白保留。')
    return CompoundArrangementReview(source_version_id=plan.source_version_id,delivery_sha256=plan.delivery_sha256,
        arrangement_sha256=sha256(compound_bytes(plan)).hexdigest(),passed=True,
        checks=[dict(source=e.source.model_dump(),text=e.text,passed=True,issues=[],advisories=[note],
            operation_checks=[dict(operation_id=o.operation_id,cue_text=o.cue_text,passed=True,issues=[],advisories=[note])
                              for o in e.operations]) for e in plan.events])
