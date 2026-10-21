"""只读取项目内已登记素材，分别声明网页与手机的真实目标能力。"""
import hashlib
import json
from pathlib import Path
from .errors import VisualError

DEFAULT_ASSETS = Path(__file__).resolve().parents[2] / 'pixel-story-mobile/packages/story_player/assets'
ACTIONS = ('enter','exit','move','face','wait','nod','shake_head','react')
PROFILES = {
    'mobile_current_v2': {'runtime_schema':'2.0.0', 'render_mode':'flame_realtime',
                          'final_text_layout':'flutter_actual_font', 'native_host':'flutter'},
    'web_basic_v1': {'runtime_schema':'2.0.0', 'render_mode':'sampled_canvas_100ms',
                     'final_text_layout':'fixed_72_preview', 'native_host':None},
}

def target_capabilities(profile):
    if profile not in PROFILES:
        raise VisualError('UNSUPPORTED_TARGET', f'目标尚未实现：{profile}', 'target_profile')
    return {**PROFILES[profile], 'target_profile':profile, 'actions':list(ACTIONS),
        'supports_fixed_timing':False, 'supports_battle':False, 'supports_props':False,
        'supports_mp4':False, 'max_characters':6, 'max_scene_cast':4,
        'max_scenes':2, 'max_events':100, 'max_duration_ms':90000}

def load_catalog(asset_root=None, pack_id='basic_pixel', version='1.0.0'):
    # pack_id/version 不能拼接任意路径；未知素材包直接拒绝。
    if (pack_id, version) != ('basic_pixel', '1.0.0'):
        raise VisualError('UNSUPPORTED_ASSET_PACK', '当前仅登记 basic_pixel/1.0.0', 'asset_pack_id')
    root = Path(asset_root) if asset_root is not None else DEFAULT_ASSETS
    files = ['basic_pixel/1.0.0/actors.png', 'basic_pixel/1.0.0/registry.json',
             'fonts/StorySansSC.ttf', 'fonts/OFL.txt', 'ASSET-LICENSES.md']
    hashes = {}
    for name in files:
        path = root / name
        if not path.is_file():
            raise VisualError('MISSING_ASSET', f'缺失素材：{name}', name)
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        registry = json.loads((root / files[1]).read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise VisualError('INVALID_ASSET_CATALOG', '素材目录不是有效 JSON', files[1]) from exc
    expected = {'pack_id':pack_id, 'version':version, 'actor_atlas':'actors.png',
        'logical_size':[320,180], 'frame_size':[32,32], 'atlas_columns':16,
        'foot_anchor':[16,30], 'frames_per_direction':2,
        'templates':['adult','elder','child','animal'], 'colors':['blue','green','red','brown'],
        'accessories':['none','hat','beard'], 'directions':['down','left','right','up'],
        'backgrounds':['indoor','courtyard','outdoor']}
    if not isinstance(registry, dict) or any(registry.get(k) != v for k,v in expected.items()):
        raise VisualError('INVALID_ASSET_CATALOG', '素材目录与播放器图集契约不一致', files[1])
    if not isinstance(registry.get('license'), str) or not registry['license'].strip():
        raise VisualError('MISSING_LICENSE', '素材目录缺少许可声明', files[1]+'.license')
    return {'pack_id':pack_id, 'version':version, 'license':registry['license'],
        'file_hashes':hashes, 'registry':registry,
        'assets':[{'asset_id':f'{pack_id}/{version}/{name}', 'file':name,
                   'sha256':hashes[name]} for name in files]}
