"""Deterministic delivery of an owned saved performance. Never calls a provider."""
from hashlib import sha256
import json

from .contracts import ArtifactRef, ProductionRequest, SavedPerformanceSource, DirectedPerformanceSource, StageResult
from .repository import ProductionError

INPUT_KINDS = {'StoryDelivery', 'RenderManifest', 'AudioMix', 'MeasuredTimeline',
               'AudioArrangement', 'AudioExecutionArrangement', 'AudioArrangementEvidence', 'AudioReport'}
STAGES = {'performance_import', 'performance_package'}


def verify_resources(value, root=None, check=lambda: None):
    from .spine_preview import local_root, resolve_asset
    from .h5_preview import load_h5_catalog
    from .scene_preview import load_scene_catalog
    root = root or local_root()
    catalogs = [load_h5_catalog(root, value['catalog_sha256'])]
    scene_hashes = sorted({s['scene']['catalog_sha256'] for s in value['scene_track']['segments']})
    catalogs.extend(load_scene_catalog(root, digest) for digest in scene_hashes)
    seen = set()
    for catalog in catalogs:
        for digest in catalog['files']:
            if digest not in seen:
                if len(seen) % 32 == 0:
                    check()
                resolve_asset(root, catalog, digest)
                seen.add(digest)
    check()
    return {'character_catalog': value['catalog_sha256'], 'scene_catalogs': scene_hashes,
            'compiler_version': value.get('compiler_version'), 'rule_version': value.get('rule_version')}


def read_saved(job, digest, store):
    from .spine_preview import local_root
    from .h5_preview import read_h5_preview
    from .commander_preview import read_commander_preview
    from .spine_director import read_directed_preview
    from .life_contracts import LifeValidationError
    root = local_root()
    try:
        raw = (root / 'previews' / job['id'] / (digest + '.json')).read_bytes()
        value = json.loads(raw)
        if sha256(raw).hexdigest() != digest:
            raise ProductionError('H5_PREVIEW_HASH_MISMATCH')
        reader = {'spine_stage_preview/3.0': read_h5_preview,
                  'spine_stage_preview/3.1': read_commander_preview,
                  'spine_stage_preview/3.2': read_directed_preview}.get(value.get('schema_version'))
        if reader is None or value.get('profile') != 'life_v2':
            raise ProductionError('H5_PREVIEW_VERSION_UNSUPPORTED')
        reader(root, job, digest, store)
        return raw, value
    except (OSError, ValueError, KeyError, LifeValidationError):
        raise ProductionError('H5_PREVIEW_INVALID') from None


def prepare_request(repository, store, owner_id, source_job_id, body):
    from .playback import playback_view
    job = repository.get(source_job_id, owner_id)
    if job['request']['source']['kind'] in ('saved_performance', 'directed_performance'):
        raise ProductionError('H5_ORIGINAL_SOURCE_REQUIRED')
    manifest = playback_view(job, store)['manifest']
    if not manifest or not manifest.get('audio'):
        raise ProductionError('SPINE_AUDIO_NOT_READY')
    if body.parent_job_id:
        parent = repository.get(body.parent_job_id, owner_id)
        source = parent['request']['source']
        if (parent['work_id'] != job['work_id'] or source.get('kind') not in ('saved_performance','directed_performance')
                or source['source_job_id'] != source_job_id or parent['status'] != 'succeeded'):
            raise ProductionError('H5_PARENT_MISMATCH')
    raw, value = read_saved(job, body.preview_sha256, store)
    verify_resources(value)
    refs = [ArtifactRef.model_validate(a) for a in job['artifacts'] if a['kind'] in INPUT_KINDS]
    for ref in refs:
        store.verify(ref)
    snapshot = store.put('SpineStagePreview', job['source_version_id'], raw)
    return ProductionRequest(idempotency_key=body.idempotency_key, work_id=job['work_id'], outputs=['h5'],
        source=SavedPerformanceSource(source_job_id=source_job_id, source_version_id=job['source_version_id'],
            preview=snapshot, inputs=refs, parent_job_id=body.parent_job_id))


def verify_snapshot(source, store, source_job, check=lambda: None):
    from .playback import playback_view
    from .h5_preview import validate_h5_value
    from .commander_preview import validate_commander_value
    from .spine_director import validate_directed_value
    from .spine_preview import local_root
    from .life_contracts import LifeValidationError
    value = store.json(source.preview)
    if (source.preview.kind != 'SpineStagePreview' or value.get('job_id') != source.source_job_id
            or value.get('source_version_id') != source.source_version_id or value.get('profile') != 'life_v2'):
        raise ProductionError('H5_SOURCE_BINDING_MISMATCH')
    owned = {a['kind']: a for a in source_job['artifacts']}
    for ref in source.inputs:
        if ref.kind not in INPUT_KINDS or owned.get(ref.kind) != ref.model_dump() or ref.source_version_id != source.source_version_id:
            raise ProductionError('H5_SOURCE_BINDING_MISMATCH')
        check()
        store.verify(ref)
    manifest = playback_view(source_job, store)['manifest']
    if not manifest or any(value[k] != manifest[k] for k in ('script', 'audio', 'timeline')):
        raise ProductionError('H5_SOURCE_BINDING_MISMATCH')
    try:
        validator = {'spine_stage_preview/3.0': validate_h5_value,
                     'spine_stage_preview/3.1': validate_commander_value,
                     'spine_stage_preview/3.2': validate_directed_value}[value['schema_version']]
        validator(local_root(), source_job, value, store)
    except (KeyError, ValueError, LifeValidationError):
        raise ProductionError('H5_PREVIEW_INVALID') from None
    resources = verify_resources(value, check=check)
    return value, resources, manifest.get('delivery_warnings', [])


class PerformanceImportStage:
    def __call__(self, ctx):
        source = ctx.request.source
        job = ctx.repository.get(source.source_job_id, ctx.stage['owner_id'])
        value, resources, warnings = verify_snapshot(source, ctx.store, job, ctx.check_active)
        provenance = {'schema_version': 'performance_source/1.0', 'source_job_id': source.source_job_id,
            'source_version_id': source.source_version_id, 'parent_job_id': source.parent_job_id,
            'preview_sha256': source.preview.sha256, 'resources': resources,
            'delivery_warnings': warnings, 'source_review_confirmed': value['review_confirmed']}
        ctx.check_active()
        return StageResult(status='succeeded', source_version_id=source.source_version_id,
            technical_quality='passed', human_review='pending', artifacts=[*source.inputs, source.preview,
                ctx.store.put_json('PerformanceSource', source.source_version_id, provenance)])


def build_manifest(job, store):
    refs = {a['kind']: ArtifactRef.model_validate(a) for a in job['artifacts']}
    source_type = DirectedPerformanceSource if job['request']['source']['kind']=='directed_performance' else SavedPerformanceSource
    source = source_type.model_validate(job['request']['source'])
    provenance = store.json(refs['PerformanceSource'])
    value = store.json(refs['SpineStagePreview'])
    if ((isinstance(source, SavedPerformanceSource) and refs['SpineStagePreview'] != source.preview)
            or provenance['preview_sha256'] != refs['SpineStagePreview'].sha256):
        raise ProductionError('H5_SOURCE_BINDING_MISMATCH')
    for key, kind in (('script', 'StoryDelivery'), ('audio', 'AudioMix'), ('timeline', 'MeasuredTimeline')):
        if ArtifactRef.model_validate(value[key]['artifact']) != refs[kind]:
            raise ProductionError('H5_SOURCE_BINDING_MISMATCH')
    pending = [c for c in value['coverage'] if c['status'] in ('needs_correction', 'unsupported')]
    def entry(kind):
        return {'artifact': refs[kind].model_dump(),
                'content_path': f"/api/v1/production-jobs/{job['id']}/artifacts/{refs[kind].sha256}"}
    manifest = {'schema_version': 'performance_manifest/1.0', 'job_id': job['id'], 'work_id': job['work_id'],
        'source_version_id': source.source_version_id, 'source_job_id': source.source_job_id,
        'parent_job_id': source.parent_job_id, 'title': value['title'],
        'script': entry('StoryDelivery'), 'audio': entry('AudioMix'), 'timeline': entry('MeasuredTimeline'),
        'preview': entry('SpineStagePreview'), 'resources': provenance['resources'],
        'sample_rate': value['stage']['sample_rate'], 'total_frames': value['stage']['total_frames'],
        'status': 'needs_attention' if pending or ('directing' in value and value['status']=='needs_attention') or any(d['code']=='UNBOUND_ROLE' for d in value['diagnostics']) else 'preview_ready',
        'coverage': value['coverage'], 'diagnostics': value['diagnostics'],
        'delivery_warnings': provenance['delivery_warnings'],
        'source_review_confirmed': provenance['source_review_confirmed'],
        'human_review': 'pending', 'publication': 'not_requested'}
    if 'directing' in value:
        manifest.update(casting_confirmed=value['directing']['casting_confirmed'],model_review=value['directing']['review_status'])
    if value.get('visual_policy'):
        manifest.update(visual_policy=value['visual_policy'],capability_sha256=value['capability_sha256'],
                        interpretation_notes=value.get('interpretation_notes',[]))
    return manifest


class PerformancePackageStage:
    def __call__(self, ctx):
        job = ctx.repository.get(ctx.stage['job_id'], ctx.stage['owner_id'])
        # Recheck fixed inputs/assets, not the mutable current catalog pointer.
        source_job = ctx.repository.get(ctx.request.source.source_job_id, ctx.stage['owner_id'])
        source = ctx.request.source
        if isinstance(source, DirectedPerformanceSource):
            snapshot = next(ArtifactRef.model_validate(a) for a in job['artifacts'] if a['kind']=='SpineStagePreview')
            fixed = SavedPerformanceSource(source_job_id=source.source_job_id, source_version_id=source.source_version_id,
                preview=snapshot, inputs=source.inputs, parent_job_id=source.parent_job_id)
            verify_snapshot(fixed, ctx.store, source_job, ctx.check_active)
        else:
            verify_snapshot(source, ctx.store, source_job, ctx.check_active)
        manifest = build_manifest(job, ctx.store)
        ctx.check_active()
        return StageResult(status='succeeded', source_version_id=manifest['source_version_id'],
            technical_quality='passed', human_review='pending',
            artifacts=[ctx.store.put_json('PerformanceManifest', manifest['source_version_id'], manifest)])


def performance_manifest(job, store):
    ref = next((ArtifactRef.model_validate(a) for a in job['artifacts'] if a['kind']=='PerformanceManifest'), None)
    if ref is None:
        return None
    value = store.json(ref)
    if ref.source_version_id != job['source_version_id'] or value != build_manifest(job, store):
        raise ProductionError('H5_MANIFEST_BINDING_MISMATCH')
    return value


def delivered_preview(job, store):
    manifest = performance_manifest(job, store)
    if manifest is None:
        raise ProductionError('H5_DELIVERY_NOT_READY')
    ref = ArtifactRef.model_validate(manifest['preview']['artifact'])
    value = store.json(ref)
    verify_resources(value)
    for key in ('script', 'audio', 'timeline'):
        store.verify(ArtifactRef.model_validate(manifest[key]['artifact']))
    return {'manifest': manifest, 'preview': {**value, 'preview_sha256': ref.sha256}}
