"""Fixed commander choreography. Archived 3.0 retains its original reader."""
from copy import deepcopy
from hashlib import sha256
import json
import math
import re
from typing import Literal
from pydantic import Field
from .contracts import Contract
from .h5_preview import (build_h5_preview, load_h5_catalog, parse, coverage_status,
                         source_context, position_at, movement_issue)
from .life_contracts import fail, action_durations, canonical_hash, LifeValidationError
from .scene_preview import load_scene_catalog
from .repository import ProductionError


class CommanderExpression(Contract):
    id: str
    instance_id: str
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    enabled: bool = True
    channel: Literal['eyes', 'mouth']
    preset: Literal['native', 'open', 'closed', 'smile', 'squint', 'surprised']


class FurnitureCue(Contract):
    id: str
    instance_id: str
    episode_id: str
    scene_id: str
    seat_id: str
    calibration_revision: str
    outfit_id: str
    start_frame: int = Field(ge=0)
    arrive_frame: int = Field(gt=0)
    leave_frame: int | None = Field(default=None, ge=0)
    end_frame: int = Field(gt=0)
    enabled: bool = True
    source: dict | None = None
    source_text: str = ''
    reason: str = ''


def commander_scene_digest(root, catalog):
    try:
        pointer = json.loads((root/'commander-scene-current.json').read_text('utf-8'))
        digest = pointer.get('by_character_catalog', {}).get(catalog['catalog_sha256'], pointer['catalog_sha256'])
        rooms = load_scene_catalog(root, digest)
        if rooms.get('commander_catalog_sha256') != catalog['catalog_sha256']: raise ValueError()
        return digest
    except (OSError, ValueError, KeyError):
        raise ProductionError('COMMANDER_SCENES_NOT_CONFIGURED') from None


def outfit_at(script, segment, iid, frame):
    oid = next(a['initial_outfit_id'] for a in script['actors'] if a['instance_id'] == iid)
    for c in sorted(script['cues'], key=lambda c: (c['start_frame'], c['id'])):
        if c['enabled'] and c['instance_id'] == iid and c['type'] == 'actor.change_outfit' and segment['start_frame'] <= c['start_frame'] <= frame:
            oid = c['params']['outfit_id']
    return oid


def room_for(segment, rooms):
    return next(r for r in rooms[segment['scene']['catalog_sha256']]['scenes'] if r['scene_id'] == segment['scene']['scene_id'])


def validate_faces(values, script, catalog, track):
    parsed = [parse(CommanderExpression, v, 'expression_track') for v in values]
    actors = {a['instance_id']: a for a in script['actors']}
    if len({c['id'] for c in parsed}) != len(parsed): fail('expression_track', '表情 ID 重复')
    for c in parsed:
        if c['instance_id'] not in actors or not c['start_frame'] < c['end_frame'] <= script['total_frames']:
            fail(c['id'], '表情演员或时间无效')
        actor = next(a for a in catalog['actors'] if a['actor_id'] == actors[c['instance_id']]['actor_id'])
        if not actor.get('face_profile') and c['preset']!='native':fail(c['id'], '该人形保留原生表情，未配置独立眼嘴控制')
        allowed = ('native', 'open', 'closed') if c['channel'] == 'mouth' else ('native', *actor.get('face_profile',{}).get('eyes',{}))
        if c['preset'] not in allowed: fail(c['id'], '嘴部与眼部预设不可混用')
        if c['enabled']:
            if not any(s['start_frame'] <= c['start_frame'] < c['end_frame'] <= s['end_frame'] for s in track): fail(c['id'], '表情不可跨场')
            if any(o is not c and o['enabled'] and (o['instance_id'], o['channel']) == (c['instance_id'], c['channel']) and max(o['start_frame'], c['start_frame']) < min(o['end_frame'], c['end_frame']) for o in parsed):
                fail(c['id'], '同一演员同一表情通道区间重叠')
    return parsed


def floor_script(script, cues, resolved):
    """Project furniture to the foot lane for conservative actor collision checks."""
    result = deepcopy(script)
    for c in cues:
        if not c['enabled']: continue
        seat = resolved[c['id']]
        def add(suffix, kind, start, end, params):
            result['cues'].append(dict(id=c['id']+suffix, type=kind, instance_id=c['instance_id'], start_frame=start, end_frame=end, enabled=True, params=params))
        add('_approach', 'actor.move_to', c['start_frame'], c['arrive_frame'], dict(to=seat['approach'], visible_after=True))
        if c['leave_frame'] is not None:
            add('_depart', 'actor.move_to', c['leave_frame']+math.ceil(.35*script['sample_rate']), c['end_frame'], dict(to=seat['exit'], visible_after=True))
    return result


def validate_furniture(values, script, track, rooms, catalog):
    if values is None: values = dict(schema_version='commander_furniture/1', cues=[])
    if values.get('schema_version') != 'commander_furniture/1' or set(values)-{'schema_version','cues','resolved'}: fail('furniture_track', '家具轨道版本或字段无效')
    cues = [parse(FurnitureCue, c, 'furniture_track') for c in values.get('cues', [])]
    if len(cues)>512 or len({c['id'] for c in cues}) != len(cues): fail('furniture_track','家具指令过多或 ID 重复')
    if {c['id'] for c in cues}&{c['id'] for c in script['cues']}: fail('furniture_track','家具和身体指令不能使用相同 ID')
    resolved = {}; actors = {a['instance_id']: a for a in script['actors']}
    for c in cues:
        iid=c['instance_id'];segment=next((s for s in track if (s['episode_id'],s['scene_id'])==(c['episode_id'],c['scene_id'])),None)
        if iid not in actors or not segment: fail(c['id'],'家具演员或分场无效')
        if not segment['start_frame'] <= c['start_frame'] < c['arrive_frame'] < c['end_frame'] <= segment['end_frame']: fail(c['id'],'家具时间必须在同一分场内，并留出坐姿时间')
        leave=c['leave_frame']
        if leave is None and c['end_frame'] != segment['end_frame']: fail(c['id'],'保持坐姿必须持续到本场结束')
        if leave is not None and not c['arrive_frame'] < leave < leave+math.ceil(.35*script['sample_rate']) < c['end_frame']: fail(c['id'],'起身与离开时间不足')
        room=room_for(segment,rooms);rc=rooms[segment['scene']['catalog_sha256']]
        if rc.get('commander_catalog_sha256') != catalog['catalog_sha256']: fail(c['id'],'场景校准未绑定此指挥官目录')
        seat=next((s for s in room.get('commander_seats',[]) if s['seat_id']==c['seat_id']),None)
        if not seat or seat['calibration_revision'] != c['calibration_revision']: fail(c['id'],'座位缺失或校准版本已变化')
        if not any(o['id']==seat['object_id'] for o in room['objects']): fail(c['id'],'座位必须绑定实际家具')
        oid=outfit_at(script,segment,iid,c['start_frame']);outfit=next(o for o in catalog['outfits'] if o['outfit_id']==oid)
        group=outfit.get('calibration_group');cal=rc.get('commander_calibrations',{}).get(group)
        asset=next(a for a in catalog['actors'] if a['actor_id']==actors[iid]['actor_id'])
        if c['outfit_id']!=oid or group not in seat['calibrated_groups'] or not cal or cal['actor_id']!=asset['actor_id'] or cal['head_sha256']!=asset['face_profile']['head_sha256'] or cal['normalization_outfit']!=asset['normalization_outfit_id']:
            fail(c['id'],'服装、固定头部或坐姿校准不匹配')
        if c['source'] is not None and (not set(c['source'])=={'episode_id','scene_id','line_id'} or (c['source']['episode_id'],c['source']['scene_id'])!=(segment['episode_id'],segment['scene_id'])): fail(c['id'],'家具动作原文来源与分场不一致')
        resolved[c['id']]={k:deepcopy(seat[k]) for k in ('object_id','position','approach','exit','facing')}
        resolved[c['id']].update(root_offset=[cal['root_offset'][0]*seat['facing'],cal['root_offset'][1]],actor_scale=room['actor_scale'])
        if not c['enabled']:continue
        for o in cues:
            if o is c or not o['enabled'] or (o['episode_id'],o['scene_id'])!=(c['episode_id'],c['scene_id']):continue
            if max(o['start_frame'],c['start_frame'])<min(o['end_frame'],c['end_frame']) and (o['instance_id']==iid or o['seat_id']==c['seat_id']):fail(c['id'],'同一演员或同一座位的家具区间重叠')
        for o in script['cues']:
            if not o['enabled'] or o['instance_id']!=iid or o['type'] not in ('actor.change_outfit','actor.place','actor.move_to','body.play'):continue
            overlap=c['start_frame']<=o['start_frame']<c['end_frame'] or max(o['start_frame'],c['start_frame'])<min(o['end_frame'],c['end_frame'])
            if overlap:fail(c['id'],'家具互动期间不能换装或插入身体、位置指令')
    floor=floor_script(script,cues,resolved)
    for segment in track:
        room=room_for(segment,rooms)
        for c in floor['cues']:
            if not c['enabled'] or c['type']!='actor.move_to' or not segment['start_frame']<=c['start_frame']<segment['end_frame']:continue
            probe={**floor,'cues':[x for x in floor['cues'] if x['id']!=c['id']]}
            issue=movement_issue(probe,segment,c['instance_id'],c['start_frame'],c['end_frame'],c['params']['to'],room)
            if issue:fail(c['id'],issue)
    return dict(schema_version='commander_furniture/1',cues=cues,resolved=resolved)


def furniture_rules(value, delivery, rooms, catalog, timeline, lines):
    """Explicit furniture nouns only; unresolved sit remains an editable gap."""
    script=value['stage'];track=value['scene_track']['segments'];cues=[]
    aliases={a['instance_id']:{a['name']} for a in script['actors']}
    for char in delivery.get('character_snapshot',[]):
        actor=next((a for a in script['actors'] if a['role_id']==char['role_id']),None)
        if actor:aliases[actor['instance_id']].update(n for n in char.get('canon',{}).get('aliases',[]) if isinstance(n,str) and n)
    # Rising is not part of the old 3.0 keyword table. Keep this addition local.
    for event in timeline['events']:
        ref=event.get('source',event);source={k:ref[k] for k in ('episode_id','scene_id','line_id')}
        line=lines.get(tuple(source.values()))
        if not line:continue
        for index,op in enumerate(event.get('operations',[event])):
            text=op.get('cue_text') or line['text']
            if op.get('mode',op.get('render_role','speech')) not in ('speech','action_note') or not re.search('起身|站起来',text):continue
            from .life_preview import NON_EXECUTION
            if NON_EXECUTION.search(text) or re.search('[？?]',text):continue
            existing=next((c for c in value['coverage'] if c['source']==source and re.search('起身|站起来',c['word'])),None)
            if existing:existing.update(action='stand',status='needs_correction',message='请关联此前的坐家具动作')
            else:value['coverage'].append(dict(id='coverage_stand_'+canonical_hash([source,index])[:20],source=source,text=text,word='起身',action='stand',start_frame=op['start_frame'],end_frame=op['end_frame'],status='needs_correction',cue_ids=[],message='请关联此前的坐家具动作',instance_id=None,target=None))
    value['coverage'].sort(key=lambda i:(i['start_frame'],i['id']))
    for item in value['coverage']:
        if item['action']=='stand':
            prefix=re.split('起身|站起来',item['text'])[0];hits=[iid for iid,names in aliases.items() if any(n in prefix for n in names)]
            if len(hits)!=1:continue
            item['instance_id']=hits[0]
            previous=[c for c in cues if c['instance_id']==hits[0] and c['source']['episode_id']==item['source']['episode_id'] and c['source']['scene_id']==item['source']['scene_id'] and c['arrive_frame']<item['start_frame'] and c['leave_frame'] is None]
            if len(previous)!=1:continue
            target=previous[0];proposed=deepcopy(target);proposed.update(leave_frame=item['start_frame'],end_frame=item['start_frame']+round(1.75*script['sample_rate']))
            candidate=[proposed if c is target else c for c in cues]
            try:validate_furniture(dict(schema_version='commander_furniture/1',cues=candidate),script,track,rooms,catalog)
            except LifeValidationError as exc:item['message']='；'.join(d['message'] for d in exc.diagnostics);continue
            cues=candidate;item.update(status='compiled',instance_id=hits[0],cue_ids=[target['id']],message='已关联起身和离开时间');continue
        if item['word'] not in ('坐下','落座') or item['status']=='not_executed':continue
        item.update(status='needs_correction',message='请明确演员与实际座位；单独坐下不推断家具',action='sit')
        segment=next(s for s in track if (s['episode_id'],s['scene_id'])==(item['source']['episode_id'],item['source']['scene_id']))
        room=room_for(segment,rooms);text=item['text'];prefix=text.split(item['word'])[0]
        hits=[iid for iid,names in aliases.items() if any(n in prefix for n in names)]
        seats=[s for s in room.get('commander_seats',[]) if any(n and n in text for n in (s['label'],re.sub('左侧|右侧|前|旁','',s['label']), '沙发' if '沙发' in s['label'] else '椅子' if '椅' in s['label'] else '凳子' if '凳' in s['label'] else ''))]
        if len(hits)!=1 or len(seats)!=1:continue
        iid=hits[0];seat=seats[0];item['instance_id']=iid
        c=dict(id=item['id']+'_seat',instance_id=iid,episode_id=segment['episode_id'],scene_id=segment['scene_id'],seat_id=seat['seat_id'],calibration_revision=seat['calibration_revision'],outfit_id=outfit_at(script,segment,iid,item['start_frame']),start_frame=item['start_frame'],arrive_frame=item['start_frame']+round(1.4*script['sample_rate']),leave_frame=None,end_frame=segment['end_frame'],enabled=True,source=item['source'],source_text=text,reason='原文明确家具的规则编排')
        try:validate_furniture(dict(schema_version='commander_furniture/1',cues=cues+[c]),script,track,rooms,catalog)
        except LifeValidationError as exc:item['message']='；'.join(d['message'] for d in exc.diagnostics);continue
        cues.append(c);item.update(status='compiled',cue_ids=[c['id']],message='已生成走近并坐至本场结束；可调整起身与离开时间')
    return cues


def build_commander_preview(job, body, store, catalog, root):
    if catalog.get('actor_model') not in ('fixed_commander/1','mixed_life/1'):fail('catalog','需要固定指挥官目录')
    base_body=body.model_copy(update=dict(expression_track=[],coverage_decisions=[],review_confirmed=False))
    value=build_h5_preview(job,base_body,store,catalog,root)
    script=value['stage'];track=value['scene_track']['segments']
    rooms={s['scene']['catalog_sha256']:load_scene_catalog(root,s['scene']['catalog_sha256']) for s in track}
    _,delivery,timeline,lines,_,_=source_context(job,store,catalog)
    auto=furniture_rules(value,delivery,rooms,catalog,timeline,lines)
    furniture=validate_furniture(dict(schema_version='commander_furniture/1',cues=auto) if body.operation=='compile' else body.furniture_track,script,track,rooms,catalog)
    for c in furniture['cues']:
        if c['source'] and tuple(c['source'][k] for k in ('episode_id','scene_id','line_id')) not in lines:fail(c['id'],'家具来源行不存在')
    proxy={**script,'cues':script['cues']+[dict(id=c['id'],enabled=c['enabled'],source=c['source']) for c in furniture['cues']]}
    # A manual rising correction refers to the same furniture interval, whose
    # primary provenance remains the earlier sitting line. Validate it explicitly.
    stand_decisions=[];ordinary_decisions=[]
    for decision in body.coverage_decisions:
        item=next((i for i in value['coverage'] if i['id']==decision.get('coverage_id')),None)
        if item and item['action']=='stand' and decision.get('resolution')=='manual':
            from .h5_preview import CoverageDecision
            d=parse(CoverageDecision,decision,'coverage_decisions')
            linked=[c for c in furniture['cues'] if c['id'] in d['cue_ids'] and c['enabled'] and c['leave_frame'] is not None and c['instance_id']==item['instance_id'] and (c['episode_id'],c['scene_id'])==(item['source']['episode_id'],item['source']['scene_id'])]
            if not d['cue_ids'] or len(linked)!=len(d['cue_ids']):fail('coverage_decisions','起身校正须关联同一人物、同一分场的有效起身区间')
            item.update(status='manual',cue_ids=d['cue_ids'],message=d['note']);stand_decisions.append(d)
        else:ordinary_decisions.append(decision)
    coverage,decisions=coverage_status(value['coverage'],ordinary_decisions,proxy)
    decisions+=stand_decisions
    if len({d['coverage_id'] for d in decisions})!=len(decisions):fail('coverage_decisions','动作处理记录重复')
    expressions=validate_faces(body.expression_track,script,catalog,track)
    iids={a['instance_id'] for a in script['actors']}
    if set(body.mouth_enabled)-iids:fail('mouth_enabled','口型开关引用未知演员')
    profiles={a['instance_id']:deepcopy(next(x.get('face_profile') for x in catalog['actors'] if x['actor_id']==a['actor_id'])) for a in script['actors']}
    pending=[c for c in coverage if c['status'] in ('needs_correction','unsupported')]
    omitted=[c for c in coverage if c['status']=='omitted']
    unbound=[r for r in value['roles'] if r['role_id'] not in {a['role_id'] for a in script['actors']}]
    if body.review_confirmed and pending:fail('review_confirmed','仍有未处理的原文动作，不能确认演绎')
    status='needs_attention' if pending else 'confirmed_with_omissions' if body.review_confirmed and (omitted or unbound) else 'confirmed' if body.review_confirmed else 'preview_ready'
    diagnostics=[dict(code='ACTION_'+c['status'].upper(),message=c['message'],source=c['source'],text=c['text']) for c in coverage if c['status'] not in ('compiled','manual')]
    diagnostics.extend(d for d in value['diagnostics'] if d['code']=='UNBOUND_ROLE')
    if body.upgraded_from:
        from .h5_preview import read_h5_preview
        old=read_h5_preview(root,job,body.upgraded_from,store)
        if old['source_version_id']!=value['source_version_id']:fail('upgraded_from','升级来源作品不匹配')
    value.update(schema_version='spine_stage_preview/3.1',mouth_control=True,face_profiles=profiles,
        mouth_enabled={iid:bool(profiles.get(iid)) and body.mouth_enabled.get(iid,True) for iid in sorted(iids)},expression_track=expressions,furniture_track=furniture,
        outfit_actions={o['outfit_id']:action_durations(next(a for a in catalog['actors'] if a['actor_id']==o['actor_id']),catalog,o['outfit_id']) for o in catalog['outfits']},
        coverage=coverage,coverage_decisions=decisions,review_confirmed=body.review_confirmed,status=status,diagnostics=diagnostics,upgraded_from=body.upgraded_from)
    return value


def read_commander_preview(root,job,digest,store):
    if not re.fullmatch('[a-f0-9]{64}',digest) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',job['id']):raise ProductionError('SPINE_PREVIEW_NOT_FOUND')
    try:
        raw=(root/'previews'/job['id']/(digest+'.json')).read_bytes();value=json.loads(raw)
        if sha256(raw).hexdigest()!=digest or value['job_id']!=job['id'] or value['schema_version']!='spine_stage_preview/3.1' or value['profile']!='life_v2':raise ValueError()
    except (OSError,ValueError,KeyError):raise ProductionError('SPINE_PREVIEW_NOT_FOUND') from None
    validate_commander_value(root,job,value,store)
    return {**value,'preview_sha256':digest}


def validate_commander_value(root,job,value,store):
    from .spine_preview import StagePreviewRequest
    body=StagePreviewRequest(profile='life_v2',operation='validate_and_save',script=value['stage'],scene_bindings=value['scene_bindings'],coverage_decisions=value['coverage_decisions'],expression_track=value['expression_track'],dialogue_enabled=value['dialogue_enabled'],review_confirmed=value['review_confirmed'],mouth_enabled=value['mouth_enabled'],furniture_track=value['furniture_track'],upgraded_from=value['upgraded_from'])
    rebuilt=build_commander_preview(job,body,store,load_h5_catalog(root,value['catalog_sha256']),root)
    if rebuilt!=value:raise ProductionError('H5_PREVIEW_REVALIDATION_MISMATCH')
