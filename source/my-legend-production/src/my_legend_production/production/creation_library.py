"""Selectable story identities backed by the verified stage wardrobe."""
from .h5_preview import load_h5_catalog
from .spine_preview import local_root, public_catalog
from .commander_preview import commander_scene_digest
from .scene_preview import load_scene_catalog


def wardrobe_characters(catalog):
    actors = {a['actor_id']: a for a in catalog['actors']}
    result = []
    for outfit in catalog['outfits']:
        actor = actors.get(outfit['actor_id'])
        if not actor or outfit['outfit_id'] not in actor['outfit_ids']:
            continue
        name = outfit['display_name']
        theme = outfit.get('style_category') or ('国风' if outfit['outfit_id'].startswith('gufeng-') else '现代')
        result.append(dict(id='wardrobe-'+outfit['outfit_id'], name=name,
            description='给这个角色一个名字，再为他写一段故事。',
            biography=(f'让{name}走进你想象中的世界。' if actor.get('asset_mode')=='single_skeleton' else f'穿上{name.split(" · " )[0]}，走进你想象中的世界。')+'性格、经历和相遇，都由你来写。',
            avatarUrl='', thumbnail=outfit.get('thumbnail') or actor.get('thumbnail'), owned=True,
            tags=[theme], voiceDescription='生成时自动绑定声音',
            stageBinding=dict(catalog_sha256=catalog['catalog_sha256'], actor_id=actor['actor_id'],outfit_id=outfit['outfit_id'])))
    return result


def creation_library():
    root=local_root();catalog=load_h5_catalog(root)
    scenes=load_scene_catalog(root,commander_scene_digest(root,catalog))
    return dict(characters=wardrobe_characters(catalog),catalog_sha256=catalog['catalog_sha256'],
                scenes=public_catalog(scenes))


def with_scene_choices(source, casting):
    """The story model chooses locations from the frozen renderable scene names."""
    choices=(casting or {}).get('scene_choices', [])
    if not choices or source.get('kind') not in ('creative','operation'):
        return source
    field='idea' if source['kind']=='creative' else 'instruction'
    note=('\n【可用舞台场景】'+ '、'.join(c['name'] for c in choices)+
          '。请根据故事需要自动选择背景；每个分场的 location 使用上述场景名称之一。'
          '同一地点连续对话可保持同一背景，允许不同分场选择不同背景。场景自带的家具与装饰随背景展示。')
    return {**source,field:source[field]+note}
