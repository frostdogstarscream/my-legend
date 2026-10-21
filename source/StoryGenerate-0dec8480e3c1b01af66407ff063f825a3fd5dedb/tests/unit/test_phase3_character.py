"""Phase 3 单元测试 —— Character Canon + Runtime 角色半固定机制。"""
from __future__ import annotations

import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent / "4-引擎代码"
sys.path.insert(0, str(ENGINE_ROOT))

import pytest
from drama_engine.characters import (
    CharacterCanon, CharacterAppearance,
    CharacterRuntimeInput, CharacterInput, ResolvedCharacter,
    CharacterResolver, CharacterCountExceededError,
    CharacterRoleIdConflictError, CharacterInvalidError,
    overlay_canon, overlay_runtime, validate_character_canon_preserved,
)


class TestCharacterCanon:
    def test_locked_fields_basic(self):
        c = CharacterCanon(name="Test", gender="male", identity="robot")
        locked = c.locked_fields()
        assert "name" in locked
        assert "gender" in locked
        assert "identity" in locked
        assert "species" not in locked
        assert "age" not in locked

    def test_locked_fields_none(self):
        c = CharacterCanon(name="Test")
        locked = c.locked_fields()
        assert locked == ["name"]

    def test_locked_fields_appearance(self):
        c = CharacterCanon(name="Test", appearance=CharacterAppearance(description="red"))
        locked = c.locked_fields()
        assert "appearance.description" in locked

    def test_summary(self):
        c = CharacterCanon(name="餮仔", gender="male", identity="智能玩具")
        s = c.summary()
        assert "餮仔" in s
        assert "male" in s or "男" in s

    def test_defaults(self):
        c = CharacterCanon(name="T")
        assert c.abilities == []
        assert c.immutable_facts == []
        assert c.appearance is None


class TestCharacterRuntimeInput:
    def test_override_fields_basic(self):
        rt = CharacterRuntimeInput(goal="save world", fear="losing friend")
        overrides = rt.override_fields()
        assert "goal" in overrides
        assert "fear" in overrides
        assert "motivation" not in overrides

    def test_override_fields_none(self):
        rt = CharacterRuntimeInput()
        assert rt.override_fields() == []

    def test_summary(self):
        rt = CharacterRuntimeInput(goal="Find X", fear="betrayal")
        s = rt.summary()
        assert "Find X" in s


class TestCharacterInput:
    def test_basic(self):
        inp = CharacterInput(
            role_id="role_01",
            canon=CharacterCanon(name="Hero"),
            runtime=CharacterRuntimeInput(goal="Win"),
        )
        assert inp.role_id == "role_01"
        assert inp.canon.name == "Hero"
        assert inp.runtime.goal == "Win"


class TestResolvedCharacter:
    def test_name_property(self):
        rc = ResolvedCharacter(
            role_id="r01", canon=CharacterCanon(name="Hero"),
            runtime=CharacterRuntimeInput(),
            source="user",
            canon_locked_fields=["name"],
        )
        assert rc.name == "Hero"


class TestCharacterResolver:
    def test_empty_input(self):
        r = CharacterResolver(max_characters=5)
        snaps, missing = r.resolve([])
        assert snaps == []
        assert missing == 5

    def test_none_input(self):
        r = CharacterResolver(max_characters=5)
        snaps, missing = r.resolve(None)
        assert snaps == []
        assert missing == 5

    def test_one_character(self):
        r = CharacterResolver(max_characters=5)
        inp = CharacterInput(canon=CharacterCanon(name="Hero"))
        snaps, missing = r.resolve([inp])
        assert len(snaps) == 1
        assert missing == 4
        assert snaps[0].source == "user"
        assert snaps[0].canon_locked_fields == ["name"]

    def test_full_characters(self):
        r = CharacterResolver(max_characters=5)
        inputs = [CharacterInput(canon=CharacterCanon(name=f"Char{i}")) for i in range(5)]
        snaps, missing = r.resolve(inputs)
        assert len(snaps) == 5
        assert missing == 0

    def test_exceeded_raises(self):
        r = CharacterResolver(max_characters=5)
        inputs = [CharacterInput(canon=CharacterCanon(name=f"C{i}")) for i in range(6)]
        with pytest.raises(CharacterCountExceededError):
            r.resolve(inputs)

    def test_duplicate_role_id_raises(self):
        r = CharacterResolver(max_characters=5)
        inputs = [
            CharacterInput(role_id="same", canon=CharacterCanon(name="A")),
            CharacterInput(role_id="same", canon=CharacterCanon(name="B")),
        ]
        with pytest.raises(CharacterRoleIdConflictError):
            r.resolve(inputs)

    def test_no_name_raises(self):
        r = CharacterResolver(max_characters=5)
        with pytest.raises(CharacterInvalidError):
            r.resolve([CharacterInput(canon=CharacterCanon(name=""))])

    def test_auto_role_id(self):
        r = CharacterResolver(max_characters=5)
        snaps, _ = r.resolve([
            CharacterInput(canon=CharacterCanon(name="A")),
            CharacterInput(canon=CharacterCanon(name="B")),
        ])
        ids = [s.role_id for s in snaps]
        assert len(set(ids)) == 2
        assert all(id_.startswith("role_") for id_ in ids)

    def test_pin_prompt_context(self):
        r = CharacterResolver(max_characters=5)
        snaps, _ = r.resolve([
            CharacterInput(
                canon=CharacterCanon(name="Hero", gender="male"),
                runtime=CharacterRuntimeInput(goal="Save world"),
            )
        ])
        ctx = r.pin_prompt_context(snaps)
        assert "Hero" in ctx
        assert "Save world" in ctx


class TestCanonOverlay:
    def test_overlay_canon_corrects_gender(self):
        locked = CharacterCanon(name="Hero", gender="male")
        llm_out = {"name": "Hero", "gender": "female", "age": 30}
        fixed, conflicts = overlay_canon(llm_out, locked, locked.locked_fields())
        assert fixed["gender"] == "male"
        assert len(conflicts) == 1
        assert conflicts[0]["field"] == "gender"

    def test_overlay_canon_no_conflict(self):
        locked = CharacterCanon(name="Hero", gender="male")
        llm_out = {"name": "Hero", "gender": "male"}
        fixed, conflicts = overlay_canon(llm_out, locked, locked.locked_fields())
        assert fixed["gender"] == "male"
        assert conflicts == []

    def test_overlay_with_appearance(self):
        locked = CharacterCanon(name="H", appearance=CharacterAppearance(description="blue"))
        llm_out = {"name": "H", "appearance": {"description": "red"}}
        fixed, conflicts = overlay_canon(llm_out, locked, locked.locked_fields())
        assert fixed["appearance"]["description"] == "blue"
        assert len(conflicts) == 1

    def test_validate_ok(self):
        locked = CharacterCanon(name="H", gender="m")
        final = {"name": "H", "gender": "m"}
        issues = validate_character_canon_preserved(final, locked, locked.locked_fields())
        assert issues == []

    def test_validate_broken(self):
        locked = CharacterCanon(name="H", gender="m")
        final = {"name": "H", "gender": "f"}
        issues = validate_character_canon_preserved(final, locked, locked.locked_fields())
        assert len(issues) == 1
        assert issues[0]["severity"] == "error"


class TestRuntimeOverlay:
    def test_overlay_goal(self):
        user_rt = CharacterRuntimeInput(goal="Find gem")
        llm_rt = {"goal": "Get rich"}
        fixed, conflicts = overlay_runtime(llm_rt, user_rt, user_rt.override_fields())
        assert fixed["goal"] == "Find gem"
        assert len(conflicts) == 1

    def test_no_override_when_not_provided(self):
        user_rt = CharacterRuntimeInput()  # nothing
        llm_rt = {"goal": "Get rich"}
        fixed, conflicts = overlay_runtime(llm_rt, user_rt, user_rt.override_fields())
        assert fixed["goal"] == "Get rich"  # unchanged
        assert conflicts == []

    def test_goal_and_fear_both(self):
        user_rt = CharacterRuntimeInput(goal="A", fear="B")
        llm_rt = {"goal": "X", "fear": "Y"}
        fixed, _ = overlay_runtime(llm_rt, user_rt, user_rt.override_fields())
        assert fixed["goal"] == "A"
        assert fixed["fear"] == "B"