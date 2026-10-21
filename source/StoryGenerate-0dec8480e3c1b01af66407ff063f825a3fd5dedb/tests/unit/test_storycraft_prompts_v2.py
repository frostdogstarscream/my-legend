"""Offline smoke tests for the StoryCraft v2 prompt wiring.

No external model calls; literary quality must be validated by a separate,
fixed-case blind A/B against actual model output.
"""
from __future__ import annotations

import inspect

from drama_engine.config import PROMPT_VERSION
from drama_engine.llm.router import MODEL_ROUTING, TIER_BALANCED, TIER_STRONG
from drama_engine.workflow import adapters
from drama_engine.workflow.storycraft_prompts import (
    PLAN_SYSTEM_V2, EPISODE_SYSTEM_V2, PERFORMANCE_SYSTEM_V2, JUDGE_SYSTEM_V2
)


def test_version_is_bumped_for_new_prompt_snapshot():
    assert PROMPT_VERSION == "p1.6.11"


def test_approved_story_writer_uses_strong_tier_without_changing_legacy_writer():
    assert MODEL_ROUTING["storycraft_episode_writer"]["tier"] == TIER_STRONG
    assert MODEL_ROUTING["episode_writer"]["tier"] == TIER_BALANCED


def test_planner_prompt_has_causality_and_confirmation_boundaries():
    assert len(PLAN_SYSTEM_V2) > 500
    for clause in ("大纲", "审批", "因果", "major_beats", "mystery", "serialized", "revision_feedback"):
        assert clause in PLAN_SYSTEM_V2


def test_episode_prompt_requires_audible_evidence_and_character_agency():
    assert len(EPISODE_SYSTEM_V2) > 700
    for clause in ("已审批", "角色选择", "major_beat", "speaker_role_id", "Unicode", "fact_delta", "preview_blurb"):
        assert clause in EPISODE_SYSTEM_V2
    assert "creative_packet" in EPISODE_SYSTEM_V2


def test_annotator_keeps_text_unchanged_and_requires_resolvable_emphasis():
    for clause in ("不得增删", "line_id", "span_text", "occurrence", "speech_rate"):
        assert clause in PERFORMANCE_SYSTEM_V2


def test_judge_cannot_claim_success_with_vague_praise():
    for clause in ("outline_alignment_passed", "continuity_passed", "content_rating_passed", "证据", "scene/line"):
        assert clause in JUDGE_SYSTEM_V2


def test_official_workflow_uses_v2_prompts():
    assert "system = PLAN_SYSTEM_V2" in inspect.getsource(adapters.LLMPlanGenerator.generate)
    assert "system = EPISODE_SYSTEM_V2" in inspect.getsource(adapters.ApprovedPlanLLMExecutor.execute)
    assert 'resolve_spec("storycraft_episode_writer"' in inspect.getsource(adapters.ApprovedPlanLLMExecutor.execute)
    assert "system = PERFORMANCE_SYSTEM_V2" in inspect.getsource(adapters.LLMPerformanceAnnotator.annotate)
    assert "JUDGE_SYSTEM_V2" in inspect.getsource(adapters.ApprovedPlanLLMExecutor.execute)
