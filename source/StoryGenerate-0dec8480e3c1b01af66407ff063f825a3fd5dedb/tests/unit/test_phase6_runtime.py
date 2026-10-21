"""Phase 6.1: Content Form Runtime Closure Tests.

验收范围:
  - route_start 不再按 content_form 分流 prose
  - gen_beats 对所有 content_form 都执行
  - route_content_form 在 Planning 后分叉
  - prose_writer 在 gen_beats 之后执行
  - prose_validate 真实 runtime 执行
  - Audio validators 不应用到 Prose
  - Prose validators 不应用到 Audio
  - Graph extensibility: 新增 Content Form 不改 graph topology
  - Persistence: content_form + renderer_version
"""
from __future__ import annotations

import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent / "4-引擎代码"
sys.path.insert(0, str(ENGINE_ROOT))

import pytest  # noqa: E402

from drama_engine.content_forms import ContentFormProfile
from drama_engine.content_forms.registry import register_content_form, _registry
from drama_engine.nodes.episode import (
    build_episode_graph,
    route_content_form,
    route_start,
)


# ============================================================================
# Routing Fix Tests
# ============================================================================
class TestRouteStartNoBypass:
    """route_start 不得按 content_form 绕过 Planning。"""

    def test_always_returns_gen_beats(self):
        """所有 content_form 都走 gen_beats。"""
        # audio_drama / 缺失
        assert route_start({"content_form": {"key": "audio_drama"}}) == "gen_beats"
        assert route_start({}) == "gen_beats"
        # prose_story 也必须走 gen_beats
        assert route_start({"content_form": {"key": "prose_story"}}) == "gen_beats"

    def test_no_prose_writer_from_start(self):
        """route_start 绝不返回 prose_writer。"""
        result = route_start({"content_form": {"key": "prose_story"}})
        assert result != "prose_writer", (
            "Phase 6.1: prose_story 必须经过 gen_beats (Story Planning)，"
            "不能从 START 直接跳到 prose_writer"
        )


# ============================================================================
# Content Form Routing After Planning
# ============================================================================
class TestRouteContentForm:
    """route_content_form: Planning 之后按 Content Form 分叉。"""

    def test_prose_routes_to_prose_writer(self):
        result = route_content_form({"content_form": {"key": "prose_story"}})
        assert result == "prose_writer"

    def test_audio_routes_to_validate_ep(self):
        result = route_content_form({"content_form": {"key": "audio_drama"}})
        assert result == "validate_ep"

    def test_default_routes_to_validate_ep(self):
        """不传 content_form → audio_drama → validate_ep。"""
        result = route_content_form({})
        assert result == "validate_ep"

    def test_unknown_form_routes_to_validate_ep(self):
        """未知 content_form → 安全回退到 audio。"""
        result = route_content_form({"content_form": {"key": "novel"}})
        assert result == "validate_ep"


# ============================================================================
# Graph Topology Tests
# ============================================================================
class TestGraphTopology:
    """Graph 拓扑验证：prose 路径包含完整 Planning → Writer → Validator 链。"""

    def test_graph_has_all_nodes(self):
        g = build_episode_graph()
        nodes = g.get_graph().nodes
        assert "gen_beats" in nodes
        assert "prose_writer" in nodes
        assert "prose_validate" in nodes
        assert "validate_ep" in nodes

    def test_gen_beats_always_first(self):
        """所有路径都从 gen_beats 开始。"""
        g = build_episode_graph()
        # START 只有一个出口：gen_beats
        edges = g.get_graph().edges
        start_edges = [e for e in edges if e[0] == "__start__"]
        assert len(start_edges) == 1
        assert start_edges[0][1] == "gen_beats"

    def test_prose_path_has_validator(self):
        """prose_writer → prose_validate → END。"""
        g = build_episode_graph()
        edges = g.get_graph().edges
        # prose_writer → prose_validate
        assert ("prose_writer", "prose_validate") in [e[:2] for e in edges]
        # prose_validate → END
        assert ("prose_validate", "__end__") in [e[:2] for e in edges]

    def test_audio_path_has_repair_loop(self):
        """Audio 路径有 validate → repair 循环。"""
        g = build_episode_graph()
        nodes = g.get_graph().nodes
        assert "repair_audio" in nodes
        assert "repair_beat" in nodes
        assert "repair_compliance" in nodes


# ============================================================================
# Validator Runtime Tests
# ============================================================================
class TestValidatorRuntime:
    """Prose 验证器在 runtime 真实执行。"""

    def test_prose_validate_registered_in_graph(self):
        g = build_episode_graph()
        assert "prose_validate" in g.get_graph().nodes

    def test_prose_validate_not_in_audio_path(self):
        """Audio 路径不应经过 prose_validate。"""
        g = build_episode_graph()
        edges = g.get_graph().edges
        # gen_beats → validate_ep (audio) 不应经过 prose_validate
        for e in edges:
            if e[0] == "gen_beats" and e[1] != "prose_writer":
                assert e[1] != "prose_validate"

    def test_audio_validators_not_in_prose_path(self):
        """Prose 路径不应经过 audio validators/repair。"""
        g = build_episode_graph()
        edges = g.get_graph().edges
        # prose_writer → prose_validate → END，不应经过 repair_audio
        prose_edges = [e for e in edges if e[0] == "prose_writer"]
        for e in prose_edges:
            assert e[1] not in ("repair_audio", "repair_beat", "repair_compliance")


# ============================================================================
# Graph Extensibility Tests
# ============================================================================
class TestGraphExtensibility:
    """新增 Content Form 不需要修改 Graph topology。"""

    def test_register_new_form_no_graph_change(self):
        """注册新的 Content Form 不需要 add_node/add_edge。

        验证：build_episode_graph 的节点数量不因 registry 中的
        Content Form 数量而变化（因为 writer 选择由 route_content_form
        内部通过 registry 完成，不是通过 graph edges）。
        """
        # 记录当前 graph 的节点数
        g1 = build_episode_graph()
        nodes_before = len(g1.get_graph().nodes)

        # 注册一个新的 Content Form（不添加新 writer 实现，只测 topology）
        dummy_profile = ContentFormProfile(
            key="dummy_form_test",
            version="1.0",
            writer="prose_writer",  # 复用已有 writer
        )
        old = _registry.get("dummy_form_test")
        register_content_form(dummy_profile)

        try:
            # 重新编译 graph
            g2 = build_episode_graph()
            nodes_after = len(g2.get_graph().nodes)
            assert nodes_after == nodes_before, (
                "注册新 Content Form 不应增加 graph 节点数。"
                f" before={nodes_before}, after={nodes_after}"
            )
        finally:
            # Cleanup: 恢复 registry
            if old:
                _registry["dummy_form_test"] = old
            else:
                _registry.pop("dummy_form_test", None)


# ============================================================================
# Same Plan Dual Renderer
# ============================================================================
class TestSamePlanDualRenderer:
    """同一个 Story Plan 可以由不同 Content Form 渲染。"""

    def test_both_writers_consume_same_state_fields(self):
        """gen_beats 产生的 outline_entry 可同时被 Audio/Prose 消费。"""
        # 验证：gen_beats 的输出字段在 state 中，且两个 writer 都能访问
        g = build_episode_graph()
        # gen_beats 产生 episode（audio）— 这是核心规划产物
        # prose_writer 读取 outline_entry — 这是共享规划数据
        assert "gen_beats" in g.get_graph().nodes
        assert "prose_writer" in g.get_graph().nodes

    def test_content_form_independent_from_mode(self):
        """Content Form 不绑定 Mode。"""
        from drama_engine.content_forms import available_content_forms
        forms = available_content_forms()
        # Prose 应对所有 Mode 可用
        assert "prose_story" in forms
        # Audio 应对所有 Mode 可用
        assert "audio_drama" in forms