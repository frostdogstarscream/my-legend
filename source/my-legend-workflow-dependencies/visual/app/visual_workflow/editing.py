"""轻微修改生成新源修订；旧批准不继承，新版本必须重新校验和审核。"""
from copy import deepcopy
from .asset_catalog import ACTIONS
from .planner import VisualError, digest

def revise(request, changes, parent_visual_version):
    if not parent_visual_version:
        raise VisualError('VERSION_CONFLICT', '修改需要关联原视觉版本')
    result = deepcopy(request)
    # 统一稿使用 description/约束等来源信息，不能在编辑时降成裸播放器 JSON。
    unified = result.get('script') is not None
    doc = result['script'] if unified else result['document']
    # 只开放现有编辑器支持的字段，拒绝 ID、顺序和入退场结构变更。
    allowed = {'character': {'color_key', 'accessory', 'template_type'},
               'scene': {'background_type'}, 'block': {'text', 'emotion', 'action'}}
    for change in changes:
        if set(change) != {'kind', 'id', 'fields'} or change['kind'] not in allowed:
            raise VisualError('INVALID_EDIT', '未知修改类型或字段')
        kind = change['kind']; fields = change['fields']
        if not fields or not set(fields) <= allowed[kind]:
            raise VisualError('INVALID_EDIT', '修改超出白名单')
        collection = doc['characters'] if kind == 'character' else doc['scenes'] if kind == 'scene' else [b for s in doc['scenes'] for b in s['blocks']]
        key = {'character':'role_id', 'scene':'scene_id', 'block':'block_id'}[kind]
        item = next((x for x in collection if x[key] == change['id']), None)
        if item is None:
            raise VisualError('INVALID_EDIT', '修改对象不存在', change['id'])
        if kind == 'block':
            # 已按旁白呈现的行动说明可编辑，仍保留原动作和事件身份供新版本溯源。
            narrated = unified and doc['constraints'].get('allow_narration_fallback', True) and item['type'] == 'action' and item.get('action') not in ACTIONS
            if 'text' in fields and item['type'] not in ('dialogue', 'narration') and not narrated:
                raise VisualError('INVALID_EDIT', '只能修改对白和旁白文本')
            if 'emotion' in fields and not (item['type'] == 'dialogue' or item.get('action') == 'react'):
                raise VisualError('INVALID_EDIT', '该块不支持情绪修改')
            if 'action' in fields and (item.get('action') not in ('nod','shake_head','react') or fields['action'] not in ('nod','shake_head','react')):
                raise VisualError('INVALID_EDIT', '只能替换已有简单反应')
            if 'action' in fields:
                item.pop('emotion', None); item.pop('intensity', None)
            if 'text' in fields:
                item.pop('duration_ms', None)
        if kind == 'character' and unified:
            if item.get('appearance') is None:
                raise VisualError('INVALID_EDIT', '人物尚未选择外观')
            item['appearance'].update(fields)
        else:
            item.update(fields)
        if kind == 'block' and unified:
            item.pop('timing', None)
    doc['revision'] = doc.get('revision', 1) + 1
    doc['source_version_id'] = 'edit_' + digest(doc)[:24]
    result['request_id'] = 'edit_' + digest(result)[:24]
    result['parent_visual_version'] = parent_visual_version
    return result
