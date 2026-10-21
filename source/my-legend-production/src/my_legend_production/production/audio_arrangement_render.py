"""Deterministic, offline whole-story audio compiler using pinned native media."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from .audio_arrangement import AudioArrangement, arrangement_bytes, bind_arrangement, resolve_frames, source_key, gain_controls
from .timeline import ArrangedTimeline, ControlledTimeline


def compile_arrangement(asset_root, output, delivery_bytes, plan: AudioArrangement, assets, *, max_output_seconds=1800):
    """Assets map source tuples to native asset records. Output must be a new directory.

    This does not generate assets, call models, or imply human/content approval.
    Inputs must already be normalized to the plan's clock; no speech resampling/cutting.
    """
    bind_arrangement(plan, delivery_bytes)
    return _compile_atomic_arrangement(asset_root, output, plan, assets, max_output_seconds=max_output_seconds)


def _compile_atomic_arrangement(asset_root, output, plan, assets, *, max_output_seconds=1800):
    """Internal rendering core. Public callers must validate original source binding first."""
    import numpy as np
    import soundfile as sf
    from lingban_pipeline.media import loop_asset, measure, mix, read, write

    asset_root, output = Path(asset_root).resolve(), Path(output).resolve()
    needed = {source_key(e.source) for e in plan.events if e.mode in ('speech', 'one_shot', 'background')}
    if set(assets) != needed:
        raise ValueError('ARRANGEMENT_MEASURED_ASSETS_MISMATCH')
    durations, verified = {}, {}
    for key, asset in assets.items():
        info = asset['processed']
        path = (asset_root / info['file']).resolve()
        if not path.is_relative_to(asset_root):
            raise ValueError('ARRANGEMENT_ASSET_PATH_ESCAPE')
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != info['sha256']:
            raise ValueError('ARRANGEMENT_ASSET_HASH_MISMATCH')
        header = sf.info(path)
        if (header.samplerate, header.channels) != (plan.sample_rate, plan.channels):
            raise ValueError('ARRANGEMENT_ASSET_CLOCK_MISMATCH')
        if header.frames != info['frames'] or info['sample_rate'] != header.samplerate or info['channels'] != header.channels:
            raise ValueError('ARRANGEMENT_ASSET_METADATA_MISMATCH')
        if not 0 < header.frames <= plan.sample_rate * max_output_seconds:
            raise ValueError('ARRANGEMENT_ASSET_SIZE_OUT_OF_BOUNDS')
        # Native read also rejects empty/non-finite PCM before output is created.
        read(path, channels=plan.channels)
        durations[key] = int(header.frames)
        verified[key] = raw
    positions = resolve_frames(plan, durations, max_output_seconds=max_output_seconds)
    curves = gain_controls(plan,positions)
    timeline_model = ControlledTimeline if plan.schema_version=='audio_arrangement/1.1' else ArrangedTimeline
    output.mkdir(parents=True, exist_ok=False)
    (output / 'inputs').mkdir()
    plan_raw = arrangement_bytes(plan)
    (output / 'arrangement.json').write_bytes(plan_raw)
    events, placements, provenance = [], [], []
    render_gains = {str(i):e.gain_db for i,e in enumerate(plan.events,1)}
    for sequence, decision in enumerate(plan.events, 1):
        key = source_key(decision.source)
        start, end = positions[key, 'start'], positions[key, 'end']
        common = dict(sequence=sequence, **decision.source.model_dump(), kind=decision.kind,
                      text=decision.text, identity_id=decision.identity_id, start_frame=start, end_frame=end)
        if decision.mode in ('continue_background','change_background') or decision.mode=='stop_sound' and timeline_model==ControlledTimeline:
            target=decision.control_target or decision.stop_target
            transition={}
            if decision.mode=='change_background':
                curve=next(c for c in curves[source_key(target)] if c['source']==decision.source.model_dump())
                transition=dict(transition_end_frame=curve['end_frame'],from_gain_db=curve['from_gain_db'],to_gain_db=curve['to_gain_db'])
            events.append(dict(**common,duration_basis='sound_control',marker_type=decision.mode,target=target.model_dump(),**transition))
            continue
        if decision.mode == 'action_note' or decision.mode == 'stop_sound':
            events.append(dict(**common, duration_basis='non_audio_marker', marker_type=decision.mode,
                               target=decision.stop_target.model_dump() if decision.stop_target else None))
            continue
        original = assets[key]
        parent = original['processed']
        local = output / 'inputs' / (parent['sha256'] + '.wav')
        if not local.exists():
            local.write_bytes(verified[key])
        asset = deepcopy(original)
        asset['processed'] = measure(local, output)
        asset.setdefault('keypoints', [])
        asset.setdefault('processing', {})
        processing = 'unchanged'
        if decision.mode == 'background' and end - start != durations[key]:
            asset = loop_asset(output, asset, end - start)
            processing = 'looped_background' if end - start > durations[key] else 'cropped_background'
        if decision.fade_out_ms:
            a, rate = read(output / asset['processed']['file'], channels=plan.channels)
            count = round(decision.fade_out_ms * rate / 1000)
            a[-count:] *= np.linspace(1, 0, count)[:, None]
            before_fade = asset['processed']
            revision = sha256(json.dumps({'parent': before_fade['sha256'], 'fade_frames': count}, sort_keys=True).encode()).hexdigest()
            faded = output / 'assets' / 'processed' / (revision + '.wav')
            write(faded, a, rate)
            asset.update(asset_id='asset_' + revision[:24], artifact_revision=revision, processed=measure(faded, output))
            asset['processing'] = asset['processing'] | {'fade_out_frames': count, 'fade_parent': before_fade}
            processing = 'faded_background' if processing == 'unchanged' else processing.replace('_background', '_faded_background')
        if decision.mode=='background' and curves[key]:
            a,rate=read(output/asset['processed']['file'],channels=plan.channels)
            # Normalize against the loudest planned gain, then apply that gain in the mixer.
            # This keeps intermediate PCM <= source amplitude, even when raising a quiet bed.
            maximum=max([decision.gain_db]+[c['to_gain_db'] for c in curves[key]])
            envelope=np.full(len(a),10**((decision.gain_db-maximum)/20),dtype='float64')
            for curve in curves[key]:
                left,right=curve['start_frame']-start,curve['end_frame']-start
                before,after=[10**((curve[name]-maximum)/20) for name in ('from_gain_db','to_gain_db')]
                if right>left:
                    envelope[left:right]=np.linspace(before,after,right-left)
                envelope[right:]=after
            a*=envelope[:,None]
            parent_info=asset['processed']
            automation=dict(initial_gain_db=decision.gain_db,render_gain_db=maximum,transitions=curves[key])
            revision=sha256(json.dumps(dict(parent=parent_info['sha256'],automation=automation),sort_keys=True).encode()).hexdigest()
            path=output/'assets/processed'/(revision+'.wav')
            write(path,a,rate)
            asset.update(asset_id='asset_'+revision[:24],artifact_revision=revision,processed=measure(path,output))
            asset['processing']=asset['processing']|dict(gain_automation=automation,automation_parent=parent_info)
            processing='automated_background';render_gains[str(sequence)]=maximum
        info = asset['processed']
        events.append(dict(**common, duration_basis='measured_audio', render_role=decision.mode,
                           source_audio_sha256=info['sha256'], processing=processing,
                           parent_audio_sha256=parent['sha256'] if processing != 'unchanged' else None))
        placements.append(dict(block_id=str(sequence), kind='speech' if decision.mode == 'speech' else
                               ('music' if decision.kind == 'music' else 'ambience') if decision.mode == 'background' else 'sfx',
                               render='narration' if decision.kind == 'narration' else 'dialogue',
                               start_frame=start, end_frame=end, file=info['file'], sha256=info['sha256']))
        provenance.append(dict(source=decision.source.model_dump(), source_audio_sha256=parent['sha256'],
                               rendered=asset, processing=processing))
    total = max(e['end_frame'] for e in events)
    # Validate all execution claims BEFORE creating the final mix.
    timeline_data = dict(source_version_id=plan.source_version_id, arrangement_sha256=sha256(plan_raw).hexdigest(),
                         sample_rate=plan.sample_rate, channels=plan.channels, total_frames=total,
                         audio_sha256='0'*64, events=events)
    timeline_model.model_validate(timeline_data)
    config = {'channels': plan.channels, 'mix': {'gains_db': render_gains,
        'duck': {'enabled': True, 'gain_db': plan.duck_gain_db, 'attack_ms': plan.duck_attack_ms,
                 'release_ms': plan.duck_release_ms}, 'peak_ceiling': .95, 'limit': True}}
    native_timeline = dict(sample_rate=plan.sample_rate, total_frames=total, placements=placements)
    rendered = mix(output, native_timeline, config, output / 'mix.wav')
    if rendered['frames'] != total:
        raise ValueError('ARRANGEMENT_MIX_FRAME_MISMATCH')
    timeline_data['audio_sha256'] = rendered['sha256']
    timeline = timeline_model.model_validate(timeline_data)
    report = dict(schema_version='audio_arrangement_compile/1.0', source_version_id=plan.source_version_id,
                  delivery_sha256=plan.delivery_sha256, arrangement_sha256=timeline.arrangement_sha256,
                  audio=rendered, asset_provenance=provenance, provider_calls=0,
                  human_listening='pending', semantic_review='pending')
    for name, data in [('timeline.json', timeline.model_dump()), ('compile-report.json', report)]:
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return timeline, report
