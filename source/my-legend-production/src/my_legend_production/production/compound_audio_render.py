"""Offline rendering of compound choices; no model calls or runtime activation."""
from hashlib import sha256
import json
from pathlib import Path

from .audio_arrangement_render import _compile_atomic_arrangement
from .compound_audio import bind_compound, compound_bytes, lower_operations
from .compound_timeline import CompoundTimeline, bind_compound_timeline


def compile_compound(asset_root, output, delivery_bytes, plan, assets, *, max_output_seconds=1800):
    """Assets are keyed by operation_id; original story source IDs never change."""
    bind_compound(plan,delivery_bytes)
    atomic,provenance=lower_operations(plan)
    needed={e.source.line_id for e in atomic.events if e.mode in ('speech','one_shot','background')}
    if set(assets)!=needed:
        raise ValueError('COMPOUND_MEASURED_ASSETS_MISMATCH')
    native_assets={(e.source.episode_id,e.source.scene_id,e.source.line_id):assets[e.source.line_id]
                   for e in atomic.events if e.source.line_id in needed}
    output=Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    internal,report=_compile_atomic_arrangement(asset_root,output/'operations',atomic,native_assets,
                                                max_output_seconds=max_output_seconds)
    by_operation={e.line_id:e for e in internal.events}
    events=[]
    for event in plan.events:
        operations=[]
        for choice in event.operations:
            rendered=by_operation[choice.operation_id]
            item=dict(operation_id=choice.operation_id,cue_text=choice.cue_text,mode=choice.mode,
                      start_frame=rendered.start_frame,end_frame=rendered.end_frame)
            if choice.mode in ('speech','one_shot','background'):
                item.update(source_audio_sha256=rendered.source_audio_sha256,processing=rendered.processing,
                            parent_audio_sha256=rendered.parent_audio_sha256)
            elif choice.mode!='action_note':
                item.update(target_operation_id=rendered.target.line_id,
                            transition_end_frame=rendered.transition_end_frame,
                            from_gain_db=rendered.from_gain_db,to_gain_db=rendered.to_gain_db)
            operations.append(item)
        events.append(dict(**event.model_dump(exclude={'operations'}),operations=operations))
    plan_raw=compound_bytes(plan)
    timeline=CompoundTimeline(source_version_id=plan.source_version_id,arrangement_sha256=sha256(plan_raw).hexdigest(),
        sample_rate=internal.sample_rate,channels=internal.channels,total_frames=internal.total_frames,
        audio_sha256=internal.audio_sha256,events=events)
    bind_compound_timeline(plan,timeline)
    audio=(output/'operations/mix.wav').read_bytes()
    if sha256(audio).hexdigest()!=timeline.audio_sha256:
        raise ValueError('COMPOUND_RENDERED_AUDIO_HASH_MISMATCH')
    (output/'mix.wav').write_bytes(audio)
    (output/'arrangement.json').write_bytes(plan_raw)
    internal_report=(output/'operations/compile-report.json').read_bytes()
    traces=[dict(operation_id=p['source']['line_id'],source=provenance[p['source']['line_id']],
                 **{k:v for k,v in p.items() if k!='source'}) for p in report['asset_provenance']]
    result=dict(schema_version='audio_arrangement_compile/2.0',source_version_id=plan.source_version_id,
                delivery_sha256=plan.delivery_sha256,arrangement_sha256=timeline.arrangement_sha256,
                audio=dict(report['audio'],file='mix.wav'),asset_provenance=traces,
                internal_render_directory='operations',internal_report_sha256=sha256(internal_report).hexdigest(),
                operation_sources=provenance,provider_calls=0,human_listening='pending',semantic_review='pending')
    for name,value in [('timeline.json',timeline.model_dump()),('compile-report.json',result)]:
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return timeline,result
