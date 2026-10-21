"""Deterministic performance-cue resolution and playback quality gate."""
from __future__ import annotations

from .schemas import EmphasisSpan, PerformanceValidationReport, StoryDelivery, Utterance

SPOKEN_KINDS = {"dialogue", "narration"}


def resolve_emphasis_span(text: str, span: EmphasisSpan) -> EmphasisSpan:
    """Resolve the Nth occurrence using Python's Unicode-code-point indexes."""
    start = -1
    cursor = 0
    for _ in range(span.occurrence):
        start = text.find(span.span_text, cursor)
        if start < 0:
            raise ValueError(f"emphasis text not found: {span.span_text!r}")
        cursor = start + len(span.span_text)
    return span.model_copy(update={"start_char": start, "end_char": start + len(span.span_text)})


def normalize_utterance(utterance: Utterance) -> Utterance:
    if utterance.kind not in SPOKEN_KINDS:
        return utterance
    resolved = [resolve_emphasis_span(utterance.text, item) for item in utterance.emphasis]
    return utterance.model_copy(update={"emphasis": resolved})


def validate_utterance(utterance: Utterance) -> list[str]:
    errors: list[str] = []
    if utterance.kind not in SPOKEN_KINDS:
        return errors
    if not utterance.text.strip():
        errors.append(f"{utterance.line_id}: spoken line text is empty")
    if utterance.kind == "dialogue" and not utterance.speaker_role_id:
        errors.append(f"{utterance.line_id}: dialogue requires speaker_role_id")
    if utterance.kind == "narration" and not utterance.narrator_id:
        errors.append(f"{utterance.line_id}: narration requires narrator_id")
    if not utterance.performer_id:
        errors.append(f"{utterance.line_id}: performer_id is required")
    if not utterance.emotion:
        errors.append(f"{utterance.line_id}: emotion is required")
    if not (utterance.tone_instruction or "").strip():
        errors.append(f"{utterance.line_id}: tone_instruction is required")
    if not utterance.emphasis:
        errors.append(f"{utterance.line_id}: at least one emphasis span is required")
    for span in utterance.emphasis:
        if span.start_char is None or span.end_char is None:
            errors.append(f"{utterance.line_id}: emphasis indexes are unresolved")
            continue
        if utterance.text[span.start_char:span.end_char] != span.span_text:
            errors.append(f"{utterance.line_id}: emphasis text/index mismatch")
    return errors


def validate_story_delivery(delivery: StoryDelivery) -> PerformanceValidationReport:
    utterances = [u for ep in delivery.episodes for scene in ep.scenes for u in scene.utterances]
    spoken = [u for u in utterances if u.kind in SPOKEN_KINDS]
    errors = [error for utterance in utterances for error in validate_utterance(utterance)]
    blurb = delivery.preview_blurb.strip()
    if not blurb:
        errors.append("preview_blurb is required")
    elif len(blurb) < 15 or len(blurb) > 160 or "\n" in blurb:
        errors.append("preview_blurb must be one paragraph of 15-160 characters")
    count = len(spoken)
    emotion_valid = sum(bool(u.emotion) for u in spoken)
    tone_valid = sum(bool(u.emotion and (u.tone_instruction or "").strip()) for u in spoken)
    emphasis_valid = sum(bool(u.emphasis) for u in spoken)
    span_total = sum(len(u.emphasis) for u in spoken)
    span_valid = sum(
        1
        for u in spoken
        for span in u.emphasis
        if span.start_char is not None
        and span.end_char is not None
        and u.text[span.start_char:span.end_char] == span.span_text
    )
    return PerformanceValidationReport(
        validation_status="PASSED" if count > 0 and not errors else "FAILED",
        spoken_line_count=count,
        emotion_coverage=emotion_valid / count if count else 0.0,
        tone_coverage=tone_valid / count if count else 0.0,
        emphasis_coverage=emphasis_valid / count if count else 0.0,
        emphasis_span_validity=span_valid / span_total if span_total else 0.0,
        warnings=errors,
    )
