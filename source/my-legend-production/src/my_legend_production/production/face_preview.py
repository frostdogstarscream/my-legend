"""Immutable, opt-in basic face profiles; retained versions must never be edited."""
import json
from pathlib import Path
from .life_contracts import canonical_hash, fail

DEFAULT_PROFILE_SHA256 = 'ea5b794b7a6bd9bee3db0459019e4fcb3d3e6b26523166a7d55c4e011a704ada'
EXPRESSION_PROFILE_SHA256 = 'e350412238956fde7ce8774e32e5ff7d1bd4ece2cc9f8c69b0253fc804307da1'


def face_envelope(script, catalog, digest=None, mode='basic', track=None):
    versions = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(
        (Path(__file__).parent / 'schemas').glob('face-*.json'))]
    selected = next((p for p in versions if canonical_hash(p) == (digest or (EXPRESSION_PROFILE_SHA256 if mode == 'expressions' else DEFAULT_PROFILE_SHA256))), None)
    if selected is None:
        fail('face_profile_sha256', '面部 Profile 版本不存在，请保留原版本或明确选择新版本')
    diagnostics = []
    for binding in script['actors']:
        profile = next((p for p in selected['profiles'] if p['actor_id'] == binding['actor_id']), None)
        actor = next(a for a in catalog['actors'] if a['actor_id'] == binding['actor_id'])
        asset = next((s for s in catalog['skeletons'] if s['asset_id'] == actor.get('asset_id')), None)
        matches = profile and asset and all([
            profile['actor_revision'] == binding['actor_revision'],
            profile['asset_id'] == asset['asset_id'],
            profile['original_sha256'] == asset['original']['sha256'],
            profile['skeleton_sha256'] == asset['skeleton']['sha256'],
            profile['source_filename'] == asset.get('source_filename'),
        ])
        diagnostics.append({'code': 'FACE_PROFILE_READY' if matches else 'FACE_PROFILE_UNAVAILABLE',
                            'instance_id': binding['instance_id'],
                            'message': '基础面部已绑定；待浏览器校验附件' if matches else '没有匹配素材版本的面部 Profile，保留原生动作'})
    expressive = selected['algorithm_version'] == 'expression_face/1'
    result = {'schema_version': 'legend_expression_face/1' if expressive else 'legend_basic_face/1', 'profile_sha256': canonical_hash(selected),
            'profile_set': selected, 'bindings': {k: script[k] for k in (
                'job_id', 'source_version_id', 'catalog_sha256', 'audio_sha256', 'timeline_sha256')},
            'diagnostics': diagnostics}
    if expressive:
        from .performance_tracks import track_bindings, validate_face_track
        value = track if track is not None else {'schema_version': 'legend_face_track/1', 'bindings': track_bindings(script),
                                                'profile_sha256': result['profile_sha256'], 'cues': []}
        result['track'] = validate_face_track(value, script, result)
    elif track is not None:
        fail('face_track', '请先启用经过表情核对的 Profile 版本')
    return result
