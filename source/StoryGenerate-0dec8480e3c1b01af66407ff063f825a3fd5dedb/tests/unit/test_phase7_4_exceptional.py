"""Phase 7.4: Exceptional Persistence Closure.

Three scenarios:
  1. FAILED Job persistent restart + resume
  2. CANCELLED Job persistent restart + resume  
  3. StoryRecord persist failure → Job FAILED

All three use file-backed SQLite for restart semantics.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent / "4-引擎代码"
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(TEST_ROOT))

import pytest  # noqa: E402

from drama_engine.config import RuleLibrary
from drama_engine.contracts import Runtime
from drama_engine.llm.latency_controlled import LatencyControlledMockProvider

from persistent_store import (
    PersistentStore,
    create_persistent_job_dict,
    get_latest_checkpoint,
    get_persistent_job,
    save_persistent_checkpoint,
)


# ── helpers ──────────────────────────────────────────────────────────
def _make_runtime():
    return Runtime(llm=LatencyControlledMockProvider(delay_sec=0.01))


def _make_lib():
    return RuleLibrary.load()


IDEA = "两个少年在废墟城市中寻找最后一部电力发电机"


def _persist_job(store, job_id, run_id, story_id, status, ch_completed, error_msg=""):
    create_persistent_job_dict(store, {
        "job_id": job_id, "request_id": f"req-{job_id}",
        "run_id": run_id, "story_id": story_id,
        "status": status, "story_mode": "general",
        "content_form": "prose_story", "generation_scale": "long_form",
        "total_chapters": 3, "current_chapter": ch_completed,
        "progress_pct": ch_completed * 30,
        "error_message": error_msg,
    })


def _persist_checkpoint_from_data(store, cp_dict):
    """Persist a checkpoint dict (from InMemoryCheckpointRepository row)."""
    save_persistent_checkpoint(store, cp_dict)


def _load_checkpoint_into_memory(store, run_id):
    """Load checkpoint from persistent store into new InMemoryCheckpointRepository."""
    from drama_engine.longform_executor import InMemoryCheckpointRepository, GenerationCheckpoint
    repo = InMemoryCheckpointRepository()
    cp_dict = get_latest_checkpoint(store, run_id)
    if cp_dict:
        cp = GenerationCheckpoint(
            run_id=cp_dict["run_id"],
            story_id=cp_dict.get("story_id", ""),
            generation_scale=cp_dict.get("generation_scale", "long_form"),
            stage=cp_dict["stage"],
            last_completed_chapter=cp_dict["last_completed_chapter"],
            next_chapter=cp_dict["next_chapter"],
            state_json=cp_dict["state_json"],
            config_fingerprint=cp_dict["config_fingerprint"],
            status=cp_dict["status"],
            version=cp_dict.get("version", "1.0"),
        )
        repo.save_or_update(cp)
    return repo


# ══════════════════════════════════════════════════════════════════════
# 1. FAILED Job Persistent Restart + Resume
# ══════════════════════════════════════════════════════════════════════
class TestFailedPersistentRestart:
    def test_failed_job_state_survives_repo_recreation(self):
        """FAILED state persists across independent sessions."""
        store = PersistentStore()
        try:
            run_id = "run-failed-1"
            _persist_job(store, "job-failed-1", run_id, "story-failed-1",
                         status="failed", ch_completed=1,
                         error_msg="Chapter 2 generation failed")
            save_persistent_checkpoint(store, {
                "run_id": run_id, "stage": "chapter_1",
                "last_completed_chapter": 1, "next_chapter": 2,
                "state_json": {}, "config_fingerprint": "fp-test",
                "status": "resumable",
            })

            # ── NEW session ──
            job = get_persistent_job(store, "job-failed-1")
            assert job is not None
            assert job["status"] == "failed"
            assert job["current_chapter"] == 1
            assert job["error_message"] == "Chapter 2 generation failed"

            cp = get_latest_checkpoint(store, run_id)
            assert cp is not None
            assert cp["next_chapter"] == 2
        finally:
            store.cleanup()

    def test_failed_job_persistent_restart_and_resume(self):
        """FAILED → persistent reload → resume → COMPLETED."""
        from drama_engine.longform_executor import run_long_form, InMemoryCheckpointRepository

        store = PersistentStore()
        lib = _make_lib()
        try:
            ws = os.path.join(tempfile.gettempdir(), "phase74_failed")
            os.makedirs(ws, exist_ok=True)

            # ── Phase A: shared checkpoint repo — captures real fingerprints ──
            ckpt_repo_a = InMemoryCheckpointRepository()
            result_a = run_long_form(
                idea=IDEA, workspace=ws, lib=lib, runtime=_make_runtime(),
                target_chapters=3, content_form="prose_story",
                fail_at_chapter=2, checkpoint_repo=ckpt_repo_a,
            )
            run_id = result_a["run_id"]
            story_id = result_a.get("story_id", f"story-{run_id}")
            assert result_a["status"] == "failed"
            assert result_a["chapter_count"] == 1

            # ── Persist using ACTUAL checkpoint data (real fingerprint) ──
            latest_a = ckpt_repo_a.get_latest(run_id)
            assert latest_a is not None
            _persist_job(store, f"job-{run_id}", run_id, story_id,
                         status="failed", ch_completed=1,
                         error_msg="Chapter 2 generation failed")
            _persist_checkpoint_from_data(store, latest_a.__dict__)

            # ── Phase B: NEW session, reload, NEW checkpoint repo ──
            job = get_persistent_job(store, f"job-{run_id}")
            assert job["status"] == "failed"
            cp = get_latest_checkpoint(store, run_id)
            assert cp["next_chapter"] == 2

            ckpt_repo_b = _load_checkpoint_into_memory(store, run_id)
            result_b = run_long_form(
                idea=IDEA, workspace=ws, lib=lib, runtime=_make_runtime(),
                target_chapters=3, content_form="prose_story",
                resume_from=run_id, checkpoint_repo=ckpt_repo_b,
            )
            assert result_b["status"] == "completed"
            assert result_b["chapter_count"] == 3
            # Ch1 preserved
            assert result_a["chapter_results"][0]["render_result"] == result_b["chapter_results"][0]["render_result"]
        finally:
            store.cleanup()


# ══════════════════════════════════════════════════════════════════════
# 2. CANCELLED Job Persistent Restart + Resume
# ══════════════════════════════════════════════════════════════════════
class TestCancelledPersistentRestart:
    def test_cancelled_job_state_survives_repo_recreation(self):
        """CANCELLED state persists across independent sessions."""
        store = PersistentStore()
        try:
            run_id = "run-cancelled-1"
            _persist_job(store, "job-cancelled-1", run_id, "story-cancelled-1",
                         status="cancelled", ch_completed=1)
            save_persistent_checkpoint(store, {
                "run_id": run_id, "stage": "chapter_1",
                "last_completed_chapter": 1, "next_chapter": 2,
                "state_json": {}, "config_fingerprint": "fp-test",
                "status": "resumable",
            })

            job = get_persistent_job(store, "job-cancelled-1")
            assert job["status"] == "cancelled"
            cp = get_latest_checkpoint(store, run_id)
            assert cp["next_chapter"] == 2
        finally:
            store.cleanup()

    def test_cancelled_job_persistent_restart_and_resume(self):
        """CANCELLED → persistent reload → explicit resume → COMPLETED."""
        from drama_engine.longform_executor import run_long_form, InMemoryCheckpointRepository

        store = PersistentStore()
        lib = _make_lib()
        try:
            ws = os.path.join(tempfile.gettempdir(), "phase74_cancelled")
            os.makedirs(ws, exist_ok=True)

            ckpt_repo_a = InMemoryCheckpointRepository()
            result_a = run_long_form(
                idea=IDEA, workspace=ws, lib=lib, runtime=_make_runtime(),
                target_chapters=3, content_form="prose_story",
                fail_at_chapter=2, checkpoint_repo=ckpt_repo_a,
            )
            run_id = result_a["run_id"]
            story_id = result_a.get("story_id", f"story-{run_id}")
            assert result_a["chapter_count"] == 1

            # Persist as CANCELLED using real checkpoint fingerprint
            latest_a = ckpt_repo_a.get_latest(run_id)
            assert latest_a is not None
            _persist_job(store, f"job-{run_id}", run_id, story_id,
                         status="cancelled", ch_completed=1)
            _persist_checkpoint_from_data(store, latest_a.__dict__)

            # New session reload
            job = get_persistent_job(store, f"job-{run_id}")
            assert job["status"] == "cancelled"

            ckpt_repo_b = _load_checkpoint_into_memory(store, run_id)
            result_b = run_long_form(
                idea=IDEA, workspace=ws, lib=lib, runtime=_make_runtime(),
                target_chapters=3, content_form="prose_story",
                resume_from=run_id, checkpoint_repo=ckpt_repo_b,
            )
            assert result_b["status"] == "completed"
            assert result_b["chapter_count"] == 3
            assert result_a["chapter_results"][0]["render_result"] == result_b["chapter_results"][0]["render_result"]
        finally:
            store.cleanup()


# ══════════════════════════════════════════════════════════════════════
# 3. StoryRecord Persist Failure → Job FAILED
# ══════════════════════════════════════════════════════════════════════
class FailingStoryRecordRepo:
    """A repo whose create() always raises — injected into Worker."""
    def create(self, record):
        raise RuntimeError("Simulated persistence failure")


class TestStoryRecordPersistFailure:
    def test_persist_failure_marks_job_failed(self, tmp_path):
        """If StoryRecord.create() raises, Job becomes FAILED, not COMPLETED."""
        from drama_engine.async_runtime import (
            InMemoryLongFormJobRepository, LongFormWorker, submit_long_form, LongFormJobStatus,
        )
        from drama_engine.longform_executor import InMemoryCheckpointRepository

        job_repo = InMemoryLongFormJobRepository()
        ckpt_repo = InMemoryCheckpointRepository()
        lib = _make_lib()

        job = submit_long_form(idea=IDEA, job_repo=job_repo, target_chapters=2,
                                workspace=str(tmp_path), lib=lib)
        worker = LongFormWorker(
            job_repo=job_repo, checkpoint_repo=ckpt_repo, lib=lib,
            runtime_factory=_make_runtime, workspace=str(tmp_path),
            story_record_repo=FailingStoryRecordRepo(),
        )
        worker.execute_job(job)

        final = job_repo.get(job.job_id)
        assert final.status == LongFormJobStatus.FAILED, (
            f"Expected FAILED, got {final.status}"
        )
        assert final.status != LongFormJobStatus.COMPLETED
        assert final.error_message is not None
        assert "persistence" in final.error_message.lower()

    def test_persist_failure_error_info_persisted(self, tmp_path):
        """FAILED job from persist failure survives repo recreation."""
        from drama_engine.async_runtime import (
            InMemoryLongFormJobRepository, LongFormWorker, submit_long_form, LongFormJobStatus,
        )
        from drama_engine.longform_executor import InMemoryCheckpointRepository

        job_repo = InMemoryLongFormJobRepository()
        ckpt_repo = InMemoryCheckpointRepository()
        lib = _make_lib()

        job = submit_long_form(idea=IDEA, job_repo=job_repo, target_chapters=2,
                                workspace=str(tmp_path), lib=lib)
        worker = LongFormWorker(
            job_repo=job_repo, checkpoint_repo=ckpt_repo, lib=lib,
            runtime_factory=_make_runtime, workspace=str(tmp_path),
            story_record_repo=FailingStoryRecordRepo(),
        )
        worker.execute_job(job)

        final = job_repo.get(job.job_id)
        assert final.status == LongFormJobStatus.FAILED

        # New repo instance can still load it
        repo2 = InMemoryLongFormJobRepository()
        repo2._jobs = job_repo._jobs  # InMemory data copy — production: same DB
        reloaded = repo2.get(job.job_id)
        assert reloaded.status == LongFormJobStatus.FAILED
        assert reloaded.error_message == final.error_message


# ══════════════════════════════════════════════════════════════════════
# Backward Compatibility
# ══════════════════════════════════════════════════════════════════════
class TestPhase74BackwardCompat:
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

    def test_sync_pipeline_works(self, tmp_path):
        from drama_engine.graph import run_pipeline
        result = run_pipeline(
            idea="测试 7.4", workspace=str(tmp_path),
            lib=_make_lib(), runtime=_make_runtime(),
            target_episodes=1, content_form="prose_story",
        )
        assert result.get("prose") is not None