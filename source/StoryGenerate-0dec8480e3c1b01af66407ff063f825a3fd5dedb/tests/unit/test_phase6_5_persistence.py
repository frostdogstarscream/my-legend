"""Phase 6.5: Persistence Roundtrip Tests.

Hard requirement: 真实 save + reload，不是 State inspection。
使用 InMemoryRecordRepo 模拟 Repository 接口。
"""
from __future__ import annotations

import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent / "4-引擎代码"
sys.path.insert(0, str(ENGINE_ROOT))

import pytest  # noqa: E402

from drama_engine.config import RuleLibrary
from drama_engine.contracts import Runtime
from drama_engine.llm.latency_controlled import LatencyControlledMockProvider
from drama_engine.graph import run_pipeline, _build_content_json
from drama_engine.persistence.repository import StoryRecord, StoryJob
from drama_engine.persistence.memory_repo import InMemoryRecordRepo


# ── helpers ──────────────────────────────────────────────────────────
def _make_runtime():
    return Runtime(llm=LatencyControlledMockProvider(delay_sec=0.01))


def _make_lib():
    return RuleLibrary.load()


def _make_repos():
    """构造最小 repos：InMemoryRecordRepo + 简单 job spoof。"""
    class SpoofJobRepo:
        def create(self, job): return job
    return type("Repos", (), {
        "story_record": InMemoryRecordRepo(),
        "story_job": SpoofJobRepo(),
        "quality_result": type("Spoof", (), {"create": lambda self, q: None})(),
        "generation_trace": type("Spoof", (), {"batch_create": lambda self, t: None})(),
    })()


# ══════════════════════════════════════════════════════════════════════
# Prose persistence roundtrip
# ══════════════════════════════════════════════════════════════════════
class TestProsePersistenceRoundtrip:
    """general + prose_story → persist → reload → verify."""

    def test_prose_roundtrip_save_reload(self, tmp_path):
        repos = _make_repos()
        result = run_pipeline(
            idea="三个刚毕业的年轻人发现房东要收房，必须决定去留",
            workspace=str(tmp_path), lib=_make_lib(), runtime=_make_runtime(),
            target_episodes=1, story_mode="general", content_form="prose_story",
            repos=repos,
        )

        # ── 验证 content_json 被构建 ──
        content = _build_content_json(result)
        assert content["content_form"] == "prose_story"
        assert isinstance(content.get("episode_results"), list)
        assert len(content["episode_results"]) == 1

        # ── 验证 prose payload ──
        prose_payload = content.get("prose")
        assert prose_payload is not None, f"prose payload missing; keys={list(content.keys())}"
        assert prose_payload["title"], "prose title empty"
        assert len(prose_payload["paragraphs"]) > 0, "no paragraphs"
        plain = prose_payload.get("plain_text") or "\n\n".join(
            p["text"] for p in prose_payload["paragraphs"]
        )
        assert len(plain) > 100, f"plain_text too short: {len(plain)} chars"

        # ── 验证 StoryRecord 被保存 ──
        records = repos.story_record.list_all()
        assert len(records) == 1
        rec: StoryRecord = records[0]
        assert rec.content_form == "prose_story", f"content_form={rec.content_form}"
        assert rec.renderer_version, "renderer_version empty"
        assert rec.content_json, "content_json empty"

        # ── 验证 reload ──
        loaded = repos.story_record.get_by_story_id(rec.story_id)
        assert loaded is not None
        assert loaded.content_form == "prose_story"
        assert loaded.renderer_version
        loaded_content = loaded.content_json
        assert loaded_content["content_form"] == "prose_story"
        lp = loaded_content.get("prose")
        assert lp is not None
        assert lp["title"] == prose_payload["title"]
        assert len(lp["paragraphs"]) == len(prose_payload["paragraphs"])


    def test_prose_roundtrip_content_form_version(self, tmp_path):
        """content_form version preserved across save+reload."""
        repos = _make_repos()
        run_pipeline(
            idea="测试版本持久化", workspace=str(tmp_path),
            lib=_make_lib(), runtime=_make_runtime(),
            target_episodes=1, story_mode="general", content_form="prose_story",
            repos=repos,
        )
        records = repos.story_record.list_all()
        assert len(records) == 1
        loaded = repos.story_record.get_by_story_id(records[0].story_id)
        assert loaded.renderer_version == "1.0"
        assert loaded.content_form == "prose_story"


# ══════════════════════════════════════════════════════════════════════
# Audio persistence regression
# ══════════════════════════════════════════════════════════════════════
class TestAudioPersistenceRoundtrip:
    """Audio persistence 不因 EpisodeRenderResult 重构而回归。"""

    def test_audio_roundtrip(self, tmp_path):
        repos = _make_repos()
        result = run_pipeline(
            idea="穿越到仙侠世界的主角发现自己是反派",
            workspace=str(tmp_path), lib=_make_lib(), runtime=_make_runtime(),
            target_episodes=1, story_mode="viral_drama", content_form="audio_drama",
            repos=repos,
        )
        content = _build_content_json(result)
        assert content["content_form"] == "audio_drama"

        records = repos.story_record.list_all()
        assert len(records) == 1
        loaded = repos.story_record.get_by_story_id(records[0].story_id)
        assert loaded.content_form == "audio_drama"
        assert loaded.content_json  # non-empty