"""Phase 10 approved-plan workflow and performance contract."""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from drama_engine.persistence.models import Base
from drama_engine.config import RuleLibrary
from drama_engine.templates.models import StoryTemplateSpec, TemplateBeat
from drama_engine.templates.repository import MemoryStoryTemplateRepository
from drama_engine.templates.resolver import StoryTemplateResolver
from drama_engine.workflow.performance import normalize_utterance, validate_story_delivery, validate_utterance
from drama_engine.workflow.adapters import (
    DraftEpisode, DraftLine, LLMPerformanceAnnotator, draft_episode_contract_errors,
    spoken_character_budget, validate_generated_plan_cast,
    resolve_line_cast, validate_generated_plan_ending, validate_generated_plan_causality,
    normalize_draft_stage_directions,
)
from drama_engine.workflow.creative_context import build_episode_creative_packet
from drama_engine.workflow.repository import (
    InMemoryWorkflowRepository,
    SqlAlchemyWorkflowRepository,
    WorkflowConflictError,
)
from drama_engine.workflow.schemas import (
    CharacterSelection,
    CharacterPortrayal,
    CharacterSnapshot,
    ContentRating,
    CreateStoryRequest,
    CreationPreferences,
    EmphasisSpan,
    EpisodeDelivery,
    EpisodeOutline,
    FictionalVoiceRole,
    MysteryCausalProof,
    OperationContext,
    PlanContent,
    PlanStatus,
    SceneDelivery,
    SourceSelector,
    StoryDelivery,
    StoryTemplateRef,
    StoryVersionStatus,
    Utterance,
    WorkflowJobStatus,
)
from drama_engine.workflow.service import StoryWorkflowService


class FixedPlanGenerator:
    def generate(self, request, source, characters, previous=None, feedback="", template=None, strategy=None):
        prefs = request.creation_preferences
        suffix = f"（{feedback}）" if feedback else ""
        return PlanContent(
            title=f"测试故事{suffix}",
            premise=f"围绕 {request.user_instruction} 展开",
            theme="选择与成长",
            beginning="主角接到任务",
            development="主角遭遇阻碍",
            climax="主角作出关键选择",
            ending="冲突得到阶段性解决",
            episode_outlines=[
                EpisodeOutline(
                    index=i,
                    title=f"第{i}集",
                    core_goal=f"完成第{i}阶段目标",
                    major_beats=["出现问题", "采取行动", "承担结果"],
                    ending="留下新的理解",
                    target_duration_sec=prefs.target_duration_sec,
                )
                for i in range(1, prefs.target_episodes + 1)
            ],
            performance_plan={"format": "audio_drama"},
            fictional_voice_roles=[FictionalVoiceRole(role_id="hero", story_name="林岚",
                performer_id="hero", medium="live", dramatic_purpose="推动行动")],
            continuity_constraints=[source.story_summary] if source else [],
            change_summary=[feedback] if feedback else [],
        )


class FixedStoryExecutor:
    def __init__(self, valid=True):
        self.valid = valid

    def execute(self, snapshot):
        prefs = snapshot.preview.resolved_preferences
        source = snapshot.preview.source_reference
        episodes = []
        for outline in snapshot.preview.plan.episode_outlines:
            index = outline.index
            utterance = Utterance(
                line_id=f"line-{index}",
                kind="dialogue",
                speaker_role_id="hero",
                performer_id="hero",
                text="我会认真完成这次任务。",
                emotion="serious" if self.valid else None,
                tone_instruction="沉稳而坚定" if self.valid else None,
                emphasis=[EmphasisSpan(span_text="认真", strength="strong")] if self.valid else [],
            )
            if self.valid:
                utterance = normalize_utterance(utterance)
            episodes.append(EpisodeDelivery(
                episode_id=f"ep-{index}", index=index, title=outline.title,
                duration_target_sec=prefs.target_duration_sec,
                scenes=[SceneDelivery(scene_id=f"scene-{index}", title="任务", utterances=[utterance])],
                validation_status="PASSED", ready_for_playback=True,
            ))
        generated_ids = [ep.episode_id for ep in episodes]
        if source and snapshot.request["intent"] == "continue":
            inherited = StoryDelivery.model_validate(source.source_content_snapshot["delivery"])
            episodes = [*inherited.episodes, *episodes]
        delivery = StoryDelivery(
            story_id=source.story_id if source and snapshot.request["intent"] in ("continue", "revise") else uuid.uuid4().hex,
            story_version_id=uuid.uuid4().hex,
            version_no=source.version_no + 1 if source and snapshot.request["intent"] in ("continue", "revise") else 1,
            parent_story_version_id=source.story_version_id if source else None,
            lineage_type=snapshot.request["intent"],
            approved_plan_id=snapshot.plan_id,
            approved_plan_revision=snapshot.plan_revision,
            title=snapshot.preview.plan.title,
            summary=snapshot.preview.plan.premise,
            preview_blurb="一位城市居民面临新的选择，必须在现实阻碍中找到自己的方向。",
            story_mode=prefs.story_mode,
            outline=snapshot.preview.plan,
            fictional_voice_roles=[v.model_dump() for v in snapshot.preview.plan.fictional_voice_roles],
            episodes=episodes,
            generated_episode_ids=generated_ids,
            quality_report={"validation_status": "FAILED"},
        )
        report = validate_story_delivery(delivery)
        return delivery.model_copy(update={
            "quality_report": report,
            "ready_for_playback": report.validation_status == "PASSED",
            "status": StoryVersionStatus.READY if report.validation_status == "PASSED" else StoryVersionStatus.CANDIDATE,
        })


def make_request(**preference_overrides):
    prefs = CreationPreferences(**preference_overrides)
    return CreateStoryRequest(
        request_id=uuid.uuid4().hex,
        idempotency_key=uuid.uuid4().hex,
        user_instruction="一个人在城市里重新找到生活方向",
        creation_preferences=prefs,
    )


def make_service(repository=None, valid=True, template_resolver=None):
    return StoryWorkflowService(
        repository or InMemoryWorkflowRepository(),
        FixedPlanGenerator(),
        FixedStoryExecutor(valid=valid),
        template_resolver=template_resolver,
    )


class TestRequestContract:
    @pytest.mark.parametrize("duration", [120, 180, 300])
    def test_product_duration_beat_sheet_is_contiguous(self, duration):
        sheet = RuleLibrary.load().beat_sheet_for(duration)
        assert sheet[0]["range"][0] == 0
        assert sheet[-1]["range"][1] == duration
        assert all(left["range"][1] == right["range"][0] for left, right in zip(sheet, sheet[1:]))

    def test_teen_cannot_request_mature_content(self):
        with pytest.raises(ValidationError):
            CreationPreferences(audience_band="14-17", content_rating="mature_non_explicit")

    def test_include_avoid_conflict_rejected(self):
        with pytest.raises(ValidationError):
            CreationPreferences(must_include=["犯罪"], must_avoid=["犯罪"])

    @pytest.mark.parametrize("duration", [90, 120, 150, 180, 300])
    def test_supported_duration_contract(self, duration):
        assert CreationPreferences(target_duration_sec=duration).target_duration_sec == duration

    def test_audio_drama_is_only_public_form(self):
        with pytest.raises(ValidationError):
            CreationPreferences(content_form="novel")


class TestPlanApprovalWorkflow:
    def test_story_portrayal_is_approved_without_changing_canon(self):
        service = make_service()
        context = OperationContext(character_snapshots={
            "toy": {"canon": {"name": "角色1", "immutable_facts": ["小狐狸玩具"]},
                    "profile": {}, "profile_revision": 1},
        })
        request = make_request().model_copy(update={
            "characters": CharacterSelection(
                selected_character_ids=["toy"],
                role_bindings=[{"character_id": "toy", "story_role_id": "hero", "performer_id": "voice-toy"}],
            ),
        })
        plan = service.prepare_story_plan(request, context)
        assert plan.character_snapshots[0].canon["name"] == "角色1"
        job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "portrayal")
        assert job.input_snapshot.preview.character_snapshots[0].canon["name"] == "角色1"

    def test_generated_plan_requires_named_approved_portrayals(self):
        content = FixedPlanGenerator().generate(make_request(), None, [])
        character = CharacterSnapshot(character_id="toy", role_id="hero", canon={"name": "角色1"})
        # Empty portrayals, provided character → missing
        with pytest.raises(ValueError, match="PLAN_CHARACTER_PORTRAYALS_MISMATCH"):
            validate_generated_plan_cast(content, [character])
        portrayal = CharacterPortrayal(
            role_id="hero", story_name="角色1", external_goal="找回旧表",
            emotional_avoidance="不愿提起离别", speech_style="短句，喜欢反问",
            relationship_stance="对爷爷的叮嘱既依赖又反抗",
        )
        with pytest.raises(ValueError, match="PLAN_PLACEHOLDER_STORY_NAME"):
            validate_generated_plan_cast(content.model_copy(update={"character_portrayals": [portrayal]}), [character])
        named = portrayal.model_copy(update={"story_name": "林星"})
        voice = FictionalVoiceRole(
            role_id="recorded-grandfather", story_name="爷爷", performer_id="voice-grandfather",
            medium="recording", dramatic_purpose="已铺垫的旧表留言",
        )
        valid = content.model_copy(update={
            "character_portrayals": [named], "fictional_voice_roles": [voice],
        })
        validate_generated_plan_cast(valid, [character])
        with pytest.raises(ValueError, match="PLAN_VOICE_ROLE_CONFLICT"):
            validate_generated_plan_cast(valid.model_copy(update={
                "fictional_voice_roles": [voice.model_copy(update={"role_id": "hero"})],
            }), [character])

    def test_one_shot_packet_prioritizes_closure(self):
        service = make_service()
        plan = service.prepare_story_plan(make_request(target_duration_sec=120))
        job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "closure")
        packet = build_episode_creative_packet(job.input_snapshot, plan.plan.episode_outlines[0], 357, 420, 483)
        assert "最后约15%" in packet.ending_contract
        assert "隐藏留言" in packet.ending_contract

    def test_closed_plan_rejects_old_convenience_tail_reveals(self):
        prefs = CreationPreferences(target_duration_sec=120, story_mode="general")
        content = FixedPlanGenerator().generate(make_request(target_duration_sec=120), None, [])
        episode = content.episode_outlines[0]
        for last_beat in (
            "母亲突然留下一段新的留言，戛然而止",
            "陈宇翻到背面，发现隐藏留言，随后结束",
        ):
            bad = content.model_copy(update={
                "episode_outlines": [episode.model_copy(update={
                    "major_beats": [*episode.major_beats[:-1], last_beat],
                })],
            })
            with pytest.raises(ValueError, match="PLAN_CLOSED_ENDING_TAIL_REVEAL"):
                validate_generated_plan_ending(bad, prefs)
        validate_generated_plan_ending(content, prefs)
        validate_generated_plan_ending(bad, CreationPreferences(story_mode="serialized"))

    def test_mystery_proof_is_approved_and_forwarded_to_writer(self):
        prefs = CreationPreferences(story_mode="mystery")
        content = FixedPlanGenerator().generate(make_request(story_mode="mystery"), None, [])
        with pytest.raises(ValueError, match="PLAN_MYSTERY_CAUSAL_PROOF_MISSING"):
            validate_generated_plan_causality(content, prefs)
        proof = MysteryCausalProof(
            actual_event_timeline="闭馆前陈宇先把书放回原架，铃铛在闭馆后被猫碰响",
            witnessed_event="林晓只听见铃响，随后才看到书在架上，没有目睹移动",
            actor_opportunity="陈宇在林晓到馆前独自整理书架，之后一直和她在一起",
            audible_trace_before_reveal="两人先听见猫爪抓挠的声音和铃铛绳子拖动声",
            onstage_verification="林晓把铃铛重新架好，猫再次碰响而书没有移动",
        )
        content = content.model_copy(update={"mystery_causal_proof": proof})
        validate_generated_plan_causality(content, prefs)
        service = make_service()
        plan = service.prepare_story_plan(make_request(story_mode="mystery"))
        approved_plan = plan.model_copy(update={"plan": content})
        job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "mystery-proof")
        snapshot = job.input_snapshot.model_copy(update={"preview": approved_plan})
        packet = build_episode_creative_packet(snapshot, content.episode_outlines[0], 357, 420, 483)
        assert packet.mystery_causal_proof == proof.model_dump(mode="json")

    def test_recorded_voice_keeps_person_and_medium(self):
        service = make_service()
        plan = service.prepare_story_plan(make_request())
        voice = FictionalVoiceRole(
            role_id="grandfather-recording", story_name="爷爷", performer_id="voice-grandfather",
            medium="recording", dramatic_purpose="旧表留言",
        )
        preview = plan.model_copy(update={
            "plan": plan.plan.model_copy(update={"fictional_voice_roles": [voice]}),
        })
        line = DraftLine(kind="dialogue", speaker_role_id="grandfather-recording", text="星子，别修了。")
        assert resolve_line_cast(line, preview) == ("voice-grandfather", "recording")
        assert resolve_line_cast(DraftLine(kind="narration", text="钟表停了。"), preview) == ("narrator", None)

    def test_approved_plan_builds_deterministic_scene_level_creation_packet(self):
        service = make_service()
        plan = service.prepare_story_plan(make_request(target_duration_sec=180, dialogue_density="high"))
        job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "packet")
        packet = build_episode_creative_packet(
            job.input_snapshot, plan.plan.episode_outlines[0], 536, 630, 724,
        )
        assert sum(card.spoken_character_budget for card in packet.scene_cards) == 630
        assert all(card.required_beats for card in packet.scene_cards)
        assert packet.audio_time_budget_sec["independent_transition_advisory"] > 0
        assert packet.audio_time_budget_sec["spoken_text_gate_estimate"] == 180
        assert packet.spoken_character_budget == {"minimum": 536, "ideal": 630, "maximum": 724}

    def test_explicit_template_is_frozen_in_plan_and_job(self):
        templates = MemoryStoryTemplateRepository()
        templates.create(StoryTemplateSpec(
            template_id="SPY_TURN", version=1, name="谍战转折",
            supported_modes=["mystery"],
            beats=[TemplateBeat(key="false_clue", purpose="假线索诱导错误判断")],
        ))
        service = make_service(template_resolver=StoryTemplateResolver(templates))
        request = make_request(story_mode="mystery").model_copy(update={
            "template_ref": StoryTemplateRef(template_id="SPY_TURN", template_revision=1),
        })
        plan = service.prepare_story_plan(request)
        assert plan.template_snapshot["beats"][0]["key"] == "false_clue"
        job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "template")
        assert job.input_snapshot.preview.template_snapshot == plan.template_snapshot

    def test_explicit_template_needs_resolver(self):
        request = make_request(story_mode="mystery").model_copy(update={
            "template_ref": StoryTemplateRef(template_id="SPY_TURN", template_revision=1),
        })
        with pytest.raises(WorkflowConflictError, match="TEMPLATE_RESOLVER_NOT_CONFIGURED"):
            make_service().prepare_story_plan(request)

    def test_create_returns_plan_without_story(self):
        service = make_service()
        plan = service.prepare_story_plan(make_request())
        assert plan.status == PlanStatus.AWAITING_APPROVAL
        assert plan.requires_confirmation is True
        assert service.get_generation_job("missing") is None

    def test_auto_mode_is_resolved_and_frozen(self):
        service = make_service()
        plan = service.prepare_story_plan(make_request(genre="轻悬疑推理"))
        assert plan.resolved_preferences.story_mode in ("mystery", "general")
        assert "story_mode" in plan.auto_filled_fields
        # Strategy snapshot should be present
        assert plan.strategy_snapshot is not None
        assert plan.strategy_snapshot.get("story_mode") == plan.resolved_preferences.story_mode

    def test_revision_invalidates_old_revision(self):
        service = make_service()
        plan = service.prepare_story_plan(make_request())
        revised = service.revise_story_plan(plan.plan_id, 1, "结局更有希望")
        assert revised.plan_revision == 2
        assert revised.plan.title.endswith("（结局更有希望）")
        with pytest.raises(WorkflowConflictError, match="STALE_PLAN"):
            service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "old")

    def test_approval_is_idempotent(self):
        service = make_service()
        plan = service.prepare_story_plan(make_request())
        first = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "same-key")
        second = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "same-key")
        assert first.job_id == second.job_id

    def test_idempotency_key_cannot_be_reused_for_another_plan(self):
        service = make_service()
        first = service.prepare_story_plan(make_request())
        second = service.prepare_story_plan(make_request())
        service.approve_story_plan(first.plan_id, 1, first.plan_fingerprint, "shared-key")
        with pytest.raises(WorkflowConflictError, match="IDEMPOTENCY_KEY_CONFLICT"):
            service.approve_story_plan(second.plan_id, 1, second.plan_fingerprint, "shared-key")

    def test_character_change_makes_plan_stale(self):
        repository = InMemoryWorkflowRepository()
        service = make_service(repository)
        context = OperationContext(character_snapshots={
            "hero": {"canon": {"name": "阿澈"}, "profile": {"personality": ["谨慎"]}, "profile_revision": 1}
        })
        request = make_request().model_copy(update={
            "characters": CharacterSelection(selected_character_ids=["hero"])
        })
        plan = service.prepare_story_plan(request, context)
        service.update_character_profile("hero", 0, {"personality": ["果断"]})
        service.update_character_profile("hero", 1, {"personality": ["沉稳"]})
        with pytest.raises(WorkflowConflictError, match="STALE_PLAN"):
            service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "stale")

    def test_canon_fields_cannot_be_changed(self):
        service = make_service()
        with pytest.raises(ValueError, match="CHARACTER_CANON_LOCKED"):
            service.update_character_profile("hero", 0, {"species": "dragon"})


class TestGenerationAndPerformanceGate:
    def test_episode_contract_rejects_short_300_second_draft(self):
        draft = DraftEpisode.model_validate({
            "title": "短稿", "synopsis": "短稿", "episode_summary": "结束",
            "scenes": [{"title": "场景", "dramatic_goal": "推进", "lines": [
                {"kind": "dialogue", "speaker_role_id": "role-1", "text": "你好。" * 240},
            ]}],
        })
        estimated, errors = draft_episode_contract_errors(draft, 300, {"role-1"})
        assert estimated < 255
        assert any("duration estimate" in error for error in errors)

    def test_spoken_character_budget_matches_gate(self):
        assert spoken_character_budget(120) == (357, 420, 483)
        assert spoken_character_budget(300) == (893, 1050, 1207)

    def test_episode_contract_rejects_unapproved_role(self):
        draft = DraftEpisode.model_validate({
            "title": "角色越界", "synopsis": "角色越界", "episode_summary": "结束",
            "scenes": [{"title": "场景", "dramatic_goal": "推进", "lines": [
                {"kind": "dialogue", "speaker_role_id": "role-5", "text": "你好。" * 350},
            ]}],
        })
        _, errors = draft_episode_contract_errors(draft, 300, {"role-1", "role-2"})
        assert any("role-5" in error for error in errors)

    def test_recorded_character_quote_cannot_be_narration(self):
        draft = DraftEpisode.model_validate({
            "title": "旧表", "synopsis": "旧表", "episode_summary": "结束",
            "scenes": [{"title": "告别", "dramatic_goal": "听完留言", "lines": [
                {"kind": "narration", "text": "（爷爷留言）星子，别修了。"},
                {"kind": "dialogue", "speaker_role_id": "hero", "text": "我听见了。" * 60},
            ]}],
        })
        _, errors = draft_episode_contract_errors(draft, 120, {"hero", "recorded-grandfather"})
        assert any("recorded speech miscast" in error for error in errors)

    def test_stage_direction_is_not_part_of_spoken_text(self):
        draft = DraftEpisode.model_validate({
            "title": "夜查", "synopsis": "夜查", "episode_summary": "结束",
            "scenes": [{"title": "图书馆", "dramatic_goal": "查线索", "lines": [
                {"kind": "dialogue", "speaker_role_id": "hero", "text": "（压低声音）你听见了吗？"},
                {"kind": "sfx", "text": "（脚步声）"},
            ]}],
        })
        normalized = normalize_draft_stage_directions(draft)
        spoken, effect = normalized.scenes[0].lines
        assert spoken.text == "你听见了吗？"
        assert spoken.performance_hint == "压低声音"
        assert effect.text == "（脚步声）"
        assert normalize_draft_stage_directions(normalized) == normalized

    def test_approved_plan_generates_ready_immutable_version(self):
        service = make_service()
        plan = service.prepare_story_plan(make_request(target_episodes=2, target_duration_sec=120))
        job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "generate")
        delivery = service.generate_from_approved_plan(job.job_id)
        assert delivery.ready_for_playback is True
        assert delivery.preview_blurb
        assert delivery.status == StoryVersionStatus.READY
        assert len(delivery.episodes) == 2
        assert service.get_generation_job(job.job_id).status == WorkflowJobStatus.SUCCEEDED
        assert service.get_story_version(delivery.story_version_id) == delivery

    def test_missing_preview_blurb_blocks_playback(self):
        service = make_service()
        plan = service.prepare_story_plan(make_request())
        job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "blurb")
        candidate = FixedStoryExecutor().execute(job.input_snapshot)
        invalid = candidate.model_copy(update={"preview_blurb": ""})
        report = validate_story_delivery(invalid)
        assert report.validation_status == "FAILED"
        assert "preview_blurb is required" in report.warnings
        too_short = candidate.model_copy(update={"preview_blurb": "故事简介"})
        assert validate_story_delivery(too_short).validation_status == "FAILED"

    def test_missing_performance_cue_fails_job(self):
        service = make_service(valid=False)
        plan = service.prepare_story_plan(make_request())
        job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "invalid")
        with pytest.raises(ValueError, match="VALIDATION_FAILED: delivery_validation"):
            service.generate_from_approved_plan(job.job_id)
        assert service.get_generation_job(job.job_id).status == WorkflowJobStatus.FAILED

    @pytest.mark.parametrize("field,value", [
        ("emotion", None), ("tone_instruction", None), ("emphasis", []),
        ("performer_id", None),
    ])
    def test_each_required_cue_independently_blocks_ready(self, field, value):
        class BrokenCueExecutor(FixedStoryExecutor):
            def execute(self, snapshot):
                delivery = super().execute(snapshot)
                episode = delivery.episodes[0]
                scene = episode.scenes[0]
                broken = scene.utterances[0].model_copy(update={field: value})
                scene = scene.model_copy(update={"utterances": [broken]})
                episode = episode.model_copy(update={"scenes": [scene]})
                return delivery.model_copy(update={"episodes": [episode]})

        service = StoryWorkflowService(InMemoryWorkflowRepository(), FixedPlanGenerator(), BrokenCueExecutor())
        plan = service.prepare_story_plan(make_request())
        job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, f"missing-{field}")
        with pytest.raises(ValueError, match="VALIDATION_FAILED"):
            service.generate_from_approved_plan(job.job_id)
        assert service.get_generation_job(job.job_id).status == WorkflowJobStatus.FAILED

    def test_missing_target_episode_cannot_be_ready(self):
        class ShortExecutor(FixedStoryExecutor):
            def execute(self, snapshot):
                delivery = super().execute(snapshot)
                return delivery.model_copy(update={"episodes": delivery.episodes[:1]})

        service = StoryWorkflowService(InMemoryWorkflowRepository(), FixedPlanGenerator(), ShortExecutor())
        plan = service.prepare_story_plan(make_request(target_episodes=2))
        job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "short")
        with pytest.raises(ValueError, match="VALIDATION_FAILED"):
            service.generate_from_approved_plan(job.job_id)
        assert service.get_generation_job(job.job_id).status == WorkflowJobStatus.FAILED

    def test_cancel_request_prevents_publishing_after_generation(self):
        class CancellingExecutor(FixedStoryExecutor):
            def execute(self, snapshot):
                service.cancel_generation_job(job.job_id)
                return super().execute(snapshot)

        service = StoryWorkflowService(InMemoryWorkflowRepository(), FixedPlanGenerator(), CancellingExecutor())
        plan = service.prepare_story_plan(make_request())
        job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "cancel")
        with pytest.raises(WorkflowConflictError, match="JOB_CANCELLED"):
            service.generate_from_approved_plan(job.job_id)
        assert service.get_generation_job(job.job_id).status == WorkflowJobStatus.CANCELLED

    def test_emphasis_uses_unicode_codepoint_half_open_span(self):
        utterance = normalize_utterance(Utterance(
            line_id="u1", kind="narration", narrator_id="narrator",
            performer_id="narrator",
            text="风吹过城市，风又停了。", emotion="calm", tone_instruction="轻缓",
            emphasis=[EmphasisSpan(span_text="风", occurrence=2, strength="strong")],
        ))
        span = utterance.emphasis[0]
        assert utterance.text[span.start_char:span.end_char] == "风"
        assert span.start_char == 6
        assert validate_utterance(utterance) == []

    def test_sfx_does_not_require_spoken_cues(self):
        assert validate_utterance(Utterance(line_id="sfx1", kind="sfx", audio_cue_ref="door")) == []

    def test_known_audio_aliases_are_normalized_but_unknown_kind_fails(self):
        class NoCallProvider:
            def complete_structured(self, *args, **kwargs):
                raise AssertionError("non-spoken cue must not invoke the model")

        annotator = LLMPerformanceAnnotator(NoCallProvider())
        output = annotator.annotate([{"line_id": "cue", "kind": "bgm", "text": "轻音乐"}], {})
        assert output[0].kind == "music"
        with pytest.raises(ValueError, match="unsupported utterance kinds"):
            annotator.annotate([{"line_id": "bad", "kind": "laser", "text": ""}], {})


class TestSourcesAndLineage:
    def test_continue_uses_exact_source_and_increments_version(self):
        service = make_service()
        create_plan = service.prepare_story_plan(make_request())
        create_job = service.approve_story_plan(
            create_plan.plan_id, 1, create_plan.plan_fingerprint, "create"
        )
        first = service.generate_from_approved_plan(create_job.job_id)
        continue_request = {
            "intent": "continue",
            "request_id": "continue-1",
            "idempotency_key": "continue-idem",
            "user_instruction": "主角第二天继续调查",
            "source_selector": {"type": "by_id", "story_version_id": first.story_version_id},
            "creation_preferences": {"target_duration_sec": 180},
        }
        plan = service.prepare_story_plan(continue_request)
        job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "continue-job")
        second = service.generate_from_approved_plan(job.job_id)
        assert second.parent_story_version_id == first.story_version_id
        assert second.story_id == first.story_id
        assert second.version_no == 2

    def test_last_played_resolves_playback_position(self):
        service = make_service()
        plan = service.prepare_story_plan(make_request())
        job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "p")
        story = service.generate_from_approved_plan(job.job_id)
        service.record_playback_event({
            "story_version_id": story.story_version_id,
            "episode_id": story.episodes[0].episode_id,
            "playback_position_ms": 42000,
            "event_type": "progress",
        })
        source = service.resolve_story_reference(SourceSelector(type="last_played"))
        assert source.story_version_id == story.story_version_id
        assert source.last_played_position_ms == 42000

    def test_last_generated_source_change_invalidates_plan(self):
        service = make_service()
        first_plan = service.prepare_story_plan(make_request())
        first_job = service.approve_story_plan(first_plan.plan_id, 1, first_plan.plan_fingerprint, "source-one")
        service.generate_from_approved_plan(first_job.job_id)
        continuation = service.prepare_story_plan({
            "intent": "continue", "request_id": "pending-continue",
            "idempotency_key": "pending-continue", "user_instruction": "下一集",
            "source_selector": {"type": "last_generated"},
        })
        second_plan = service.prepare_story_plan(make_request())
        second_job = service.approve_story_plan(second_plan.plan_id, 1, second_plan.plan_fingerprint, "source-two")
        service.generate_from_approved_plan(second_job.job_id)
        with pytest.raises(WorkflowConflictError, match="STALE_PLAN"):
            service.approve_story_plan(continuation.plan_id, 1, continuation.plan_fingerprint, "stale-source")

    def test_continue_playback_is_not_a_generation_plan(self):
        service = make_service()
        with pytest.raises(WorkflowConflictError, match="PLAYBACK_RESUME"):
            service.prepare_story_plan({
                "intent": "continue", "request_id": "playback", "idempotency_key": "playback",
                "user_instruction": "继续", "continuation_kind": "resume_playback",
                "source_selector": {"type": "last_played"},
            })

    def test_revise_tracks_downstream_impact_and_remix_branches(self):
        service = make_service()
        plan = service.prepare_story_plan(make_request(target_episodes=2))
        job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "original")
        original = service.generate_from_approved_plan(job.job_id)
        revised_plan = service.prepare_story_plan({
            "intent": "revise", "request_id": "revise", "idempotency_key": "revise",
            "user_instruction": "调整第一集的行动", "source_selector": {
                "type": "by_id", "story_version_id": original.story_version_id,
            },
            "creation_preferences": {"target_episodes": 2},
            "edit": {
                "scope": "episode", "target": {"episode_id": original.episodes[0].episode_id},
                "instruction": "更明确的选择", "downstream_policy": "replan_affected",
            },
        })
        assert revised_plan.impact_analysis["affected_episode_indexes"] == [1, 2]
        remix_plan = service.prepare_story_plan({
            "intent": "remix", "request_id": "remix", "idempotency_key": "remix",
            "user_instruction": "改为平行世界", "source_selector": {
                "type": "by_id", "story_version_id": original.story_version_id,
            },
            "remix": {
                "instruction": "改为平行世界", "retain_elements": ["主角"],
                "transform_elements": ["结局"], "relation_to_source": "parallel_world",
            },
        })
        remix_job = service.approve_story_plan(remix_plan.plan_id, 1, remix_plan.plan_fingerprint, "remix-job")
        remixed = service.generate_from_approved_plan(remix_job.job_id)
        assert remixed.parent_story_version_id == original.story_version_id
        assert remixed.story_id != original.story_id


class TestSqlRepository:
    def test_template_snapshot_survives_sqlite_restart(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'template-workflow.db'}")
        Base.metadata.create_all(engine)
        templates = MemoryStoryTemplateRepository()
        templates.create(StoryTemplateSpec(
            template_id="SPY_TURN", version=1, name="谍战转折",
            supported_modes=["mystery"],
            beats=[TemplateBeat(key="false_clue", purpose="假线索")],
        ))
        request = make_request(story_mode="mystery").model_copy(update={
            "template_ref": StoryTemplateRef(template_id="SPY_TURN", template_revision=1),
        })
        with Session(engine) as session:
            service = make_service(
                SqlAlchemyWorkflowRepository(session),
                template_resolver=StoryTemplateResolver(templates),
            )
            plan = service.prepare_story_plan(request)
        with Session(engine) as session:
            service = make_service(SqlAlchemyWorkflowRepository(session))
            restored = service.get_story_plan(plan.plan_id)
            assert restored.template_snapshot == plan.template_snapshot
            job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "template-sql")
            assert job.input_snapshot.preview.template_snapshot == plan.template_snapshot
        engine.dispose()

    def test_sqlite_roundtrip_and_approval_restart(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'workflow.db'}")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            service = make_service(SqlAlchemyWorkflowRepository(session))
            plan = service.prepare_story_plan(make_request())
            job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "sql-idem")
            session.commit()
            job_id = job.job_id
        with Session(engine) as session:
            service = make_service(SqlAlchemyWorkflowRepository(session))
            restored = service.get_generation_job(job_id)
            assert restored.status == WorkflowJobStatus.QUEUED
            delivery = service.generate_from_approved_plan(job_id)
            session.commit()
            version_id = delivery.story_version_id
        with Session(engine) as session:
            service = make_service(SqlAlchemyWorkflowRepository(session))
            restored_story = service.get_story_version(version_id)
            assert restored_story.ready_for_playback is True
            assert restored_story.preview_blurb

    def test_sqlite_idempotency_and_principal_isolation(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'isolation.db'}")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            service = make_service(SqlAlchemyWorkflowRepository(session))
            alice = OperationContext(principal_id="alice")
            bob = OperationContext(principal_id="bob")
            plan = service.prepare_story_plan(make_request(), alice)
            other = service.prepare_story_plan(make_request(), bob)
            job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "unique-key")
            assert service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "unique-key").job_id == job.job_id
            with pytest.raises(WorkflowConflictError, match="IDEMPOTENCY_KEY_CONFLICT"):
                service.approve_story_plan(other.plan_id, 1, other.plan_fingerprint, "unique-key")
            delivery = service.generate_from_approved_plan(job.job_id)
            session.commit()
        with Session(engine) as session:
            service = make_service(SqlAlchemyWorkflowRepository(session))
            assert service.resolve_story_reference(
                SourceSelector(type="by_id", story_version_id=delivery.story_version_id), alice
            ).story_version_id == delivery.story_version_id
            with pytest.raises(KeyError, match="SOURCE_NOT_FOUND"):
                service.resolve_story_reference(
                    SourceSelector(type="by_id", story_version_id=delivery.story_version_id), bob
                )

    def test_failed_generation_state_survives_session_restart(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'failed.db'}")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            service = make_service(SqlAlchemyWorkflowRepository(session), valid=False)
            plan = service.prepare_story_plan(make_request())
            job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, "failed-job")
            with pytest.raises(ValueError, match="VALIDATION_FAILED"):
                service.generate_from_approved_plan(job.job_id)
            job_id = job.job_id
        with Session(engine) as session:
            service = make_service(SqlAlchemyWorkflowRepository(session))
            restored = service.get_generation_job(job_id)
            assert restored.status == WorkflowJobStatus.FAILED
            assert restored.result_story_version_id is None
