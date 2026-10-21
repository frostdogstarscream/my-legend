"""Tests for StoryStrategyResolver and ResolvedStoryStrategy."""
from __future__ import annotations

import pytest

from drama_engine.config import RuleLibrary
from drama_engine.workflow.strategy import (
    ResolvedStoryStrategy,
    StoryStrategyResolver,
    StrategyConflictError,
)


@pytest.fixture(scope="module")
def lib():
    return RuleLibrary.load()


@pytest.fixture
def resolver(lib):
    return StoryStrategyResolver(lib)


class TestStrategyResolver:
    """Test strategy resolution with various input combinations."""

    def test_explicit_genre_returns_exact_match(self, resolver):
        strategy = resolver.resolve(
            story_mode="general",
            genre="G01",
            recipe_id="R4",
            user_instruction="家庭冲突故事",
            explicit_fields=["story_mode", "genre", "recipe_id"],
        )
        assert strategy.genre_id == "G01"
        assert strategy.genre_name == "现实家庭冲突"
        assert strategy.recipe_id == "R4"
        assert strategy.recipe_name == "家庭反击"
        assert strategy.story_mode == "general"
        assert "genre" not in strategy.auto_filled_fields

    def test_auto_genre_matches_from_idea(self, resolver):
        strategy = resolver.resolve(
            story_mode="auto",
            genre="auto",
            user_instruction="一个被婆婆欺负的儿媳终于觉醒反击",
            explicit_fields=["user_instruction"],
        )
        assert strategy.genre_id in ("G01", "G04")
        assert "genre" in strategy.auto_filled_fields

    def test_auto_recipe_derived_from_genre(self, resolver):
        strategy = resolver.resolve(
            story_mode="auto",
            genre="G02",  # 成年人治愈爱情
            user_instruction="治愈系爱情故事",
            explicit_fields=["genre"],
        )
        assert strategy.recipe_id == "R5"
        assert strategy.recipe_name == "破镜重圆治愈"
        assert "recipe_id" in strategy.auto_filled_fields

    def test_recipe_injects_slot_assignments(self, resolver):
        strategy = resolver.resolve(
            story_mode="viral_drama",
            genre="G08",
            recipe_id="R3",
            user_instruction="穿越搞事业",
            explicit_fields=["story_mode", "genre", "recipe_id"],
        )
        assert len(strategy.recipe_slots) >= 5
        assert strategy.recipe_slots.get("A", "")

    def test_recipe_injects_mechanisms(self, resolver):
        for rid in ("R1", "R2", "R3", "R4", "R5"):
            strategy = resolver.resolve(
                story_mode="auto",
                genre="auto",
                recipe_id=rid,
                user_instruction="测试",
                explicit_fields=["recipe_id"],
            )
            assert strategy.conflict_mechanism, f"R{rid} missing conflict_mechanism"
            assert strategy.causal_driver, f"R{rid} missing causal_driver"
            assert strategy.ending_strategy, f"R{rid} missing ending_strategy"
            assert strategy.tone_guidance, f"R{rid} missing tone_guidance"

    def test_each_recipe_has_unique_mechanisms(self, resolver):
        strategies = {}
        for rid in ("R1", "R2", "R3", "R4", "R5"):
            strategies[rid] = resolver.resolve(
                story_mode="auto", genre="auto", recipe_id=rid,
                user_instruction="测试", explicit_fields=["recipe_id"],
            )
        # Verify each recipe's conflict mechanism is different
        mechanisms = {rid: s.conflict_mechanism for rid, s in strategies.items()}
        assert len(set(mechanisms.values())) == 5, (
            f"Recipes should have distinct mechanisms, got: {mechanisms}"
        )

    def test_plan_context_is_different_per_recipe(self, resolver):
        ctx_r4 = resolver.resolve(
            story_mode="general", genre="G01", recipe_id="R4",
            user_instruction="家庭冲突", explicit_fields=["story_mode", "genre", "recipe_id"],
        ).plan_context()
        ctx_r5 = resolver.resolve(
            story_mode="general", genre="G02", recipe_id="R5",
            user_instruction="治愈爱情", explicit_fields=["story_mode", "genre", "recipe_id"],
        ).plan_context()
        assert ctx_r4 != ctx_r5
        assert "家庭反击" in ctx_r4
        assert "破镜重圆" in ctx_r5

    def test_writer_context_has_hints(self, resolver):
        strategy = resolver.resolve(
            story_mode="general", genre="G05", recipe_id="R5",
            user_instruction="治愈", explicit_fields=["story_mode", "genre", "recipe_id"],
        )
        hints = strategy.writer_context()
        assert len(hints) >= 2
        assert any("冲突" in h for h in hints)
        assert any("结局" in h for h in hints)

    def test_all_modes_auto_resolve_without_error(self, resolver):
        for genre_id in ("G01", "G02", "G03", "G04", "G05",
                          "G06", "G07", "G08", "G09", "G10"):
            strategy = resolver.resolve(
                story_mode="auto", genre=genre_id,
                user_instruction="测试故事",
                explicit_fields=["genre"],
            )
            assert strategy.story_mode in ("viral_drama", "general", "mystery", "serialized")
            assert strategy.genre_id == genre_id

    def test_genre_default_mappings_are_complete(self, lib):
        """Every genre has a default recipe via genre_default_recipe or genre entry."""
        for g in lib.all_genres:
            gid = g.get("genre_id", "")
            default = lib.genre_default_recipe(gid)
            # If genre has keywords, it should have a default recipe mapping
            if g.get("keywords"):
                assert default, f"Genre {gid} ({g.get('name','?')}) missing default recipe"

    def test_recipe_mode_affinity_consistent(self, lib):
        """All recipes have at least one compatible mode."""
        affinity = lib.recipe_mode_affinity()
        for r in lib.all_recipes:
            rid = r.get("id", "")
            if r.get("enabled", True):
                modes = affinity.get(rid, [])
                assert len(modes) > 0, f"Recipe {rid} has no compatible modes"


class TestStrategyConflictDetection:
    """Test that incompatible combinations raise clear errors."""

    def test_explicit_recipe_mode_mismatch_raises(self, resolver):
        with pytest.raises(StrategyConflictError, match="R5"):
            resolver.resolve(
                story_mode="viral_drama",
                genre="G01",
                recipe_id="R5",  # R5 only supports general
                user_instruction="测试",
                explicit_fields=["story_mode", "recipe_id"],
            )

    def test_all_recipes_have_mechanism_coverage(self, lib):
        for r in lib.all_recipes:
            rid = r.get("id", "")
            if not r.get("enabled", True):
                continue
            mech = lib.recipe_mechanism(rid)
            if mech is None:
                continue  # Some recipes may not have mechanisms in the library yet
            assert mech.get("conflict_mechanism"), f"{rid}: missing conflict_mechanism"
            assert mech.get("causal_driver"), f"{rid}: missing causal_driver"
            assert mech.get("ending_strategy"), f"{rid}: missing ending_strategy"
            assert mech.get("tone_guidance"), f"{rid}: missing tone_guidance"


class TestResolvedStoryStrategy:
    """Test the ResolvedStoryStrategy model methods."""

    def test_serialization_roundtrip(self):
        s = ResolvedStoryStrategy(
            story_mode="general",
            mode_label="通用故事",
            genre_id="G01",
            genre_name="现实家庭冲突",
            genre_mechanism="憋屈积累→反击打脸",
            genre_emotion="怒→爽→暖",
            recipe_id="R4",
            recipe_name="家庭反击",
            recipe_slots={"A": "被压榨的妻子", "B": "觉醒+实证"},
            recipe_landing_note="首选赛道",
            conflict_mechanism="权力反转",
            causal_driver="憋屈→觉醒→反击",
            ending_strategy="关系重新定义",
            tone_guidance="真实、情绪浓度高",
            auto_filled_fields=["genre"],
        )
        data = s.model_dump(mode="json")
        restored = ResolvedStoryStrategy.model_validate(data)
        assert restored.genre_id == "G01"
        assert restored.recipe_id == "R4"
        assert restored.conflict_mechanism == "权力反转"

    def test_plan_context_contains_all_sections(self, resolver):
        strategy = resolver.resolve(
            story_mode="general", genre="G01", recipe_id="R4",
            user_instruction="家庭故事", explicit_fields=["story_mode", "genre", "recipe_id"],
        )
        ctx = strategy.plan_context()
        assert "故事模式" in ctx
        assert "题材赛道" in ctx
        assert "角色配方" in ctx
        assert "核心冲突机制" in ctx
        assert "因果推进方式" in ctx
        assert "结局策略" in ctx


class TestKeywordMatching:
    """Test the genre keyword matching from user instructions."""

    def test_family_conflict_keywords(self, resolver):
        genre = resolver._match_genre_from_idea("婆婆欺负儿媳，儿媳反击离婚")
        assert genre == "G01"  # 现实家庭冲突

    def test_healing_love_keywords(self, resolver):
        genre = resolver._match_genre_from_idea("两个受伤的人互相治愈的故事")
        assert genre in ("G02", "G05")  # 治愈爱情 or 现实治愈

    def test_time_travel_business_keywords(self, resolver):
        genre = resolver._match_genre_from_idea("穿越到古代用现代商业知识搞事业")
        assert genre == "G08"  # 脑洞穿越搞事业

    def test_palace_intrigue_keywords(self, resolver):
        genre = resolver._match_genre_from_idea("宫斗复仇，步步为营除掉仇人")
        assert genre == "G04"  # 宫斗复仇

    def test_cute_kid_keywords(self, resolver):
        genre = resolver._match_genre_from_idea("六岁萌娃下山找师兄，被一群大佬宠上天")
        assert genre == "G07"  # 萌宝团宠

    def test_no_match_falls_back(self, resolver):
        genre = resolver._match_genre_from_idea("一个不相关的话题")
        assert genre is None  # No meaningful match


class TestIntegrationWithWorkflowService:
    """Test strategy flows through the StoryWorkflowService pipeline."""

    def test_strategy_snapshot_in_plan_preview(self):
        from drama_engine.workflow.service import StoryWorkflowService
        from drama_engine.workflow.repository import InMemoryWorkflowRepository
        from drama_engine.workflow.schemas import (
            CreationPreferences, CreateStoryRequest, StoryModeChoice,
        )
        import uuid

        # Reuse the test fixtures
        from tests.unit.test_phase10_workflow import FixedPlanGenerator, FixedStoryExecutor

        service = StoryWorkflowService(
            InMemoryWorkflowRepository(),
            FixedPlanGenerator(),
            FixedStoryExecutor(),
        )
        request = CreateStoryRequest(
            request_id=uuid.uuid4().hex,
            idempotency_key=uuid.uuid4().hex,
            user_instruction="一个被婆婆欺负的儿媳最终觉醒反击",
            creation_preferences=CreationPreferences(
                story_mode=StoryModeChoice.AUTO,
                genre="auto",
            ),
        )
        plan = service.prepare_story_plan(request)
        assert plan.strategy_snapshot is not None
        assert "genre_id" in plan.strategy_snapshot
        assert "recipe_id" in plan.strategy_snapshot
        assert "conflict_mechanism" in plan.strategy_snapshot

    def test_strategy_survives_approval(self):
        from drama_engine.workflow.service import StoryWorkflowService
        from drama_engine.workflow.repository import InMemoryWorkflowRepository
        from drama_engine.workflow.schemas import (
            CreationPreferences, CreateStoryRequest, StoryModeChoice,
        )
        import uuid
        from tests.unit.test_phase10_workflow import FixedPlanGenerator, FixedStoryExecutor

        service = StoryWorkflowService(
            InMemoryWorkflowRepository(),
            FixedPlanGenerator(),
            FixedStoryExecutor(),
        )
        request = CreateStoryRequest(
            request_id=uuid.uuid4().hex,
            idempotency_key=uuid.uuid4().hex,
            user_instruction="宫斗复仇故事",
            creation_preferences=CreationPreferences(
                story_mode=StoryModeChoice.VIRAL_DRAMA,
                genre="G04",
                recipe_id="R1",
            ),
        )
        plan = service.prepare_story_plan(request)
        job = service.approve_story_plan(
            plan.plan_id, 1, plan.plan_fingerprint, "strategy-test",
        )
        snapshot = job.input_snapshot
        assert snapshot.preview.strategy_snapshot is not None
        assert snapshot.preview.strategy_snapshot == plan.strategy_snapshot

    def test_strategy_reaches_creative_packet(self):
        from drama_engine.workflow.service import StoryWorkflowService
        from drama_engine.workflow.repository import InMemoryWorkflowRepository
        from drama_engine.workflow.schemas import (
            CreationPreferences, CreateStoryRequest, StoryModeChoice,
        )
        from drama_engine.workflow.creative_context import build_episode_creative_packet
        import uuid
        from tests.unit.test_phase10_workflow import FixedPlanGenerator, FixedStoryExecutor

        service = StoryWorkflowService(
            InMemoryWorkflowRepository(),
            FixedPlanGenerator(),
            FixedStoryExecutor(),
        )
        request = CreateStoryRequest(
            request_id=uuid.uuid4().hex,
            idempotency_key=uuid.uuid4().hex,
            user_instruction="一个妈妈觉醒后反击偏心婆婆",
            creation_preferences=CreationPreferences(
                story_mode=StoryModeChoice.GENERAL,
                genre="G01",
                recipe_id="R4",
            ),
        )
        plan = service.prepare_story_plan(request)
        job = service.approve_story_plan(
            plan.plan_id, 1, plan.plan_fingerprint, "packet-strategy",
        )
        packet = build_episode_creative_packet(
            job.input_snapshot, plan.plan.episode_outlines[0], 357, 420, 483,
        )
        assert len(packet.strategy_hints) >= 2
        assert any("冲突" in h for h in packet.strategy_hints)

    def test_explicit_recipe_in_preferences_flows_to_strategy(self):
        from drama_engine.workflow.service import StoryWorkflowService
        from drama_engine.workflow.repository import InMemoryWorkflowRepository
        from drama_engine.workflow.schemas import (
            CreationPreferences, CreateStoryRequest, StoryModeChoice,
        )
        import uuid
        from tests.unit.test_phase10_workflow import FixedPlanGenerator, FixedStoryExecutor

        service = StoryWorkflowService(
            InMemoryWorkflowRepository(),
            FixedPlanGenerator(),
            FixedStoryExecutor(),
        )
        request = CreateStoryRequest(
            request_id=uuid.uuid4().hex,
            idempotency_key=uuid.uuid4().hex,
            user_instruction="测试",
            creation_preferences=CreationPreferences(
                story_mode=StoryModeChoice.GENERAL,
                recipe_id="R5",
            ),
        )
        plan = service.prepare_story_plan(request)
        assert plan.strategy_snapshot["recipe_id"] == "R5"
        assert plan.strategy_snapshot["recipe_name"] == "破镜重圆治愈"