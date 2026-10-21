"""统一入口可独立校验，也可被 LangGraph 调用；结构化路径不依赖 AI。"""
from copy import deepcopy
from pydantic import ValidationError
from .contracts import Request
from .asset_catalog import load_catalog
from .source_adapter import normalize_script
from .planner import VisualError, compile_document, digest

def prepare_request(raw_request):
    try:
        request = Request.model_validate(raw_request)
        if request.presentation_profile == 'standing_dialogue_v1':
            # 旁白必须由剧本端交付成稿；不能在生成时把行动说明改写为剧情。
            if request.script is None:
                raise VisualError('INVALID_INPUT', '立绘模板需要统一结构化 script', 'script')
            if request.script.constraints.allow_narration_fallback:
                raise VisualError('INVALID_INPUT', '立绘模板必须关闭自动动作转旁白，请直接提供 narration',
                                  'script.constraints.allow_narration_fallback')
            for si, scene in enumerate(request.script.scenes):
                for bi, block in enumerate(scene.blocks):
                    if block.type == 'action' and block.action not in ('enter', 'exit', 'move', 'wait'):
                        raise VisualError('SCRIPT_NARRATION_REQUIRED',
                            '仅支持立绘平移或等待；请由剧本端将该动作写为 narration 成稿',
                            f'script.scenes.{si}.blocks.{bi}')
        load_catalog(pack_id=request.asset_pack_id, version=request.asset_pack_version)
        normalized = (normalize_script(request.script, request.target_profile, request.generation_mode == 'automatic') if request.script else
            {'document':request.document, 'source_snapshot':request.document,
             'missing_requirements':[], 'warnings':[]})
        return {'request':request.model_dump(mode='json', exclude_none=True),
            'normalized_script':normalized['document'],
            'source_snapshot':deepcopy(raw_request['script'] if request.script else raw_request['document']),
            'missing_requirements':normalized['missing_requirements'],
            'source_warnings':normalized['warnings'], 'degradations':normalized.get('degradations', []), 'generation_issues':normalized.get('generation_issues', [])}
    except ValidationError as exc:
        first = exc.errors()[0]
        raise VisualError('INVALID_INPUT', first['msg'],
            '.'.join(map(str,first['loc'])) or 'request') from exc

def compile_prepared(prepared):
    if prepared['missing_requirements']:
        raise VisualError('INCOMPLETE_CONTEXT', '需要先补充输入缺项，不能直接编排')
    request = prepared['request']
    plan = compile_document(prepared['normalized_script'], request['target_profile'],
        request.get('asset_pack_id','basic_pixel'), request.get('asset_pack_version','1.0.0'))
    budget = request.get('script', {}).get('target_duration_ms')
    if budget is not None and plan['manifest']['duration_ms'] > budget:
        raise VisualError('TIMING_BUDGET_EXCEEDED',
            f"预计 {plan['manifest']['duration_ms']}ms，超出 {budget}ms 预算；请调整预算或人工修改源稿",
            'script.target_duration_ms')
    # 降级记录参与计划哈希，避免旧审核结果批准不同的表现方案。
    plan['presentation_policy_version'] = 'basic_narration/1.0'
    plan['presentation_profile'] = request.get('presentation_profile', 'legacy_basic')
    if plan['presentation_profile'] == 'standing_dialogue_v1':
        plan['target_capabilities']['actions'] = ['enter', 'exit', 'move', 'wait']
        plan['target_capabilities']['presentation_profile'] = 'standing_dialogue_v1'
    plan['generation_issues'] = prepared.get('generation_issues', [])
    if request.get('preview_skin') in ('fogport_v1', 'fogport_v2', 'fogport_painted_v1'):
        from pathlib import Path
        import hashlib
        # 素材路径来自登记的版本，不接受请求中的任意路径；旧版本继续绑定旧人物。
        character_pack = 'fogport_painted_v1' if request['preview_skin'] == 'fogport_painted_v1' else 'fogport_v1'
        asset = Path(__file__).parent / 'assets' / character_pack / 'characters.png'
        if not asset.is_file():
            raise VisualError('MISSING_ASSET', '生成的人物素材不存在')
        plan['preview_skin'] = {'id':request['preview_skin'], 'sha256':hashlib.sha256(asset.read_bytes()).hexdigest(),
            'roles':['shenyan','sutang','laoqin'], 'animation':'single_pose_transform', 'mobile_supported':False}
        if request.get('presentation_profile') == 'standing_dialogue_v1' and request['preview_skin'] == 'fogport_painted_v1':
            # 当前示例复用已登记的三格素材，按人物表绑定中性 ID；不根据名字生成或猜外观。
            roles = [actor['role_id'] for actor in plan['document']['characters']]
            if len(roles) > 3:
                raise VisualError('UNSUPPORTED_ASSET_CAPACITY', '当前示例人物图集最多绑定三个人物', 'script.characters')
            plan['preview_skin']['roles'] = roles
            plan['preview_skin']['binding_basis'] = 'character_array_order'
            plan['target_capabilities']['max_characters'] = 3
            plan['target_capabilities']['max_scene_cast'] = 3
    if request.get('preview_skin') in ('fogport_v2', 'fogport_painted_v1'):
        background = Path(__file__).parent / 'assets' / request['preview_skin'] / 'background.png'
        if not background.is_file():
            raise VisualError('MISSING_ASSET', '新版电台背景不存在')
        plan['preview_skin']['background_sha256'] = hashlib.sha256(background.read_bytes()).hexdigest()
        painted = request['preview_skin'] == 'fogport_painted_v1'
        plan['preview_skin']['layout'] = {'foot_offset_y':36, 'sprite_height':84 if painted else 64}
        plan['preview_skin']['art_style'] = 'painted' if painted else 'pixel'
    plan['generation_mode'] = request.get('generation_mode', 'reviewed')
    plan['degradations'] = prepared.get('degradations', [])
    plan['source_snapshot'] = prepared['source_snapshot']
    plan['source_hash'] = digest(plan['source_snapshot'])
    plan['resolved_source_snapshot'] = request.get('script', prepared['normalized_script'])
    plan['validation_report']['warnings'].extend(prepared['source_warnings'])
    plan['plan_hash'] = digest({k:v for k,v in plan.items() if k not in ('plan_hash','frames')})
    return plan
