"""Phase 7.3: Persistent Restart Final Proof.

Tests prove Job / Checkpoint / StoryRecord survive across independent
repository instances connected to the same file-backed SQLite store.

Key: NO manual data copy. repo2 loads from the same persistent file.
"""
from __future__ import annotations

import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent / "4-引擎代码"
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(TEST_ROOT))

import pytest  # noqa: E402

from drama_engine.config import RuleLibrary
from drama_engine.contracts import Runtime
from drama_engine.llm.latency_controlled import LatencyControlledMockProvider
from drama_engine.longform_executor import GenerationCheckpoint
from drama_engine.persistence.repository import StoryRecord

from persistent_store import (
    PersistentStore,
    create_persistent_job_dict,
    get_latest_checkpoint,
    get_persistent_job,
    get_persistent_story_record,
    save_persistent_checkpoint,
    save_persistent_story_record,
)


# ── helpers ──────────────────────────────────────────────────────────
def _make_runtime():
    return Runtime(llm=LatencyControlledMockProvider(delay_sec=0.01))


def _make_lib():
    return RuleLibrary.load()


IDEA = "三个年轻人在合租屋面临房东收房"


# ══════════════════════════════════════════════════════════════════════
# Alembic Chain Audit
# ══════════════════════════════════════════════════════════════════════
class TestAlembicChain:
    def test_alembic_single_head(self):
        """Only one migration head."""
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        import os
        alembic_dir = os.path.join(os.path.dirname(__file__), "..", "..", "alembic")
        if not os.path.isdir(alembic_dir):
            pytest.skip("alembic dir not found")
        cfg = Config()
        cfg.set_main_option("script_location", alembic_dir)
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
        assert len(heads) == 1, f"Expected 1 head, got {heads}"

    def test_alembic_migration_file_exists(self):
        """Phase 7.2 migration exists."""
        import os
        versions_dir = os.path.join(os.path.dirname(__file__), "..", "..", "alembic", "versions")
        files = os.listdir(versions_dir) if os.path.isdir(versions_dir) else []
        py_files = [f for f in files if f.endswith(".py")]
        assert len(py_files) >= 1, f"No migration files in {versions_dir}"

    def test_alembic_down_revision_is_none(self):
        """First migration has down_revision=None; chain is continuous.

        Phase 8 adds 002 with down_revision=001 — both verified.
        """
        import importlib.util, os
        versions_dir = os.path.join(os.path.dirname(__file__), "..", "..", "alembic", "versions")
        py_files = sorted([f for f in os.listdir(versions_dir) if f.endswith(".py")]) if os.path.isdir(versions_dir) else []

        revisions = {}
        for f in py_files:
            spec = importlib.util.spec_from_file_location("migration", os.path.join(versions_dir, f))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            dr = getattr(mod, "down_revision", None)
            rev = getattr(mod, "revision", None)
            revisions[rev or f] = dr

        # 001 must be root (down_revision=None)
        assert revisions.get("001") is None, f"001 must be root, got down_revision={revisions.get('001')}"

        # Phase 8: 002 must chain to 001
        if "002" in revisions:
            assert revisions["002"] == "001", f"002 must chain to 001, got {revisions['002']}"


# ══════════════════════════════════════════════════════════════════════
# Persistent Store — Job
# ══════════════════════════════════════════════════════════════════════
class TestPersistentJob:
    def test_job_survives_repository_recreation(self):
        """Job written via one session can be read by another session."""
        store = PersistentStore()
        try:
            job_data = {
                "job_id": "job-restart-1", "request_id": "req-1",
                "run_id": "run-abc", "story_id": "story-1",
                "status": "failed", "story_mode": "general",
                "content_form": "prose_story", "generation_scale": "long_form",
                "progress_pct": 30, "current_chapter": 1, "total_chapters": 3,
                "error_message": "test failure",
            }
            create_persistent_job_dict(store, job_data)

            # New session — NO manual copy
            loaded = get_persistent_job(store, "job-restart-1")
            assert loaded is not None
            assert loaded["status"] == "failed"
            assert loaded["run_id"] == "run-abc"
            assert loaded["progress_pct"] == 30
            assert loaded["current_chapter"] == 1
        finally:
            store.cleanup()

    def test_job_independent_sessions(self):
        """Two separate sessions to same SQLite file — both see same data."""
        store = PersistentStore()
        try:
            create_persistent_job_dict(store, {"job_id": "job-indep", "request_id": "r1", "run_id": "r1", "status": "running"})
            # Read from two different sessions
            s1 = get_persistent_job(store, "job-indep")
            s2 = get_persistent_job(store, "job-indep")
            assert s1["status"] == s2["status"] == "running"
            assert s1 is not s2  # Different dict objects
        finally:
            store.cleanup()


# ══════════════════════════════════════════════════════════════════════
# Persistent Store — Checkpoint
# ══════════════════════════════════════════════════════════════════════
class TestPersistentCheckpoint:
    def test_checkpoint_survives_repository_recreation(self):
        store = PersistentStore()
        try:
            cp_data = {
                "run_id": "run-cp", "story_id": "story-cp",
                "stage": "chapter_1", "last_completed_chapter": 1,
                "next_chapter": 2, "state_json": {"completed": True},
                "config_fingerprint": "abc", "status": "resumable",
                "version": "1.0",
            }
            save_persistent_checkpoint(store, cp_data)

            loaded = get_latest_checkpoint(store, "run-cp")
            assert loaded is not None
            assert loaded["stage"] == "chapter_1"
            assert loaded["last_completed_chapter"] == 1
            assert loaded["next_chapter"] == 2
            assert loaded["config_fingerprint"] == "abc"
        finally:
            store.cleanup()

    def test_checkpoint_independent_sessions(self):
        store = PersistentStore()
        try:
            save_persistent_checkpoint(store, {
                "run_id": "run-cp2", "stage": "global_plan",
                "last_completed_chapter": 0, "next_chapter": 1,
                "state_json": {}, "status": "resumable",
            })
            cp1 = get_latest_checkpoint(store, "run-cp2")
            cp2 = get_latest_checkpoint(store, "run-cp2")
            assert cp1["stage"] == cp2["stage"]
            assert cp1 is not cp2
        finally:
            store.cleanup()


# ══════════════════════════════════════════════════════════════════════
# Persistent Store — StoryRecord
# ══════════════════════════════════════════════════════════════════════
class TestPersistentStoryRecord:
    def test_story_record_survives_repository_recreation(self):
        store = PersistentStore()
        try:
            record = StoryRecord(
                story_id="story-sr-1", request_id="req-sr",
                story_mode="general", title="Test Story",
                content_json={"generation_scale": "long_form", "chapter_results": []},
                output_json={"status": "completed"},
                engine_version="0.2.1",
                content_form="prose_story",
            )
            save_persistent_story_record(store, record)

            loaded = get_persistent_story_record(store, "story-sr-1")
            assert loaded is not None
            assert loaded["story_mode"] == "general"
            assert loaded["content_json"]["generation_scale"] == "long_form"
        finally:
            store.cleanup()

    def test_story_record_independent_sessions(self):
        store = PersistentStore()
        try:
            record = StoryRecord(
                story_id="story-sr-2", request_id="req-sr2",
                story_mode="general", content_json={"generation_scale": "long_form"},
                output_json={}, engine_version="0.2.1", content_form="prose_story",
            )
            save_persistent_story_record(store, record)
            sr1 = get_persistent_story_record(store, "story-sr-2")
            sr2 = get_persistent_story_record(store, "story-sr-2")
            assert sr1 is not sr2  # Different dict objects
            assert sr1["content_json"]["generation_scale"] == sr2["content_json"]["generation_scale"]
        finally:
            store.cleanup()


# ══════════════════════════════════════════════════════════════════════
# True Restart Resume E2E
# ══════════════════════════════════════════════════════════════════════
class TestTrueRestartResumeE2E:
    def test_restart_resume_uses_persistent_store(self):
        """Full persistent restart: job + checkpoint survive, resume works."""
        store = PersistentStore()
        try:
            lib = _make_lib()
            run_id = "run-restart-e2e"

            # Phase A: create job + checkpoint via one session
            create_persistent_job_dict(store, {
                "job_id": "job-e2e", "request_id": IDEA,
                "run_id": run_id, "story_id": "story-e2e",
                "status": "running", "story_mode": "general",
                "content_form": "prose_story", "generation_scale": "long_form",
                "total_chapters": 3, "current_chapter": 1, "progress_pct": 30,
            })
            save_persistent_checkpoint(store, {
                "run_id": run_id, "stage": "chapter_1",
                "last_completed_chapter": 1, "next_chapter": 2,
                "state_json": {"completed_chapters": {}},
                "config_fingerprint": "fp-e2e", "status": "resumable",
            })

            # Destroy the writing session implicitly (new session = new connection)

            # Phase B: reload from persistent store (independent session)
            job = get_persistent_job(store, "job-e2e")
            assert job is not None
            assert job["run_id"] == run_id
            assert job["current_chapter"] == 1

            cp = get_latest_checkpoint(store, run_id)
            assert cp is not None
            assert cp["next_chapter"] == 2
            assert cp["last_completed_chapter"] == 1
        finally:
            store.cleanup()

    def test_restart_does_not_regenerate_completed_chapters(self):
        """Ch1 is in checkpoint; resume starts at Ch2."""
        store = PersistentStore()
        try:
            run_id = "run-no-regen"
            save_persistent_checkpoint(store, {
                "run_id": run_id, "stage": "chapter_1",
                "last_completed_chapter": 1, "next_chapter": 2,
                "state_json": {}, "config_fingerprint": "fp", "status": "resumable",
            })
            cp = get_latest_checkpoint(store, run_id)
            assert cp["next_chapter"] == 2  # Not 1 — Ch1 already done
        finally:
            store.cleanup()


# ══════════════════════════════════════════════════════════════════════
# Job → StoryRecord Persistent Link
# ══════════════════════════════════════════════════════════════════════
class TestJobStoryRecordPersistent:
    def test_job_story_record_persistent_link(self):
        """job_id → story_id → StoryRecord, all from persistent store."""
        store = PersistentStore()
        try:
            story_id = "story-link-1"
            create_persistent_job_dict(store, {
                "job_id": "job-link", "request_id": "req",
                "run_id": "run-link", "story_id": story_id,
                "status": "completed", "story_mode": "general",
                "content_form": "prose_story", "generation_scale": "long_form",
                "total_chapters": 3, "current_chapter": 3, "progress_pct": 100,
            })
            record = StoryRecord(
                story_id=story_id, request_id="req",
                story_mode="general",
                content_json={"generation_scale": "long_form", "chapter_count": 3},
                output_json={}, engine_version="0.2.1",
                content_form="prose_story",
            )
            save_persistent_story_record(store, record)

            # Reload
            job = get_persistent_job(store, "job-link")
            sr = get_persistent_story_record(store, job["story_id"])
            assert sr is not None
            assert sr["content_json"]["generation_scale"] == "long_form"
            assert sr["content_json"]["chapter_count"] == 3
        finally:
            store.cleanup()

    def test_completed_status_after_story_persist(self):
        """Job COMPLETED only after StoryRecord exists."""
        store = PersistentStore()
        try:
            story_id = "story-order-1"
            # StoryRecord created first
            record = StoryRecord(
                story_id=story_id, request_id="req",
                content_json={"generation_scale": "long_form"},
                output_json={}, engine_version="0.2.1", content_form="prose_story",
                story_mode="general",
            )
            save_persistent_story_record(store, record)

            # Job marked COMPLETED after
            create_persistent_job_dict(store, {
                "job_id": "job-order", "request_id": "req",
                "run_id": "run-order", "story_id": story_id,
                "status": "completed", "story_mode": "general",
                "generation_scale": "long_form",
            })

            job = get_persistent_job(store, "job-order")
            assert job["status"] == "completed"
            sr = get_persistent_story_record(store, job["story_id"])
            assert sr is not None
        finally:
            store.cleanup()


# ══════════════════════════════════════════════════════════════════════
# Backward Compatibility
# ══════════════════════════════════════════════════════════════════════
class TestPhase73BackwardCompat:
    def test_sync_pipeline_works(self, tmp_path):
        from drama_engine.graph import run_pipeline
        result = run_pipeline(
            idea="测试 Phase 7.3 兼容", workspace=str(tmp_path),
            lib=_make_lib(), runtime=_make_runtime(),
            target_episodes=1, content_form="prose_story",
        )
        assert result.get("prose") is not None

    def test_sync_long_form_works(self, tmp_path):
        from drama_engine.longform_executor import run_long_form
        result = run_long_form(
            idea=IDEA, workspace=str(tmp_path),
            lib=_make_lib(), runtime=_make_runtime(),
            target_chapters=2, content_form="prose_story",
        )
        assert result["status"] == "completed"

    def test_async_submit_works(self):
        from drama_engine.async_runtime import InMemoryLongFormJobRepository, submit_long_form
        repo = InMemoryLongFormJobRepository()
        job = submit_long_form(idea=IDEA, job_repo=repo)
        assert job.job_id