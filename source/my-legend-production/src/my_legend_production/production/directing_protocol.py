"""Line-grouped model output; expand identifiers without changing directing choices."""
from copy import deepcopy
import json
import math
from typing import Literal, Annotated
from pydantic import Field, create_model
from .contracts import Contract
from .directing_contracts import (Anchor, DirectedAction, DirectedExpression,
    DirectedFurniture, DirectedScene, DirectingPlan)
from .life_contracts import fail


class LocalAnchor(Contract):
    operation_index: int = Field(default=0, ge=0)
    edge: Literal['start', 'end'] = 'start'
    offset_ms: int = Field(default=0, ge=-30000, le=30000)


class ReferencedAnchor(LocalAnchor):
    source_ref: str


def local_model(name, original):
    # Share parameter constraints with the canonical compiler contract.
    fields={k:(v.annotation,deepcopy(v)) for k,v in original.model_fields.items()
            if k not in ('id','quote','anchor','leave_anchor')}
    fields['anchor']=(LocalAnchor,Field(default_factory=LocalAnchor))
    if 'leave_anchor' in original.model_fields:
        fields['leave_anchor']=(ReferencedAnchor | None,None)
    return create_model(name,__base__=Contract,**fields)


BeatAction=local_model('BeatAction',DirectedAction)
BeatExpression=local_model('BeatExpression',DirectedExpression)
BeatFurniture=local_model('BeatFurniture',DirectedFurniture)


class SourceBeat(Contract):
    source_ref: str
    quote: str = Field(min_length=1,max_length=1000)
    classification: Literal['no_visible_action','performed','gap']
    reason: str = Field(min_length=1,max_length=1000)
    actions: list[BeatAction] = Field(default_factory=list,max_length=30)
    expressions: list[BeatExpression] = Field(default_factory=list,max_length=30)
    furniture: list[BeatFurniture] = Field(default_factory=list,max_length=30)


class GroupedPlan(Contract):
    schema_version: Literal['spine_directing_plan/1.1']
    scenes: list[DirectedScene] = Field(min_length=1,max_length=128)
    beats: list[SourceBeat] = Field(min_length=1,max_length=2000)


class CueParameters(Contract):
    role_id: str
    reason: str = Field(min_length=1,max_length=1000)
    anchor: LocalAnchor = Field(default_factory=LocalAnchor)


class TimedParameters(CueParameters):
    duration_ms: int | None = Field(default=None,ge=300,le=30000)


class NamedTarget(Contract):
    kind: Literal['object','slot','entrance']
    id: str


class PersonTarget(Contract):
    kind: Literal['actor']
    role_id: str


class PositionTarget(Contract):
    kind: Literal['position']
    x: float = Field(ge=0,le=1,allow_inf_nan=False)


class MoveAction(TimedParameters):
    kind: Literal['move']
    target: Annotated[NamedTarget | PersonTarget | PositionTarget,Field(discriminator='kind')]


class WalkInAction(TimedParameters):
    kind: Literal['walk_in']
    slot_id: str


class WalkOutAction(TimedParameters):
    kind: Literal['walk_out']
    entrance_id: str


class FaceAction(CueParameters):
    kind: Literal['face']
    facing: Literal[-1,1]


class BodyAction(TimedParameters):
    kind: Literal['idle','sit','pick','lie','victory','victoryloop']


Action = Annotated[MoveAction | WalkInAction | WalkOutAction | FaceAction | BodyAction,Field(discriminator='kind')]


class StructuredBeat(SourceBeat):
    actions: list[Action] = Field(default_factory=list,max_length=30)


class StructuredPlan(Contract):
    schema_version: Literal['spine_directing_plan/1.2']
    scenes: list[DirectedScene] = Field(min_length=1,max_length=128)
    beats: list[StructuredBeat] = Field(min_length=1,max_length=2000)


class GroupMoveAction(TimedParameters):
    kind: Literal['move_group']
    followers: list[str] = Field(min_length=1,max_length=2)
    target: Annotated[NamedTarget | PositionTarget,Field(discriminator='kind')]


CoordinatedAction = Annotated[MoveAction | GroupMoveAction | WalkInAction | WalkOutAction | FaceAction | BodyAction,Field(discriminator='kind')]


class CoordinatedBeat(StructuredBeat):
    actions: list[CoordinatedAction] = Field(default_factory=list,max_length=30)


class CoordinatedPlan(StructuredPlan):
    schema_version: Literal['spine_directing_plan/1.3']
    beats: list[CoordinatedBeat] = Field(min_length=1,max_length=2000)


class GroupWalkOutAction(TimedParameters):
    kind: Literal['walk_out_group']
    followers: list[str] = Field(min_length=1,max_length=2)
    entrance_id: str


CoordinatedExitAction=Annotated[MoveAction | GroupMoveAction | WalkInAction | WalkOutAction | GroupWalkOutAction | FaceAction | BodyAction,Field(discriminator='kind')]


class CoordinatedExitBeat(CoordinatedBeat):
    actions: list[CoordinatedExitAction] = Field(default_factory=list,max_length=30)


class CoordinatedExitPlan(CoordinatedPlan):
    schema_version: Literal['spine_directing_plan/1.4']
    beats: list[CoordinatedExitBeat] = Field(min_length=1,max_length=2000)


class StageBeat(CoordinatedExitBeat):
    classification: Literal['no_visible_action','performed']
    presentation: Literal['direct','adapted','audio_led']
    intent: str = Field(min_length=1,max_length=500)
    simplification: str = Field(max_length=700)


class AssetStagePlan(CoordinatedExitPlan):
    schema_version: Literal['spine_directing_plan/1.5']
    beats: list[StageBeat] = Field(min_length=1,max_length=2000)


def source_refs(context):
    return {f'src_{i:04d}':line for i,line in enumerate(context['model_input']['source_lines'],1)}


def planner_input(context):
    data=deepcopy(context['model_input'])
    data['source_lines']=[dict(line,source_ref=ref) for ref,line in source_refs(context).items()]
    rate=data['sample_rate']
    scenes={(s['episode_id'],s['scene_id']):s for s in data['source_scenes']}
    # The model chooses source-relative timing in milliseconds. Audio sample
    # frames remain immutable in the stored context and deterministic compiler;
    # exposing both units led real plans to copy frame counts into duration_ms.
    data['timing_units']='All supplied timing values are milliseconds. Anchor operation_index is zero-based. Audio frames are handled by the compiler.'
    data['scene_state_rule']='Each scene resets to its own scenes[].actors. Movement or visibility in the previous scene is not inherited.'
    for actor in data['locked_cast']:
        actor['actions']={name:dict(duration_ms=math.ceil(seconds*1000),playback='once' if name=='victory' else 'loop')
            for name,seconds in actor['actions'].items()}
    for line in data['source_lines']:
        scene=scenes[(line['source']['episode_id'],line['source']['scene_id'])]
        line['operations']=[dict({k:v for k,v in op.items() if k not in ('start_frame','end_frame')},operation_index=i,start_ms=round(op['start_frame']*1000/rate,3),
            end_ms=round(op['end_frame']*1000/rate,3),duration_ms=round((op['end_frame']-op['start_frame'])*1000/rate,3),
            remaining_scene_ms=round((scene['end_frame']-op['start_frame'])*1000/rate,3)) for i,op in enumerate(line['operations'])]
    data['source_scenes']=[dict({k:v for k,v in s.items() if k not in ('start_frame','end_frame')},
        start_ms=round(s['start_frame']*1000/rate,3),end_ms=round(s['end_frame']*1000/rate,3),
        duration_ms=round((s['end_frame']-s['start_frame'])*1000/rate,3)) for s in data['source_scenes']]
    data['total_duration_ms']=round(data.pop('total_frames')*1000/rate,3)
    data.pop('sample_rate')
    return data


def grouped_schema(context):
    from .stage_policy import asset_first
    staged=asset_first(context)
    schema=(AssetStagePlan if staged else CoordinatedExitPlan).model_json_schema();defs=schema['$defs'];data=context['model_input']
    roles=[a['role_id'] for a in data['locked_cast']]
    for definition in defs.values():
        if 'role_id' in definition.get('properties',{}):definition['properties']['role_id']['enum']=roles
    defs['DirectedScene']['properties']['resource_scene_id']['enum']=[r['scene_id'] for r in data['scene_resources']]
    for name in ('StageBeat' if staged else 'CoordinatedExitBeat','ReferencedAnchor'):
        defs[name]['properties']['source_ref']['enum']=list(source_refs(context))
    defs['WalkInAction']['properties']['slot_id']['enum']=sorted({s['id'] for r in data['scene_resources'] for s in r['slots']})
    defs['WalkOutAction']['properties']['entrance_id']['enum']=sorted({s['id'] for r in data['scene_resources'] for s in r['entrances']})
    defs['GroupMoveAction']['properties']['followers']['items']['enum']=roles
    defs['GroupWalkOutAction']['properties']['followers']['items']['enum']=roles
    defs['GroupWalkOutAction']['properties']['entrance_id']['enum']=defs['WalkOutAction']['properties']['entrance_id']['enum']
    defs['BeatExpression']['properties']['preset']['enum']=sorted(
        {'native'}|{p for a in data['locked_cast'] for channel in ('eyes','mouth') for p in a[channel]})
    return schema


def resolve_plan(raw,context):
    if isinstance(raw,str):
        try:raw=json.loads(raw)
        except ValueError:
            # Preserve Pydantic's structured JSON diagnostics for bounded repair.
            return StructuredPlan.model_validate_json(raw)
    from .stage_policy import asset_first, StagePlan
    if isinstance(raw,dict) and raw.get('schema_version')=='spine_directing_plan/1.5':
        if not asset_first(context):fail('visual_policy','新演绎方案不能用于旧策略任务')
        staged=AssetStagePlan.model_validate(raw)
        legacy=staged.model_dump();legacy['schema_version']='spine_directing_plan/1.4'
        for beat in legacy['beats']:
            for field in ('presentation','intent','simplification'):beat.pop(field)
        normalized=resolve_plan(legacy,context).model_dump()
        normalized['schema_version']='spine_directing_plan/2.0'
        for assessment,beat in zip(normalized['assessments'],staged.beats):
            assessment.update(presentation=beat.presentation,intent=beat.intent,simplification=beat.simplification)
        return StagePlan.model_validate(normalized)
    if isinstance(raw,dict) and raw.get('schema_version') in ('spine_directing_plan/1.2','spine_directing_plan/1.3','spine_directing_plan/1.4'):
        contract={'spine_directing_plan/1.2':StructuredPlan,'spine_directing_plan/1.3':CoordinatedPlan,'spine_directing_plan/1.4':CoordinatedExitPlan}[raw['schema_version']]
        raw=contract.model_validate(raw).model_dump()
        raw['schema_version']='spine_directing_plan/1.1'
        for beat in raw['beats']:
            for action in beat['actions']:
                kind=action['kind']
                if kind in ('move','move_group'):
                    target=action.pop('target');action['target_kind']=target['kind']
                    if target['kind']=='position':action['target_x']=target['x']
                    else:action['target_id']=target['role_id'] if target['kind']=='actor' else target['id']
                elif kind=='walk_in':action.update(kind='enter',target_kind='slot',target_id=action.pop('slot_id'))
                elif kind=='walk_out':action.update(kind='exit',target_kind='entrance',target_id=action.pop('entrance_id'))
                elif kind=='walk_out_group':action.update(kind='exit_group',target_kind='entrance',target_id=action.pop('entrance_id'))
    if not isinstance(raw,dict) or raw.get('schema_version')!='spine_directing_plan/1.1':
        return DirectingPlan.model_validate(raw)
    grouped=GroupedPlan.model_validate(raw);refs=source_refs(context)
    keys=[b.source_ref for b in grouped.beats]
    if len(set(keys))!=len(keys) or set(keys)!=set(refs):
        fail('beats','每个 source_ref 必须且只能出现一次，不得遗漏、新增或重复')
    value={'scenes':[s.model_dump() for s in grouped.scenes],
           'actions':[],'expressions':[],'furniture':[],'assessments':[]}
    for beat in grouped.beats:
        source=refs[beat.source_ref]['source'];ids=[]
        for track in ('actions','expressions','furniture'):
            for i,item in enumerate(getattr(beat,track),1):
                v=item.model_dump();cid=f'{beat.source_ref}_{track[0]}{i}'
                v.update(id=cid,quote=beat.quote,anchor={'source':source,**v['anchor']})
                if track=='furniture' and v['leave_anchor']:
                    leave=v['leave_anchor'];ref=leave.pop('source_ref')
                    if ref not in refs:fail(cid,'起身 source_ref 不属于冻结原文')
                    v['leave_anchor']={'source':refs[ref]['source'],**leave}
                value[track].append(v);ids.append(cid)
        value['assessments'].append(dict(source=source,quote=beat.quote,
            classification=beat.classification,reason=beat.reason,cue_ids=ids))
    # This also enforces total cue counts and all existing canonical constraints.
    return DirectingPlan.model_validate(value)


GROUPED_INSTRUCTIONS='''本次使用 spine_directing_plan/1.4 输出协议，只返回 schema_version、scenes、beats。
每条 source_lines 提供唯一 source_ref；beats 中每个 source_ref 恰好一项，包含 quote、classification、reason 及本行 actions/expressions/furniture。
不要输出顶层 assessments/actions/expressions/furniture，不要填写指令 id、cue_ids 或每条指令的 quote/source。程序只根据本行 source_ref 绑定原文并生成唯一编号，不替你选择或修改任何演出。
每条指令仍需 role_id、reason 及对应参数。anchor 只含 operation_index、edge、offset_ms；省略 anchor 表示本行第一个操作开始。家具 leave_anchor 用 source_ref 引用起身源行。
本行分类 performed 必须至少有一条实际指令；只有对白、声音或延续上一行表情时 no_visible_action；存在未支持动作时 gap，并保留可执行的部分指令。全行 quote 必须逐字来自本行，按句意选择动作，不将表情冒充精细动作。
身体动作严格按 kind 使用不同字段，不要输出旧协议的 target_kind、target_id、target_x 或 enter/exit。
move 表示在场演员行走，target 只能是 {kind:object/slot/entrance,id:真实目录ID}、{kind:actor,role_id:人物ID} 或 {kind:position,x:明确终点}。
原文明确同行、领走或跟随时优先使用 move_group：role_id 是领走者，followers 是一至两位跟随者，target 是领走者的目的地（object/slot/entrance/position），duration_ms 是整组共同的行走时间。程序保持所有人的既有间距和左右次序，同步同速移动，不再分别填写跟随者终点或起步延迟；不能另加与这次同行重复的单人移动。
move_group 的领走者必须已经位于行进方向最前方：向右走领走者 x 最大，向左走 x 最小。请据此安排 scenes.actors 的初始站位。group 不会替你交换左右顺序、改变初始站位、截短或瞬移；所有成员须已在场，目的地和跟随者终点均须在通道内。到入口表示走近入口，不等于退场或进屋，不能借此声称已经进出房间。
多人一起退场用 walk_out_group：role_id 为靠近出口的领走者，followers 为其他同行者，entrance_id 为出口；duration_ms 是直到最后一人离场的总时间。程序计算共同速度，各人实际到达出口后才隐藏，不让后方演员因距离更长而追上前人。不要再为同一组退场编两条相同 duration_ms 的单人 walk_out，也不要猜起步延迟。若之后还需退场，前序 move_group 应停在入口附近并留出实际行走距离，不要先停在入口点上再原地退场。
actor 目标是一次静态靠近：终点取目标人物在本动作开始时的 x，再从近侧偏移 .18；它不会追踪正在移动的目标。目标正在移动时不要用 actor 表示跟随。
walk_in 是从最近入口走入 slot_id 指定的站位，仅用于初始隐藏角色；不能写 target 或坐标，也不能用它在入口处原地显现。多人从同一入口同时走入会重叠，应合理错开时间并检查整条路径。
walk_out 是走到 entrance_id 后隐藏，只填入口ID。face 只填 facing（-1向左、1向右），不是瞬移。idle/sit/pick/lie/victory/victoryloop 只使用角色真正支持的动作。sit 是原生坐姿，不能冒充已贴合家具；pick 是被提起或悬挂，不能冒充拿取物品；lie 是躺卧，不能冒充与床的精确互动。没有独立面部的角色只保留 native 表情。
新场开始时已在镜头内的人应 visible=true；原文只是继续走时不需要额外入场。优先安排正确初始在场、前后顺序和朝向，再安排有位移的行走；不要添加无依据的出入场。
每一场都会重置为该场 scenes.actors 的初始状态，绝不继承上一场的移动终点或 visible。下一场设置 visible=false 且没有本场 walk_in 时，该演员始终不在场，不能执行 face、身体动作或表情。不能以“前序移动已实现”为由省略本场必要状态。
输入已列出每个声音操作的 duration_ms 和 remaining_scene_ms，所有时间都使用毫秒。operation_index 从0开始，不能为负。表情应短于本行可用时间且不与同演员同通道重叠，无需每行都安排表情。
locked_cast.actions 的 duration_ms 也是毫秒；playback=once 的动作必须安排至少该时长，并能完整放进当前场次剩余时间。若一句对白或场尾只剩1760ms，而 victory 素材需要1767ms，不能安排1760ms的 victory；原文仅为开心台词时可选择有依据的表情，不必额外强加无法完成的身体动作。
repair_feedback.preflight_diagnostics 是诊断信息，不是可交付方案。provisional=true 的条目来自隔离先前错误指令后的检查，需要结合完整原方案重新核对；excluded_before 只说明诊断隔离，不代表允许删除原文动作。修复后必须完整通过全部检查。
同行先安排两人完整起终点，使二人同向运动且持续保持间距。向左走时领先者 x 更小，向右走时领先者 x 更大；全程不能交换左右顺序，间距至少 .10。明确位置仍按 duration_ms 行走，终点和路径必须在 walk_lane 内。
场景或角色素材不存在的提灯、木牌等道具不能只在 reason 中虚构；举灯、拿持物品也不是 walk 动画的能力，须明确 gap，同时保留可执行的走位。
纯对白或音效中明确存在的关键物件、状态也要核对可见覆盖。例如“是木牌”“钉子松了”在无木牌/钉子素材时仍应 gap，即使添加了表情；“灯亮着”缺灯盏时也应 gap。“脚步从屋外走回屋内”不是没有可见动作，须有对应走位或准确报告跨门、外景缺口。gap 行仍可保留已实现的动作、表情，不能删除可执行内容来逃避检查。
输出前逐行核对所提及的具体可见材质、地面与接触状态，不能因为它出现在声音描述中就忽略。例如“踩在泥土和碎石上”以及“脚步从屋外走回屋内，质感从碎石变为闷响”都应检查本场是否真的显示泥土/碎石；没有时各自标 gap，并保留有能力执行的行走。场景 difference 已承认缺少的道具或地面，在所有明确提及该细节的源行都应一致标 gap，不能只修审查点名的一行。
修复时先逐条保留已通过的有效动作及参数；只调整具体问题及受它影响的连续状态。修复仅涉及缺失物件/材质的 classification 和 reason 时，不删除已有有效走位，不重排无关动作与时长。'''
