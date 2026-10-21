"""源剧本与手机协议分开：先保留来源，再做白名单字段映射。"""
from copy import deepcopy
from .script_models import VisualScript
from .errors import VisualError
from .asset_catalog import ACTIONS


def normalize_script(script: VisualScript, target_profile: str, automatic=False):
    script = script.model_copy(deep=True)
    source = script.model_dump(mode='json')
    issues = []
    if automatic:
        # 使用呈现默认值，原始剧本仍由入口另存；不声称默认值来自剧情。
        from .script_models import Appearance
        for actor in script.characters:
            if actor.appearance is None:
                actor.appearance = Appearance(template_type='adult', color_key='blue', accessory='none')
                issues.append({'code':'DEFAULT_APPEARANCE','role_id':actor.role_id,'message':'已使用默认外观，可在剧情编辑端修改'})
        if script.excerpt and not script.continuity_confirmed:
            issues.append({'code':'EXCERPT_PRESENTATION','message':'以独立节选展示，不承诺完整剧情衔接'})
    missing, warnings, degradations = [], [], []
    for i, actor in enumerate(script.characters):
        if actor.visual_requirements:
            raise VisualError('UNSUPPORTED_VISUAL_REQUIREMENT',
                '当前基础模板无法验证指定外观特征，请选择支持的素材包或明确修改源要求',
                f'script.characters.{i}.visual_requirements')
        if actor.appearance is None:
            missing.append({'code':'MISSING_APPEARANCE', 'field_path':f'characters.{actor.role_id}.appearance',
                'message':f'请为{actor.name}明确选择基础人物外观', 'role_id':actor.role_id})
    if script.excerpt and not script.continuity_confirmed and not automatic:
        missing.append({'code':'UNCONFIRMED_CONTINUITY', 'field_path':'continuity_confirmed',
                        'message':'当前为节选，请确认这些场景允许按给定顺序连续展示'})
    scenes = []
    for si, scene in enumerate(script.scenes):
        converted = []
        for bi, block in enumerate(scene.blocks):
            path = f'script.scenes.{si}.blocks.{bi}'
            narrate = block.type == 'action' and block.action not in ACTIONS and script.constraints.allow_narration_fallback
            if block.participant_role_ids and not narrate:
                raise VisualError('UNSUPPORTED_CAPABILITY', '当前目标不支持多人同步动作，不能静默拆分关键事件', path)
            if block.type == 'action' and block.action not in ACTIONS and not narrate:
                raise VisualError('UNSUPPORTED_ACTION', f'{target_profile}不支持动作：{block.action}', path+'.action')
            if block.type == 'action' and block.target_role_id is not None and block.action != 'face' and not narrate:
                raise VisualError('UNSUPPORTED_CAPABILITY', '当前动作不能表达对象关系，请提供支持的明确行动', path+'.target_role_id')
            if block.timing:
                if block.timing.verified:
                    raise VisualError('UNSUPPORTED_TIMING', '当前目标仅支持视觉估算，无法执行已核实的固定源时间轴', path+'.timing')
                warnings.append({'code':'SOURCE_TIMING_UNVERIFIED', 'field_path':path+'.timing',
                    'block_id':block.block_id, 'message':'未核实的源时间只保留溯源，不作为已锁定播放时间'})
            # 原始行动说明可以保存，但不解释其中的自然语言，也不将声音提示当作字幕。
            fields = block.model_dump(exclude_none=True, exclude={'participant_role_ids','timing'})
            if narrate:
                # 仅改变呈现方式，不推断动作结果或增添剧情。行动说明逐字显示，人物和对象留在溯源记录。
                # 动作时长不是阅读时长，因此交给原字幕规则重新估算；固定源时间仍在上方拒绝。
                text = block.text
                # 只分离明确的编辑备注后缀，不自动推断其中缺失的故事事实。
                if automatic and '尚未确认' in text:
                    parts = text.rsplit('，', 1)
                    if len(parts) == 2 and '尚未确认' in parts[1]:
                        text = parts[0]
                        issues.append({'code':'EDITOR_NOTE_REMOVED','block_id':block.block_id,
                            'message':parts[1], 'original_text':block.text})
                fields = {'block_id':block.block_id, 'type':'narration', 'text':text}
                if block.source_ref is not None:
                    fields['source_ref'] = block.source_ref
                degradations.append({'scene_id':scene.scene_id, 'block_id':block.block_id,
                    'source_event':block.model_dump(mode='json', exclude_none=True),
                    'output_type':'narration', 'target_profile':target_profile,
                    'duration_policy':'reestimate_for_reading', 'authorization':'allow_narration_fallback'})
                warnings.append({'code':'ACTION_NARRATION_FALLBACK', 'field_path':path,
                    'block_id':block.block_id, 'message':f'动作 {block.action} 使用原行动说明作为旁白字幕，时长按阅读规则重新估算'})
            converted.append(fields)
        initial = scene.initial_cast
        if initial is None:
            entering = {b.role_id for b in scene.blocks if b.action == 'enter'}
            initial = [r for r in scene.cast if r not in entering]
        scenes.append({'scene_id':scene.scene_id, 'name':scene.name,
            'background_type':scene.background_type, 'cast':scene.cast,
            'initial_hidden_roles':[r for r in scene.cast if r not in initial], 'blocks':converted})
    if missing:
        return {'document':None, 'source_snapshot':source, 'missing_requirements':missing, 'warnings':warnings, 'degradations':degradations, 'generation_issues':issues}
    document = {'schema_version':'2.0.0', 'document_id':script.document_id,
        'source_version_id':script.source_version_id, 'revision':script.revision, 'title':script.title,
        'timebase':1000, 'timing_mode':'visual_estimated', 'style_pack_id':'basic_pixel',
        'style_pack_version':'1.0.0', 'subtitle_enabled':True,
        'characters':[{'role_id':a.role_id, 'name':a.name, **a.appearance.model_dump()} for a in script.characters],
        'scenes':scenes}
    return {'document':document, 'source_snapshot':source, 'missing_requirements':[], 'warnings':warnings, 'degradations':degradations, 'generation_issues':issues}

def apply_context_answers(request, answers, requirements):
    """只开放本次缺项对应的回答；不能借确认修改台词、ID 或剧情结构。"""
    result = deepcopy(request)
    required = {r['field_path'] for r in requirements}
    if not isinstance(answers, dict) or set(answers) != required:
        raise VisualError('INCOMPLETE_CONTEXT', '必须逐项回答当前缺项，不能跳过或添加修改')
    script = result['script']
    for key, value in answers.items():
        if key == 'continuity_confirmed':
            if value is not True:
                raise VisualError('INCOMPLETE_CONTEXT', '节选连续性尚未确认', key)
            script[key] = value
        else:
            role_id = key.split('.')[1]
            actor = next(a for a in script['characters'] if a['role_id'] == role_id)
            actor['appearance'] = value
    # 重新校验回答本身，不让未知外观字段绕过模型。
    result['script'] = VisualScript.model_validate(script).model_dump(mode='json')
    return result
