"""Validate and reuse immutable planning evidence when remaking existing audio."""
from hashlib import sha256

from .audio_arrangement import arrangement_bytes, bind_arrangement
from .audio_plan import SoundArrangementReview, review_plan
from .compound_audio import CompoundArrangement, read_audio_arrangement
from .compound_audio_plan import CompoundArrangementReview, review_operations
from .repository import ProductionError


def read_revision_plan(store, revision, delivery_ref):
    plan = read_audio_arrangement(store.json(revision.arrangement))
    bind_arrangement(plan, store.verify(delivery_ref).read_bytes())
    if sha256(arrangement_bytes(plan)).hexdigest() != revision.arrangement.sha256:
        raise ProductionError('AUDIO_REVISION_PLAN_BINDING_INVALID')
    compound = isinstance(plan, CompoundArrangement)
    review_model = CompoundArrangementReview if compound else SoundArrangementReview
    review = review_model.model_validate(store.json(revision.review))
    (review_operations if compound else review_plan)(review, plan)
    return plan


def reuse_revision_plan(ctx, delivery_ref):
    revision = ctx.request.audio_revision
    if 'visual' in ctx.request.outputs:
        raise ProductionError('AUDIO_ARRANGEMENT_VISUAL_UNSUPPORTED')
    read_revision_plan(ctx.store, revision, delivery_ref)
    return [revision.arrangement, revision.review,
            ctx.store.put_json('AudioArrangementEvidence', delivery_ref.source_version_id,
                dict(schema_version='audio_arrangement_reuse/1.0', arrangement_sha256=revision.arrangement.sha256,
                     review_sha256=revision.review.sha256, reused=True, provider_calls=0,
                     session_context_supplied=False))]
