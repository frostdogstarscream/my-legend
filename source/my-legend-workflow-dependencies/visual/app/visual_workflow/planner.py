"""结构化输入 → 固定素材绑定 → 已有规则编排 → 可追踪计划。"""
import hashlib
import json
from pathlib import Path
import subprocess
from .errors import VisualError
from .asset_catalog import load_catalog, target_capabilities

ROOT = Path(__file__).resolve().parents[2]
MOBILE = ROOT / 'pixel-story-mobile'
ASSETS = MOBILE / 'packages/story_player/assets'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()

def compile_document(document, target_profile='mobile_current_v2', pack_id='basic_pixel', pack_version='1.0.0'):
    """上游 JSON 只能作为 stdin 数据传入固定程序，不能指定脚本或资源路径。"""
    capabilities = target_capabilities(target_profile)
    catalog = load_catalog(ASSETS, pack_id, pack_version)
    raw = json.dumps(document, ensure_ascii=False)
    if len(raw.encode()) > 1024 * 1024:
        raise VisualError('CAPACITY_EXCEEDED', '剧情 JSON 超过 1MiB')
    dart = Path(__import__('os').environ['MY_LEGEND_DART']) if __import__('os').environ.get('MY_LEGEND_DART') else MOBILE / '.tooling/flutter/bin/cache/dart-sdk/bin/dart'
    packages = Path(__import__('os').environ['MY_LEGEND_DART_PACKAGES']) if __import__('os').environ.get('MY_LEGEND_DART_PACKAGES') else MOBILE / 'packages/story_player/.dart_tool/package_config.json'
    # 只重试一次启动/超时故障；内容错误不重试，避免重复编排掩盖非法输入。
    for attempt in range(2):
        try:
            result = subprocess.run([str(dart), f'--packages={packages}',
                str(Path(__file__).with_name('mobile_bridge.dart'))],
                input=raw, text=True, capture_output=True, timeout=30, check=True)
            break
        except (OSError, subprocess.TimeoutExpired) as exc:
            if attempt == 1:
                raise VisualError('COMPILER_UNAVAILABLE', f'Dart 编排器不可用：{exc}') from exc
        except subprocess.CalledProcessError as exc:
            raise VisualError('COMPILER_FAILED', 'Dart 编排器执行失败，请检查工具链') from exc
    data = json.loads(result.stdout)
    if not data['ok']:
        raise VisualError(**data['error'])
    registry = catalog['registry']
    hashes = catalog['file_hashes']
    normalized = data['document']
    # 源事件一一对应，不新增对白或关键动作；manifest 的时间是视觉估算。
    coverage = {b['block_id']: [b['block_id']] for s in normalized['scenes'] for b in s['blocks']}
    bindings = {'pack_id': registry['pack_id'], 'version': registry['version'],
        'license': registry['license'], 'file_hashes': hashes,
        'characters': {a['role_id']: {k: a[k] for k in ('template_type','color_key','accessory')}
                       for a in normalized['characters']},
        'backgrounds': {s['scene_id']: s['background_type'] for s in normalized['scenes']}}
    bindings['assets'] = catalog['assets']
    data.update(target_capabilities=capabilities, asset_bindings=bindings, coverage=coverage, compiler_version='mobile_rules_1.0.0',
        validation_report={'errors': [], 'warnings': [
            {'code': 'PREVIEW_LAYOUT', 'message': '网页用固定72字符分页，手机须经真实字体再次校验；不承诺网页与手机字幕时长一致。'},
            {'code': 'VISUAL_ESTIMATED', 'message': '时间为视觉估算，没有核实或锁定声音时间轴。'}]})
    data['plan_hash'] = digest({k: v for k, v in data.items() if k != 'frames'})
    return data
