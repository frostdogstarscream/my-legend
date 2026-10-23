"""Local Spine preview sidecar: consumes delivered audio, never advances production.

No model calls, content approvals, or mutations to the original job. Third-party
assets are served only by content hash from an explicit local catalog.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field

from .contracts import ArtifactRef, Contract
from .playback import playback_view
from .repository import ProductionError
from .timeline import read_timeline
from .auto_face_suggestions import FaceSuggestionRequest, build_face_suggestions


class StagePreviewRequest(Contract):
    bindings: dict[str, str] = Field(default_factory=dict, max_length=3)
    profile: Literal['battle', 'life_v1'] = 'battle'
    operation: Literal['compile', 'validate_and_save'] = 'compile'
    initial_outfits: dict[str, str | None] = Field(default_factory=dict, max_length=3)
    script: dict | None = None
    face_enabled: bool = False
    face_profile_sha256: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    face_mode: Literal['basic', 'expressions'] = 'basic'
    face_track: dict | None = None
    dialogue_enabled: bool = False
    dialogue_track: dict | None = None


def local_root():
    workspace = Path(os.environ.get('MY_LEGEND_WORKSPACE', Path(__file__).resolve().parents[5]))
    return Path(os.environ.get('MY_LEGEND_SPINE_ROOT', workspace / 'runtime/my-legend-spine'))


def load_catalog(root):
    try:
        value = json.loads((root / 'catalog.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise ProductionError('SPINE_ASSETS_NOT_CONFIGURED') from None
    if value.get('schema_version') != 'spine_preview_catalog/1.0' or value.get('mouth_control') is not False:
        raise ProductionError('SPINE_CATALOG_INVALID')
    return value


def public_catalog(value):
    return {k: v for k, v in value.items() if k not in ('files',)}


def resolve_asset(root, catalog, digest):
    if not re.fullmatch('[a-f0-9]{64}', digest) or digest not in catalog['files']:
        raise ProductionError('SPINE_ASSET_NOT_FOUND')
    path = root / 'content' / digest
    ref = catalog['files'][digest]
    if not path.is_file() or path.stat().st_size != ref['size_bytes'] or sha256(path.read_bytes()).hexdigest() != digest:
        raise ProductionError('SPINE_ASSET_HASH_MISMATCH')
    return path, ref['media_type']


def compile_preview(job, body, store, catalog):
    manifest = playback_view(job, store)['manifest']
    if not manifest or not manifest.get('audio'):
        raise ProductionError('SPINE_AUDIO_NOT_READY')
    delivery = store.json(ArtifactRef.model_validate(manifest['script']['artifact']))
    raw_timeline = store.json(ArtifactRef.model_validate(manifest['timeline']['artifact']))
    read_timeline(raw_timeline)
    version = manifest['source_version_id']
    if delivery.get('story_version_id') != version:
        raise ProductionError('SOURCE_VERSION_MISMATCH')
    # This is a standing cast sample, not a claim of reconstructed plot actions.
    names = {c['role_id']: c.get('canon', {}).get('name', c['role_id']) for c in delivery.get('character_snapshot', [])}
    names.update({c['role_id']: c.get('story_name') or c.get('name') or c['role_id']
                  for key in ('character_portrayals', 'fictional_voice_roles') for c in delivery.get(key, [])})
    source = {}
    speaking_roles = []
    warnings = ['本机战斗动作预览：保留已交付声音，可手动选择攻击、移动等素材动作；不含口型。']
    action_count = 0
    for ep in delivery['episodes']:
        for sc in ep['scenes']:
            for line in sc['utterances']:
                source[(ep['episode_id'], sc['scene_id'], line['line_id'])] = line
                role = line.get('speaker_role_id') if line['kind'] == 'dialogue' else None
                if role and role not in speaking_roles:
                    speaking_roles.append(role)
                action_count += line['kind'] == 'action'
    available = {a['actor_id']: a for a in catalog['actors']}
    bindings = dict(body.bindings) if body.bindings else dict(zip(speaking_roles[:3], available))
    if any(role not in speaking_roles or actor not in available for role, actor in bindings.items()):
        raise ProductionError('SPINE_BINDING_INVALID')
    if not bindings:
        raise ProductionError('SPINE_DIALOGUE_ROLE_REQUIRED')
    if len(set(bindings.values())) != len(bindings):
        raise ProductionError('SPINE_DUPLICATE_ACTOR')
    actors = []
    for i, (role, actor_id) in enumerate(bindings.items()):
        asset = available[actor_id]
        actors.append({'role_id': role, 'name': names.get(role, role), 'actor_id': actor_id,
                       'actor_version': asset['version'], 'x': (i+1)/(len(bindings)+1),
                       'animation': asset['idle_animation'], 'loop': True})
    cues = []
    for event in raw_timeline['events']:
        ref = event.get('source', event)
        key = (ref['episode_id'], ref['scene_id'], ref['line_id'])
        line = source.get(key)
        if line is None:
            raise ProductionError('SPINE_SOURCE_REFERENCE_INVALID')
        if line['kind'] not in ('dialogue', 'narration'):
            continue
        operations = event.get('operations', [event])
        for op in operations:
            mode = op.get('mode', op.get('render_role', 'speech'))
            if mode != 'speech' or op['end_frame'] <= op['start_frame']:
                continue
            role = line.get('speaker_role_id') if line['kind'] == 'dialogue' else None
            cues.append({'source': {k: v for k, v in zip(('episode_id', 'scene_id', 'line_id'), key)},
                         'role_id': role, 'speaker': names.get(role, role or '旁白'),
                         'text': op.get('cue_text') or line['text'],
                         'start_frame': op['start_frame'], 'end_frame': op['end_frame']})
    cues.sort(key=lambda x: (x['start_frame'], x['end_frame']))
    if not cues:
        raise ProductionError('SPINE_SPEECH_TIMELINE_REQUIRED')
    if action_count:
        warnings.append(f'{action_count} 条剧情动作尚未自动编排；当前由用户选择素材动作。')
    if len(speaking_roles) > len(bindings):
        warnings.append('未绑定的声音角色继续显示字幕，不自动增加或替换舞台演员。')
    return {'schema_version': 'spine_stage_preview/1.0', 'compiler_version': 'standing_spine21/1',
            'job_id': job['id'], 'source_version_id': version, 'title': delivery.get('title', 'Spine 演绎'),
            'catalog_sha256': catalog['catalog_sha256'], 'mouth_control': False,
            'script': manifest['script'], 'audio': manifest['audio'], 'timeline': manifest['timeline'],
            'stage': {'schema_version': 'spine_stage_script/1.0', 'sample_rate': manifest['sample_rate'],
                      'total_frames': manifest['total_frames'], 'actors': actors, 'cues': cues},
            'warnings': warnings, 'usage': 'local_preview_only'}


def save_preview(root, value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    digest = sha256(data).hexdigest()
    # Job IDs are repository-owned, but validate before using any path component.
    if not re.fullmatch('[A-Za-z0-9_-]{1,128}', value['job_id']):
        raise ProductionError('SPINE_JOB_ID_INVALID')
    folder = root / 'previews' / value['job_id']
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (digest + '.json')
    if not target.exists():
        fd, tmp = tempfile.mkstemp(dir=folder, prefix='.preview-')
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            os.replace(tmp, target)
        finally:
            Path(tmp).unlink(missing_ok=True)
    return {**value, 'preview_sha256': digest}


def register_spine_routes(router, repository, artifacts, owner, translate):
    from .life_contracts import LifeValidationError
    from .life_preview import build_life_preview, life_asset_catalog, load_life_catalog, read_life_preview

    def life_result(operation):
        try:
            return translate(operation)
        except LifeValidationError as exc:
            return JSONResponse(status_code=422, content={'code': 422, 'message': '；'.join(
                d['path'] + '：' + d['message'] for d in exc.diagnostics), 'data': {'diagnostics': exc.diagnostics}})

    @router.get('/stage-preview/catalog')
    def catalog(request: Request, profile: Literal['battle', 'life_v1'] = 'battle', catalog_sha256: str | None = None):
        owner(request)
        return life_result(lambda: public_catalog(load_life_catalog(local_root(), catalog_sha256) if profile == 'life_v1' else load_catalog(local_root())))

    @router.get('/stage-preview/assets/{digest}')
    def asset(digest: str, request: Request):
        owner(request)
        root = local_root()
        try:
            try:
                allowlist = load_catalog(root)
            except ProductionError:
                allowlist = {'files': {}}
            if digest not in allowlist['files']:
                allowlist = life_asset_catalog(root, digest)
            path, mime = resolve_asset(root, allowlist, digest)
        except ProductionError as exc:
            raise HTTPException(404, exc.code) from None
        return FileResponse(path, media_type=mime, headers={'Cache-Control': 'private, no-store'})

    @router.post('/production-jobs/{job_id}/stage-preview')
    def preview(job_id: str, body: StagePreviewRequest, request: Request):
        job = translate(lambda: repository.get(job_id, owner(request)))['data']
        root = local_root()
        if body.profile == 'life_v1':
            return life_result(lambda: save_preview(root, build_life_preview(job, body, artifacts,
                load_life_catalog(root, body.script.get('catalog_sha256') if body.operation == 'validate_and_save' and body.script else None))))
        return translate(lambda: save_preview(root, compile_preview(job, body, artifacts, load_catalog(root))))

    @router.get('/production-jobs/{job_id}/stage-preview/{preview_sha256}')
    def saved_preview(job_id: str, preview_sha256: str, request: Request):
        job = translate(lambda: repository.get(job_id, owner(request)))['data']
        return life_result(lambda: read_life_preview(local_root(), job, preview_sha256, artifacts))

    @router.post('/production-jobs/{job_id}/stage-preview/suggest-face')
    def suggest_face(job_id: str, body: FaceSuggestionRequest, request: Request):
        # Ownership precedes all preview/artifact reads. No save_preview or model call here.
        job = translate(lambda: repository.get(job_id, owner(request)))['data']
        def generate():
            root = local_root()
            value = read_life_preview(root, job, body.source_preview_sha256, artifacts)
            return build_face_suggestions(value,
                artifacts.json(ArtifactRef.model_validate(value['script']['artifact'])),
                artifacts.json(ArtifactRef.model_validate(value['timeline']['artifact'])),
                load_life_catalog(root, value['catalog_sha256']), body)
        return life_result(generate)
