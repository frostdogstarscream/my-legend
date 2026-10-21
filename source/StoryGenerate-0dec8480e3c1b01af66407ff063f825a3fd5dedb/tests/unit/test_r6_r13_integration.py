"""Integration tests for R6-R13 recipes and G11-G14 genres.

Verifies:
- R6-R13 load from extension library and produce valid ResolvedStoryStrategy
- G11-G14 genres have valid default recipes and keyword mappings
- All recipes have distinct mechanism data
- Mode compatibility is consistent
- Free-text genre keywords map correctly
"""
from __future__ import annotations

import pytest

from drama_engine.config import RuleLibrary
from drama_engine.workflow.strategy import (
    StoryStrategyResolver,
    StrategyConflictError,
    ResolvedStoryStrategy,
)


@pytest.fixture(scope="module")
def lib():
    return RuleLibrary.load()


@pytest.fixture
def resolver(lib):
    return StoryStrategyResolver(lib)


class TestR6toR13Loading:
    """Verify all R6-R13 recipes load from extension library."""

    def test_all_extension_recipes_loaded(self, lib):
        ext_recipes = lib.formula_ext.get("roster_recipes_extension", [])
        assert len(ext_recipes) == 8, f"Expected 8 extension recipes, got {len(ext_recipes)}"
        recipe_ids = {r["id"] for r in ext_recipes}
        for rid in ("R6", "R7", "R8", "R9", "R10", "R11", "R12", "R13"):
            assert rid in recipe_ids, f"Missing {rid}"

    def test_all_extension_recipes_enabled(self, lib):
        for r in lib.formula_ext.get("roster_recipes_extension", []):
            assert r.get("enabled", False), f"{r['id']} not enabled"

    def test_all_recipes_have_mechanisms(self, lib):
        for r in lib.all_recipes:
            rid = r.get("id", "")
            if not r.get("enabled", True):
                continue
            mech = lib.recipe_mechanism(rid)
            assert mech is not None, f"{rid}: no mechanism"
            assert len(mech.get("conflict_mechanism", "")) > 20, f"{rid}: conflict_mechanism too short"
            assert len(mech.get("causal_driver", "")) > 20, f"{rid}: causal_driver too short"
            assert len(mech.get("ending_strategy", "")) > 20, f"{rid}: ending_strategy too short"
            assert len(mech.get("tone_guidance", "")) > 10, f"{rid}: tone_guidance too short"

    def test_all_recipes_have_mode_affinity(self, lib):
        affinity = lib.recipe_mode_affinity()
        for rid in ("R6", "R7", "R8", "R9", "R10", "R11", "R12", "R13"):
            modes = affinity.get(rid, [])
            assert len(modes) > 0, f"{rid}: no mode affinity"

    def test_all_recipe_mechanisms_distinct(self, lib):
        mechanisms = {}
        for r in lib.all_recipes:
            rid = r.get("id", "")
            if not r.get("enabled", True):
                continue
            mech = lib.recipe_mechanism(rid)
            if mech:
                mechanisms[rid] = mech["conflict_mechanism"]
        # All recipes should have unique conflict mechanisms
        unique = set(mechanisms.values())
        assert len(unique) == len(mechanisms), (
            f"Duplicate mechanisms: {len(mechanisms) - len(unique)} collisions"
        )


class TestG11toG14Loading:
    """Verify all G11-G14 genres load from extension library."""

    def test_all_extension_genres_loaded(self, lib):
        ext_genres = lib.formula_ext.get("genre_fit_matrix_extension", [])
        assert len(ext_genres) == 4, f"Expected 4 extension genres, got {len(ext_genres)}"
        genre_ids = {g["genre_id"] for g in ext_genres}
        for gid in ("G11", "G12", "G13", "G14"):
            assert gid in genre_ids, f"Missing {gid}"

    def test_all_genres_have_keywords(self, lib):
        # Only extension genres (G11-G14) are required to have keywords;
        # base library genres (G01-G10) come from the original formula library
        for g in lib.formula_ext.get("genre_fit_matrix_extension", []):
            gid = g.get("genre_id", "")
            keywords = g.get("keywords", [])
            assert len(keywords) >= 2, f"{gid} ({g.get('name','?')}): too few keywords"

    def test_all_genres_have_default_recipe(self, lib):
        for g in lib.all_genres:
            gid = g.get("genre_id", "")
            if gid in ("G01", "G02", "G03", "G04", "G05", "G06", "G07", "G08", "G09", "G10",
                       "G11", "G12", "G13", "G14"):
                default = lib.genre_default_recipe(gid)
                assert default, f"{gid}: no default recipe"
                # Default recipe must exist and be enabled
                r = lib.recipe(default)
                assert r, f"{gid}: default recipe {default} not found"
                assert r.get("enabled", True), f"{gid}: default recipe {default} disabled"

    def test_G11_default_recipe_is_R6(self, lib):
        assert lib.genre_default_recipe("G11") == "R6"

    def test_G12_default_recipe_is_R10(self, lib):
        assert lib.genre_default_recipe("G12") == "R10"

    def test_G13_default_recipe_is_R9(self, lib):
        assert lib.genre_default_recipe("G13") == "R9"

    def test_G14_default_recipe_is_R13(self, lib):
        assert lib.genre_default_recipe("G14") == "R13"


class TestFreeTextGenreExtension:
    """Free-text genre keywords map to new G11-G14 genres."""

    def test_xuanyi_maps_to_G11(self, resolver):
        s = resolver.resolve(story_mode="general", genre="悬疑",
                             user_instruction="推理故事", explicit_fields=["story_mode", "genre"])
        assert s.genre_id == "G11"
        assert s.genre_name == "悬疑推理"

    def test_yineng_maps_to_G12(self, resolver):
        s = resolver.resolve(story_mode="general", genre="异能",
                             user_instruction="超能力故事", explicit_fields=["story_mode", "genre"])
        assert s.genre_id == "G12"

    def test_niandai_maps_to_G13(self, resolver):
        s = resolver.resolve(story_mode="general", genre="年代",
                             user_instruction="时代故事", explicit_fields=["story_mode", "genre"])
        assert s.genre_id == "G13"

    def test_zhichang_maps_to_G14(self, resolver):
        s = resolver.resolve(story_mode="general", genre="职场",
                             user_instruction="职场故事", explicit_fields=["story_mode", "genre"])
        assert s.genre_id == "G14"


class TestR6toR13StrategyResolution:
    """Verify each R6-R13 recipe resolves to a valid strategy.

    Each recipe may have different mode affinities; these tests use
    the recipe's first compatible mode from recipe_mode_affinity.
    """
    ALL_RECIPES = ("R6", "R7", "R8", "R9", "R10", "R11", "R12", "R13")
    # Mode override for recipes that don't support 'general'
    MODE_OVERRIDE = {
        "R10": "viral_drama",  # 异能觉醒: only viral_drama + mystery
    }

    @pytest.mark.parametrize("rid", ALL_RECIPES)
    def test_recipe_resolves_with_auto_genre(self, resolver, lib, rid):
        mode = self.MODE_OVERRIDE.get(rid, "auto")
        strategy = resolver.resolve(
            story_mode=mode,
            genre="auto",
            recipe_id=rid,
            user_instruction="测试",
            explicit_fields=["recipe_id"],
        )
        assert strategy.recipe_id == rid
        assert strategy.recipe_name != ""
        assert strategy.recipe_enabled
        assert len(strategy.conflict_mechanism) > 20
        assert len(strategy.causal_driver) > 20
        assert len(strategy.ending_strategy) > 20
        assert len(strategy.tone_guidance) > 10

    @pytest.mark.parametrize("rid", ALL_RECIPES)
    def test_recipe_has_writer_hints(self, resolver, lib, rid):
        mode = self.MODE_OVERRIDE.get(rid, "auto")
        strategy = resolver.resolve(
            story_mode=mode,
            genre="auto",
            recipe_id=rid,
            user_instruction="测试",
            explicit_fields=["recipe_id"],
        )
        hints = strategy.writer_context()
        assert len(hints) == 4, f"{rid}: expected 4 hints, got {len(hints)}"

    @pytest.mark.parametrize("rid", ALL_RECIPES)
    def test_recipe_mode_is_compatible(self, resolver, rid, lib):
        affinity = lib.recipe_mode_affinity()
        modes = affinity.get(rid, [])
        for mode in modes:
            strategy = resolver.resolve(
                story_mode=mode,
                genre="auto",
                recipe_id=rid,
                user_instruction="测试",
                explicit_fields=["story_mode", "recipe_id"],
            )
            assert strategy.recipe_id == rid
            assert strategy.story_mode == mode


class TestRecipeModeIncompatibility:
    """Verify incompatible mode+recipe raises clear error."""

    def test_R5_rejects_viral_drama(self, resolver):
        with pytest.raises(StrategyConflictError, match="R5"):
            resolver.resolve(
                story_mode="viral_drama",
                genre="G01",
                recipe_id="R5",
                user_instruction="测试",
                explicit_fields=["story_mode", "recipe_id"],
            )

    def test_R9_rejects_mystery(self, resolver):
        with pytest.raises(StrategyConflictError, match="R9"):
            resolver.resolve(
                story_mode="mystery",
                genre="auto",
                recipe_id="R9",
                user_instruction="测试",
                explicit_fields=["story_mode", "recipe_id"],
            )


class TestFullLoadCoverage:
    """Verify R1-R13 + G01-G14 are all present and coherent."""

    def test_total_recipe_count(self, lib):
        enabled = [r for r in lib.all_recipes if r.get("enabled", True)]
        assert len(enabled) == 13, f"Expected 13 enabled recipes, got {len(enabled)}"

    def test_total_genre_count(self, lib):
        all_genres = lib.all_genres
        assert len(all_genres) >= 14, f"Expected >=14 genres, got {len(all_genres)}"

    def test_freetext_genre_map_covers_G11_to_G14(self, lib):
        ft = lib.freetext_genre_map
        genre_ids_in_map = set(ft.values())
        for gid in ("G11", "G12", "G13", "G14"):
            assert gid in genre_ids_in_map, (
                f"{gid} not represented in free-text genre map"
            )

    def test_all_extension_recipes_have_version(self, lib):
        for r in lib.formula_ext.get("roster_recipes_extension", []):
            assert r.get("version"), f"{r['id']}: missing version"

    def test_24_test_ideas_exist(self, lib):
        ideas = lib.formula_ext.get("test_ideas", [])
        assert len(ideas) == 24, f"Expected 24 test ideas, got {len(ideas)}"
        for i, idea in enumerate(ideas):
            assert len(idea) >= 10, f"Idea {i} too short"