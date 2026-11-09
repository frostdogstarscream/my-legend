"""Immutable, inspectable capability snapshots built from verified runtime catalogs."""
from copy import deepcopy
import json
import math
from pathlib import Path
from .life_contracts import canonical_hash, action_durations
from .repository import ProductionError

ACTION_NOTES = {
    'idle': '自然待机、倾听、停顿；不能冒充操作物件。',
    'walk': '行走与走位，运行时自动朝向行进方向；可表达靠近、同行、探查。',
    'pick': '宿舍原生被提起或悬挂姿态；不是伸手拿取物品，不能替代道具交互。',
    'lie': '宿舍原生躺卧姿态；不表示已经贴合床或沙发，家具需要另行校准。',
    'sit': '坐姿，仅能使用本场已校准且兼容当前服装的座位。',
    'victory': '一次性庆祝动作，必须完整播放；用于明确喜悦或成功，不能替代精细手部交互。',
    'victoryloop': '持续庆祝姿态，仅在剧情情绪合适时使用，避免滥用。',
}
EYE_NOTES = {'open': '常态睁眼，关注与倾听', 'closed': '闭合弧线眼，适合轻松停顿；不自动表示悲伤', 'smile': '弯眼笑，友善与明显喜悦',
             'squint': '用力挤眼（大于小于号形），适合窘迫或强烈反应；不是普通凝神', 'surprised': '空心圆眼，意外、吃惊或愣住'}
SCENE_MOODS = {
    'loft':'明亮现代居室、木地板、窗与会客家具，适合日常交谈；不是写实深夜。',
    'cafe':'暖棕木质咖啡吧、吧台与桌椅，适合会面与倾听。',
    'halloween':'暗色石墙、哥特窗与节庆装饰，适合神秘、探查；带明显万圣节元素。',
    'cafe_classic':'砖墙咖啡店、桌椅与吧台，适合日常会面。',
    'library':'棕色书架、壁炉、阅读座位，适合寻书、调查与安静交谈。',
    'classroom':'明亮教室、黑板、课桌与储物柜，适合讲述与问答。',
    'nanyang':'绿植窗景和暖色餐饮空间，适合休闲会面。',
    'convenience':'明亮货架和冷柜，适合购物与偶遇。',
    'spring_festival':'夜色天空、灯笼与节庆摊位，适合夜间户外舞台化；有明显春节氛围。',
    'museum':'展柜、恐龙骨架与护栏，适合参观、发现和讲解。',
    'seaside':'蓝色海景窗、吧台和休闲椅，适合轻松日常。',
    'izakaya':'夜色背景与暖光日式摊位，适合夜间会面；不是普通房屋。',
    'wedding':'白昼草地、花拱和宾客座位，适合仪式与团聚。',
    'command':'深色室内、地图屏幕和设备，适合简报与讨论。',
    'snow_cabin':'雪地、雪花墙面与暖色壁炉，适合冬日温暖氛围；装饰性强。',
}
OBJECT_NAMES = {'wall':'背景墙','floor':'地面','sofa':'沙发','chair':'椅子','table':'桌子','desk':'桌子',
                'bookcase':'书架','fireplace':'壁炉','counter':'吧台','bed':'床','window':'窗景',
                'blackboard':'黑板','cabinet':'柜子','bench':'长凳','lamp':'灯饰',
                'antlers':'鹿角装饰','arch':'拱门','armor':'盔甲陈列','art':'装饰画','bar':'吧台',
                'barrier':'护栏','bicycle':'自行车陈设','boards':'告示板','cart':'推车陈设',
                'case_left':'左侧展柜','case_right':'右侧展柜','chairs_left':'左侧座椅','chairs_right':'右侧座椅',
                'chandelier':'吊灯','church':'教堂布景','desks':'课桌','dining':'餐桌陈设','entry':'入口布景',
                'equipment':'设备陈设','fossil':'化石骨架','fridge':'冷柜','front_deco':'前景装饰',
                'header_left':'左侧上方装饰','header_right':'右侧上方装饰','interior':'室内布景',
                'lanterns':'灯笼装饰','lectern':'讲台','lockers':'储物柜','machine':'机器陈设','map':'地图',
                'menu':'菜单牌','notice':'公告栏','ornament':'装饰物','palm':'棕榈植物','pond':'水池布景',
                'portrait':'肖像画','poster':'海报','register':'收银台','screen':'显示屏','seal':'标志装饰',
                'seat':'座椅','server':'服务器陈设','shelf':'货架','shelf_left':'左侧架子','shelf_right':'右侧架子',
                'sign':'招牌','speaker':'音箱','stall_left':'左侧摊位','stall_right':'右侧摊位','stool':'凳子',
                'tea':'茶具陈设','tree':'树木装饰','tv':'电视','vending':'售货机','whale':'鲸鱼装饰'}


def build_capabilities(catalog, rooms):
    if rooms.get('commander_catalog_sha256') != catalog['catalog_sha256']:
        raise ProductionError('DIRECTING_SCENE_CATALOG_MISMATCH')
    actors=[]
    for actor in catalog['actors']:
        outfits=[]
        for outfit in catalog['outfits']:
            if outfit['actor_id'] != actor['actor_id']:continue
            durations=action_durations(actor,catalog,outfit['outfit_id'])
            outfits.append(dict(outfit_id=outfit['outfit_id'],name=outfit.get('display_name',outfit['outfit_id']),
                revision=outfit.get('revision',canonical_hash(outfit)),calibration_group=outfit.get('calibration_group'),
                actions={k:dict(duration_ms=math.ceil(v*1000),playback='once' if k=='victory' else 'loop',
                    description=('宿舍原生坐姿，不表示已贴合椅子或沙发。' if k=='sit' and actor.get('asset_mode')=='single_skeleton' else ACTION_NOTES.get(k,'已解析动画；语义需预览核对。'))) for k,v in durations.items()}))
        actors.append(dict(actor_id=actor['actor_id'],name=actor.get('display_name',actor['actor_id']),
            revision=actor['revision'],status='ready',outfits=outfits,
            eyes={k:EYE_NOTES.get(k,k) for k in actor.get('face_profile',{}).get('eyes',{})},
            mouth=dict(mode='automatic_dialogue' if actor.get('face_profile') else 'native',presets=['native','open','closed'] if actor.get('face_profile') else ['native'],
                       compatible_actions=actor.get('face_profile',{}).get('mouth',{}).get('actions',[]))))
    scenes=[]
    for room in rooms['scenes']:
        targets=room.get('blocking',{}).get('targets',[]);seats=room.get('commander_seats',[])
        objects=[]
        for obj in room['objects']:
            label=next((t.get('label') for t in targets if t.get('object_id')==obj['id']),None)
            objects.append(dict(id=obj['id'],name=label or OBJECT_NAMES.get(obj['id'],obj.get('source_name',obj['id'])),
                image=obj.get('image'),position=obj.get('position'),layer=obj.get('layer'),
                capability='calibrated_seat' if any(s['object_id']==obj['id'] for s in seats) else 'static_decoration',
                manipulation_supported=False))
        scenes.append(dict(scene_id=room['scene_id'],name=room['display_name'],description=room.get('description',''),
            appearance=SCENE_MOODS.get(room['scene_id'],'以原目录空间说明为依据；未做独立外观语义标注。'),
            status='ready' if room.get('blocking') else 'needs_adaptation',objects=objects,
            blocking=deepcopy(room.get('blocking')),seats=deepcopy(seats),
            limitations='布景及物件为已有图像；只有列出的座位可坐，不能开关、拿取、移动物件或改变灯光。'))
    value=dict(schema_version='stage_capabilities/1',character_catalog_sha256=catalog['catalog_sha256'],
        scene_catalog_sha256=rooms['catalog_sha256'],actors=actors,scenes=scenes,
        evidence=dict(kind='parsed_runtime_catalog',visual_acceptance='separate_preview_required'),
        policy='现有能力优先；简化非阻塞；禁止虚构资源和交互。')
    return {**value,'capability_sha256':canonical_hash(value)}


def save_capabilities(root,value):
    target=Path(root)/'capability-catalogs'/(value['capability_sha256']+'.json')
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if json.loads(target.read_text('utf-8'))!=value:raise ProductionError('CAPABILITY_HASH_MISMATCH')
    else:target.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    return value['capability_sha256']


def load_capabilities(root,digest,catalog,rooms):
    import re
    if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ProductionError('CAPABILITY_NOT_FOUND')
    try:value=json.loads((Path(root)/'capability-catalogs'/(digest+'.json')).read_text('utf-8'))
    except (OSError,ValueError):raise ProductionError('CAPABILITY_NOT_FOUND') from None
    unhashed={k:v for k,v in value.items() if k!='capability_sha256'}
    if (value.get('schema_version')!='stage_capabilities/1' or canonical_hash(unhashed)!=digest
            or value.get('capability_sha256')!=digest or value.get('character_catalog_sha256')!=catalog['catalog_sha256']
            or value.get('scene_catalog_sha256')!=rooms['catalog_sha256']):raise ProductionError('CAPABILITY_HASH_MISMATCH')
    return value


def enrich_context(value,capabilities):
    value.update(visual_policy='asset_stage/1',capability_sha256=capabilities['capability_sha256'],capabilities=capabilities)
    data=value['model_input'];data['visual_policy']='asset_stage/1'
    for cast in data['locked_cast']:
        actor=next(a for a in capabilities['actors'] if a['actor_id']==cast['actor_id'])
        outfit=next(o for o in actor['outfits'] if o['outfit_id']==cast['initial_outfit_id'])
        cast['capabilities']=dict(actions=outfit['actions'],eyes=actor['eyes'],mouth=actor['mouth'])
    for room in data['scene_resources']:
        source=next(s for s in capabilities['scenes'] if s['scene_id']==room['scene_id'])
        room.update(description=source['description'],appearance=source.get('appearance',''),limitations=source['limitations'],
                    objects=[{k:v for k,v in o.items() if k!='image'} for o in source['objects']])
        compatible=[]
        for seat in source['seats']:
            roles=[]
            for cast in data['locked_cast']:
                actor=next(a for a in capabilities['actors'] if a['actor_id']==cast['actor_id'])
                outfit=next(o for o in actor['outfits'] if o['outfit_id']==cast['initial_outfit_id'])
                if outfit['calibration_group'] in seat.get('calibrated_groups',[]):roles.append(cast['role_id'])
            compatible.append({**next(s for s in room['seats'] if s['seat_id']==seat['seat_id']),
                               'label':seat.get('label',seat['seat_id']),'compatible_role_ids':roles})
        room['seats']=compatible
    return value
