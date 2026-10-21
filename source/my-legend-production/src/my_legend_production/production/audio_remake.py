"""Create a new production version from an owned, completed audio result."""
from .contracts import ProductionRequest
from .repository import ProductionError
from ..performance_production import validate_voices
from ..core import AdapterError


def remake_request(job, body, store):
    if job["revision"] != body.expected_revision:
        raise ProductionError("REVISION_CONFLICT")
    if job["status"] not in ("succeeded", "failed", "cancelled") or job["unsettled_calls"]:
        raise ProductionError("AUDIO_REMAKE_SOURCE_NOT_READY")
    refs = {a["kind"]: a for a in job["artifacts"]}
    if not {"StoryDelivery", "RenderManifest", "AudioMix", "AudioCache", "VoiceBindings"} <= refs.keys():
        raise ProductionError("AUDIO_REMAKE_CACHE_UNAVAILABLE")
    from .contracts import ArtifactRef
    delivery = store.json(ArtifactRef.model_validate(refs["StoryDelivery"]))
    bindings = store.json(ArtifactRef.model_validate(refs["VoiceBindings"]))["bindings"]
    voices = body.voices if body.voices is not None else bindings
    try:
        validate_voices(delivery, voices)
    except AdapterError:
        raise ProductionError("VOICE_BINDING_REQUIRED") from None
    events = {(ep["episode_id"], sc["scene_id"], u["line_id"]): u for ep in delivery["episodes"] for sc in ep["scenes"] for u in sc["utterances"]}
    revision = dict(cache=refs['AudioCache'], regenerate=[e.model_dump(exclude_none=True) for e in body.regenerate])
    plan_events = None
    if 'AudioExecutionArrangement' in refs:
        # Cache is bound to the original plan. Do not remake against a different execution plan.
        raise ProductionError('AUDIO_REMAKE_SOURCE_NOT_READY')
    if 'AudioArrangement' in refs:
        if 'AudioArrangementReview' not in refs:
            raise ProductionError('AUDIO_REVISION_PLAN_REVIEW_REQUIRED')
        if 'visual' in body.outputs:
            raise ProductionError('AUDIO_ARRANGEMENT_VISUAL_UNSUPPORTED')
        revision.update(arrangement=refs['AudioArrangement'], review=refs['AudioArrangementReview'])
        from .contracts import AudioRevision
        from .audio_revision_plan import read_revision_plan
        from .audio_arrangement import source_key
        plan = read_revision_plan(store, AudioRevision.model_validate(revision), ArtifactRef.model_validate(refs['StoryDelivery']))
        if any(getattr(e, 'sound_intent', None)=='omitted_for_delivery' for e in plan.events) or (
                'AudioArrangementEvidence' in refs and
                store.json(ArtifactRef.model_validate(refs['AudioArrangementEvidence'])).get('delivery_policy')=='audio_first'):
            raise ProductionError('AUDIO_REMAKE_SOURCE_NOT_READY')
        plan_events = {source_key(e.source): e for e in plan.events}
    for event in body.regenerate:
        line = events.get((event.episode_id, event.scene_id, event.line_id))
        if line is None:
            raise ProductionError("AUDIO_REMAKE_EVENT_NOT_FOUND")
        if line["kind"] not in ("dialogue", "narration", "sfx"):
            raise ProductionError("AUDIO_REMAKE_EVENT_UNSUPPORTED")
        if plan_events is not None:
            planned = plan_events.get((event.episode_id, event.scene_id, event.line_id))
            operations = getattr(planned, 'operations', [planned])
            audible = [o for o in operations if o and o.mode in ('speech', 'one_shot', 'background')]
            if not audible or (event.operation_id is not None and not any(getattr(o, 'operation_id', None) == event.operation_id for o in audible)):
                raise ProductionError('AUDIO_REMAKE_EVENT_UNSUPPORTED')
        elif event.operation_id is not None:
            raise ProductionError('AUDIO_REMAKE_EVENT_UNSUPPORTED')
    if not body.regenerate and voices == bindings:
        raise ProductionError("AUDIO_REMAKE_NO_CHANGE")
    return ProductionRequest.model_validate(dict(idempotency_key=body.idempotency_key, work_id=job["work_id"],
        source=dict(kind="delivery", source_version_id=job["source_version_id"], delivery=refs["StoryDelivery"], manifest=refs["RenderManifest"]),
        outputs=body.outputs, mode="automatic", voices=dict(mode="configured", bindings=voices),
        visual_profile=job["request"]["visual_profile"], budget=body.budget.model_dump(),
        audio_revision=revision))
