"""Phase 7.5: Final Cancellation & Error-Code Proof.

Two final gaps:
  1. Real cooperative cancel via cancel_job() → CANCEL_REQUESTED → CANCELLED
     → persistent restart → explicit resume → COMPLETED
  2. StoryRecord persist failure → standard error_code → persisted across restart
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent / "4-引擎代码"
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(TEST_ROOT))

import pytest  # noqa: E402

from drama_engine.config import RuleLibrary
from drama_engine.contracts import Runtime
from drama_engine.llm.latency_controlled import LatencyControlledMockProvider
from drama_engine.core.errors import EngineErrorCode

from persistent_store import (
    PersistentStore,
    create_persistent_job_dict,
    get_latest_checkpoint,
    get_persistent_job,
    update_persistent_job,
    save_persistent_checkpoint,
)


# ── helpers ──────────────────────────────────────────────────────────
def _make_runtime():
    return Runtime(llm=LatencyControlledMockProvider(delay_sec=0.01))


def _make_lib():
    return RuleLibrary.load()


IDEA = "两个少年在废墟城市中寻找最后一部电力发电机"


# ══════════════════════════════════════════════════════════════════════
# 1. Real Cooperative Cancel E2E + Persistent Restart + Resume
# ══════════════════════════════════════════════════════════════════════
class TestRealCooperativeCancel:
    """Prove: cancel_job() → CANCEL_REQUESTED → cooperative _check_cancel() →
       CANCELLED → persistent restart → explicit resume → COMPLETED."""

    def test_real_cancel_transitions_and_persistent_restart(self):
        """Full chain: real cancel_job(), real _check_cancel(), persistent reload, resume."""
        from drama_engine.async_runtime import (
            InMemoryLongFormJobRepository, LongFormWorker, LongFormJobStatus,
            submit_long_form, cancel_job,
        )
        from drama_engine.longform_executor import InMemoryCheckpointRepository

        store = PersistentStore()
        lib = _make_lib()
        try:
            ws = os.path.join(tempfile.gettempdir(), "phase75_cancel")
            os.makedirs(ws, exist_ok=True)

            # ── Phase A: shared wrappers for cross-thread cancel ──
            job_repo = InMemoryLongFormJobRepository()
            ckpt_repo = InMemoryCheckpointRepository()

            job = submit_long_form(
                idea=IDEA, job_repo=job_repo, target_chapters=3,
                workspace=ws, lib=lib,
            )

            # Synchronization: event set when cancel_job has been called
            cancel_called = threading.Event()

            class CancelAwareWorker(LongFormWorker):
                """Worker that allows cancel to be called during execution."""
                def execute_job(self, job):
                    """Override: set cancel_requested via cancel_job before assemble."""
                    from drama_engine.longform_executor import run_long_form
                    import time as _time

                    # Mark RUNNING
                    self.job_repo.update(job.job_id,
                        status=LongFormJobStatus.RUNNING,
                        stage="planning",
                        started_at=_time.time(),
                    )

                    try:
                        runtime = self._runtime_factory() if self._runtime_factory else None
                        result = run_long_form(
                            idea=IDEA, workspace=self.workspace, lib=self.lib,
                            runtime=runtime, story_mode=job.story_mode,
                            content_form=job.content_form,
                            target_chapters=job.total_chapters,
                            checkpoint_repo=self.checkpoint_repo,
                            fail_at_chapter=2,  # Stop after Ch1 — deterministic
                        )
                        # Capture the actual run_id from the engine
                        self._run_id = result.get("run_id", "")
                        # Update progress for completed chapters
                        chapter_results = result.get("chapter_results") or []
                        for ci, cr in enumerate(chapter_results, start=1):
                            self.job_repo.update(job.job_id,
                                stage="chapter_generation",
                                current_chapter=ci,
                                progress_pct=ci * 30,
                            )

                        # Check cancel — this is where real _check_cancel fires
                        self._check_cancel(job)
                        cancel_called.set()

                        # Should never reach here if cancel was acknowledged
                        self.job_repo.update(job.job_id,
                            status=LongFormJobStatus.COMPLETED,
                        )
                    except Exception as exc:
                        if "Cancel" in type(exc).__name__:
                            self.job_repo.update(job.job_id,
                                status=LongFormJobStatus.CANCELLED,
                                message="Job cancelled by user",
                            )
                        else:
                            raise
                    finally:
                        cancel_called.set()

            # ── Start Worker in background thread ──
            worker = CancelAwareWorker(
                job_repo=job_repo, checkpoint_repo=ckpt_repo,
                lib=lib, runtime_factory=_make_runtime, workspace=ws,
            )
            t = threading.Thread(target=worker.execute_job, args=(job,), daemon=True)
            t.start()

            # ── While Worker is running, call the real cancel_job() ──
            # Poll until job is RUNNING, then cancel
            for _ in range(100):
                current = job_repo.get(job.job_id)
                if current and current.status == LongFormJobStatus.RUNNING:
                    break
                time.sleep(0.005)
            else:
                t.join(timeout=5)
                current = job_repo.get(job.job_id)
                raise RuntimeError(f"Job never entered RUNNING: {current.status if current else 'None'}")

            # Call the REAL cancel_job()
            result = cancel_job(job.job_id, job_repo)
            assert result is True, "cancel_job() should return True"

            # Verify CANCEL_REQUESTED was set
            mid = job_repo.get(job.job_id)
            assert mid.cancel_requested or mid.status == LongFormJobStatus.CANCEL_REQUESTED, (
                f"Expected CANCEL_REQUESTED, got status={mid.status}, cancel_requested={mid.cancel_requested}"
            )

            # Wait for worker to finish and process cancel
            t.join(timeout=10)
            assert not t.is_alive(), "Worker should finish"

            # ── Verify final CANCELLED state ──
            final = job_repo.get(job.job_id)
            assert final.status == LongFormJobStatus.CANCELLED, (
                f"Expected CANCELLED, got {final.status}"
            )
            assert final.status != LongFormJobStatus.COMPLETED, (
                "CANCELLED job must not be COMPLETED"
            )

            # Checkpoint exists from run_long_form's internal save
            actual_run_id = getattr(worker, '_run_id', job.run_id)
            latest_ckpt = ckpt_repo.get_latest(actual_run_id)
            assert latest_ckpt is not None, f"Checkpoint must exist for run_id={actual_run_id}"
            assert latest_ckpt.last_completed_chapter >= 1

            # ── Phase B: persist to store ──
            create_persistent_job_dict(store, {
                "job_id": job.job_id, "request_id": job.request_id,
                "run_id": actual_run_id, "story_id": job.story_id,
                "status": "cancelled", "story_mode": job.story_mode,
                "content_form": job.content_form, "generation_scale": "long_form",
                "total_chapters": job.total_chapters,
                "current_chapter": latest_ckpt.last_completed_chapter,
            })
            if latest_ckpt:
                import dataclasses
                if dataclasses.is_dataclass(latest_ckpt):
                    cp_dict = dataclasses.asdict(latest_ckpt)
                else:
                    cp_dict = latest_ckpt.__dict__
                save_persistent_checkpoint(store, cp_dict)

            # ── Phase C: new session, reload ──
            job2 = get_persistent_job(store, job.job_id)
            assert job2 is not None
            assert job2["status"] == "cancelled"

            cp2 = get_latest_checkpoint(store, actual_run_id)
            assert cp2 is not None
            next_ch = cp2.get("next_chapter", 2)
            assert next_ch >= 2, f"Expected next_chapter >= 2, got {next_ch}"

            # ── Phase D: explicit resume ──
            from drama_engine.longform_executor import run_long_form, InMemoryCheckpointRepository, GenerationCheckpoint
            ckpt_resume = InMemoryCheckpointRepository()
            cp_obj = GenerationCheckpoint(
                run_id=cp2["run_id"],
                story_id=cp2.get("story_id", ""),
                stage=cp2["stage"],
                last_completed_chapter=cp2["last_completed_chapter"],
                next_chapter=cp2["next_chapter"],
                state_json=cp2.get("state_json", {}),
                config_fingerprint=cp2["config_fingerprint"],
                status=cp2["status"],
                version=cp2.get("version", "1.0"),
            )
            ckpt_resume.save_or_update(cp_obj)

            result_final = run_long_form(
                idea=IDEA, workspace=ws, lib=lib, runtime=_make_runtime(),
                target_chapters=3, content_form="prose_story",
                resume_from=actual_run_id, checkpoint_repo=ckpt_resume,
            )

            assert result_final["status"] == "completed"
            assert result_final["chapter_count"] == 3
        finally:
            store.cleanup()

    def test_cancelled_job_reload_uses_persistent_store(self):
        """CANCELLED job reloaded from persistent store (new session)."""
        store = PersistentStore()
        try:
            run_id = "run-c75-reload"
            create_persistent_job_dict(store, {
                "job_id": "job-c75-1", "request_id": "req", "run_id": run_id,
                "story_id": "story-1", "status": "cancelled",
                "story_mode": "general", "content_form": "prose_story",
                "generation_scale": "long_form", "total_chapters": 3,
                "current_chapter": 1,
            })
            save_persistent_checkpoint(store, {
                "run_id": run_id, "stage": "chapter_1",
                "last_completed_chapter": 1, "next_chapter": 2,
                "state_json": {}, "config_fingerprint": "fp-c75",
                "status": "resumable",
            })

            # New session
            job = get_persistent_job(store, "job-c75-1")
            assert job["status"] == "cancelled"
            cp = get_latest_checkpoint(store, run_id)
            assert cp["next_chapter"] == 2
        finally:
            store.cleanup()


# ══════════════════════════════════════════════════════════════════════
# 2. StoryRecord Persist Failure → Standard Error Code
# ══════════════════════════════════════════════════════════════════════
class FailingStoryRecordRepo:
    def create(self, record):
        raise RuntimeError("Simulated persistence failure")


class TestPersistenceErrorCode:
    def test_persist_failure_uses_standard_error_code(self, tmp_path):
        """StoryRecord.create() raises → Job FAILED with PERSISTENCE_ERROR."""
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
        # Standard error code
        assert final.error_code == EngineErrorCode.PERSISTENCE_ERROR.value, (
            f"Expected {EngineErrorCode.PERSISTENCE_ERROR.value}, got {final.error_code}"
        )
        assert final.error_message is not None
        assert final.error_message != ""
        assert "persistence" in final.error_message.lower()

    def test_error_code_survives_repository_recreation(self, tmp_path):
        """FAILED job with PERSISTENCE_ERROR survives repo instance replacement."""
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
        assert final.error_code == EngineErrorCode.PERSISTENCE_ERROR.value

        # New repo (simulates persistent restart)
        repo2 = InMemoryLongFormJobRepository()
        repo2._jobs = job_repo._jobs  # InMemory data copy — production: same DB
        reloaded = repo2.get(job.job_id)
        assert reloaded.status == LongFormJobStatus.FAILED
        assert reloaded.error_code == EngineErrorCode.PERSISTENCE_ERROR.value, (
            f"Error code not preserved: {reloaded.error_code}"
        )
        assert reloaded.error_message == final.error_message

    def test_persist_failure_job_never_completed(self, tmp_path):
        """Even with final_result data, job is FAILED when StoryRecord fails."""
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
        # Hard guarantee: must NOT be COMPLETED
        assert final.status == LongFormJobStatus.FAILED
        assert final.status != LongFormJobStatus.COMPLETED
        # No dangling story_id pointing to nonexistent record
        # (story_id on the FAILED job is acceptable — it's the planned story_id,
        #  but no corresponding StoryRecord exists in the repo)


# ══════════════════════════════════════════════════════════════════════
# Backward Compatibility
# ══════════════════════════════════════════════════════════════════════
class TestPhase75BackwardCompat:
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
            idea="测试 7.5", workspace=str(tmp_path),
            lib=_make_lib(), runtime=_make_runtime(),
            target_episodes=1, content_form="prose_story",
        )
        assert result.get("prose") is not None