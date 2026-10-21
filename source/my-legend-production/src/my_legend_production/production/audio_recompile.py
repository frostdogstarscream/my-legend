"""Recompile a completed native batch with timing-only edits, without providers."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from ..core import require
from ..worker import save
from ..performance_production import read
from .compound_audio import CompoundArrangement


TIMING_FIELDS = ('start', 'end', 'transition_end', 'fade_out_ms')
REPAIRABLE = {'ARRANGEMENT_EMPTY_AUDIO_INTERVAL', 'ARRANGEMENT_FADE_EXCEEDS_BACKGROUND',
              'ARRANGEMENT_SPEECH_OVERLAP_UNSUPPORTED', 'ARRANGEMENT_CONTROL_OUTSIDE_BACKGROUND',
              'ARRANGEMENT_GAIN_TRANSITION_OUTSIDE_BACKGROUND', 'ARRANGEMENT_STOP_NOT_EXECUTED'}


def generation_signature(plan):
    data = deepcopy(plan.model_dump())
    for event in data['events']:
        for op in event['operations']:
            for field in TIMING_FIELDS:
                op.pop(field, None)
    return data


def assert_timing_only(original, revised):
    require(generation_signature(original) == generation_signature(revised), 'AUDIO_TIMING_REPAIR_CONTENT_CHANGED')


def recompile_saved(root, request):
    from .audio_job import combine_arranged, usage_snapshot
    from .audio_cache import export_cache
    snapshot = read(root / 'native-complete.json')
    binding = snapshot['binding']
    for key, value in binding.items():
        if key != 'arrangement_sha256':
            require(request.get(key) == value, 'AUDIO_TIMING_REPAIR_INPUT_CHANGED')
    raw = Path(request['arrangement_path']).read_bytes()
    require(sha256(raw).hexdigest() == request['arrangement_sha256'], 'AUDIO_ARRANGEMENT_HASH_MISMATCH')
    plan = CompoundArrangement.model_validate_json(raw)
    assert_timing_only(CompoundArrangement.model_validate(snapshot['arrangement']), plan)
    delivery_raw = Path(request['delivery_path']).read_bytes()
    require(sha256(delivery_raw).hexdigest() == binding['delivery_sha256'], 'SOURCE_HASH_MISMATCH')
    report = combine_arranged(root, snapshot['batches'], snapshot['results'], delivery_raw, plan)
    cache = export_cache(root, snapshot['batches'], dict(binding, arrangement_sha256=request['arrangement_sha256']), snapshot['candidates'])
    report.update(raw_cache_sha256=cache['sha256'], raw_cache_entries=cache['entries'],
        imported_cache_entries=snapshot['imported'], timing_only_recompile=True,
        instruction_length_assessment=read(root / 'input/instruction-length-assessment.json'))
    report.update(usage_snapshot(root))
    save(root / 'result.json', report)
