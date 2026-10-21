from copy import deepcopy

import pytest
from pydantic import ValidationError

from my_legend_production.production.contracts import ProductionRequest, StageResult, RunEvidence, stage_graph, fingerprint


def production_request(**changes):
    value = {"idempotency_key": "test-request-001", "source": {
        "kind": "creative", "title": "院子里的故事", "idea": "两位伙伴在院子交谈后分别离开。", "genre": "生活",
        "character_ids": ["one", "two"]}, "outputs": ["script", "audio", "visual"]}
    return ProductionRequest.model_validate(value | changes)


def test_requested_outputs_determine_dependencies():
    assert stage_graph(production_request(outputs=["script"])) == {"story": []}
    assert stage_graph(production_request(outputs=["audio"])) == {"story": [], "audio": ["story"]}
    graph = stage_graph(production_request(outputs=["visual"]))
    assert graph["visual_plan"] == ["story"]
    assert graph["visual_render"] == ["audio", "visual_plan"]
    assert graph["integrate"] == ["audio", "visual_render"]


def test_reject_duplicate_outputs_unknown_profiles_and_fake_human_approval():
    for changes in ({"outputs": ["audio", "audio"]}, {"visual_profile": "anything"}, {"budget": {"max_provider_calls": 9999}}):
        with pytest.raises(ValidationError):
            production_request(**changes)
    with pytest.raises(ValidationError):
        StageResult(status="running", human_review="approved")
    with pytest.raises(ValidationError):
        StageResult(status="succeeded", technical_quality="passed")


def test_source_version_mismatch_rejected_before_task():
    ref = {"kind": "StoryDelivery", "sha256": "a" * 64, "source_version_id": "v1", "size_bytes": 10}
    with pytest.raises(ValidationError):
        production_request(source={"kind": "delivery", "source_version_id": "v1", "delivery": ref,
                                   "manifest": ref | {"kind": "RenderManifest", "source_version_id": "v2"}})


def test_output_order_is_canonical_and_evidence_defaults_to_unattested():
    assert fingerprint(production_request(outputs=["visual", "audio"])) == fingerprint(production_request(outputs=["audio", "visual"]))
    evidence = RunEvidence(job_id="j", frozen_files={})
    assert evidence.session_interventions is None
    assert evidence.evidence_basis == "unattested"


def test_optional_audio_revision_does_not_change_existing_idempotency_hashes():
    request = production_request()
    legacy = request.model_dump(mode="json", exclude={"audio_revision"})
    assert fingerprint(request) == fingerprint(legacy)


def test_duration_matches_current_story_engine_before_any_provider_call():
    value = production_request().model_dump()
    value['source']['target_duration_seconds'] = 60
    with pytest.raises(ValidationError):
        ProductionRequest.model_validate(value)
