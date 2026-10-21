"""Phase 1 request, fingerprint and storage contract."""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from drama_engine.persistence.models import Base, StoryPlanModel, StoryPlanRevisionModel
from drama_engine.workflow.schemas import CreationPreferences, StoryOperationRequest, plan_fingerprint


def test_request_discriminator_and_audience_policy():
    adapter = TypeAdapter(StoryOperationRequest)
    request = adapter.validate_python({
        "intent": "create", "request_id": "r", "idempotency_key": "k",
        "user_instruction": "写一个温暖故事",
    })
    assert request.creation_preferences.content_form == "audio_drama"
    with pytest.raises(ValidationError):
        CreationPreferences(audience_band="14-17", content_rating="mature_non_explicit")


def test_fingerprint_uses_canonical_json_order():
    assert plan_fingerprint({"a": 1, "b": 2}) == plan_fingerprint({"b": 2, "a": 1})
    assert plan_fingerprint({"a": 1}) != plan_fingerprint({"a": 2})


def test_sqlite_workflow_tables_present(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'contract.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(StoryPlanModel(
            plan_id="p", request_id="r", principal_id="local", intent="create",
            status="AWAITING_APPROVAL", current_revision=1,
            request_snapshot={}, source_reference={},
        ))
        session.add(StoryPlanRevisionModel(
            plan_id="p", revision=1, status="AWAITING_APPROVAL",
            plan_json={}, fingerprint="x", character_snapshot=[], source_snapshot={},
        ))
        session.commit()
        assert session.query(StoryPlanRevisionModel).filter_by(plan_id="p").count() == 1
