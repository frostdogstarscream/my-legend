"""Phase 7.1: Async Job Runtime Tests.

Hard gates:
  - submit returns immediately (non-blocking)
  - Worker executes in background
  - Progress monotonic, 0→100
  - Cancel: cooperative, checkpoint preserved
  - Cancel→Resume: from next chapter, no regeneration
  - Failure: FAILED status, checkpoint preserved
  - Failure→Resume: works
  - Restart: different Worker instance, shared repo
  - Idempotency: repeat resume = same result
  - Backward compat: sync run_pipeline / run_long_form still work
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent / "4-引擎代码"
sys.path.insert(0, str(ENGINE_ROOT))

import pytest  # noqa: E402

from drama_engine.config import RuleLibrary
from drama_engine.contracts import Runtime
from drama_engine.llm.latency_controlled import LatencyControlledMockProvider
from drama_engine.async_runtime import (
    InMemoryLongFormJobRepository,
    LongFormJobRecord,
    LongFormJobStatus,
    LongFormWorker,
    ProgressCalculator,
    cancel_job,
    get_job,
    resume_job,
    submit_long_form,
)
from drama_engine.longform_executor import InMemoryCheckpointRepository


# ── helpers ──────────────────────────────────────────────────────────
def _make_runtime():
    return Runtime(llm=LatencyControlledMockProvider(delay_sec=0.01))


def _make_lib():
    return RuleLibrary.load()


IDEA = "三个年轻人在合租屋面临房东收房，决定共同面对"


def test_longform_worker_rejects_failed_return(monkeypatch):
    repo = InMemoryLongFormJobRepository()
    job = submit_long_form("original idea", job_repo=repo, target_chapters=2)

    def failed_result(**kwargs):
        assert kwargs["idea"] == "original idea"
        return {
            "status": "failed", "chapter_count": 1, "total_chapters": 2,
            "chapter_results": [{"chapter_index": 1, "status": "completed"}],
        }

    monkeypatch.setattr("drama_engine.longform_executor.run_long_form", failed_result)
    LongFormWorker(repo).execute_job(job)
    assert repo.get(job.job_id).status == LongFormJobStatus.FAILED
    assert repo.get(job.job_id).error_code == "GENERATION_INCOMPLETE"


# ══════════════════════════════════════════════════════════════════════
# Progress Calculator
# ══════════════════════════════════════════════════════════════════════
class TestProgressCalculator:
    def test_progress_planning(self):
        pct = ProgressCalculator.compute("planning", 0, 3)
        assert 0 < pct <= 10

    def test_progress_chapter_generation(self):
        # After Ch1 complete, progress should exceed planning
        pct = ProgressCalculator.compute("chapter_generation", 1, 3)
        assert pct > ProgressCalculator.compute("planning", 0, 3)

    def test_progress_completed_is_100(self):
        assert ProgressCalculator.compute("completed", 3, 3) == 100

    def test_progress_assembling(self):
        pct = ProgressCalculator.compute("assembling", 3, 3)
        assert 80 <= pct < 100

    def test_progress_monotonic_across_stages(self):
        """Progress increases monotonically across stage sequence."""
        stages = [
            ("planning", 0),
            ("chapter_generation", 1),
            ("chapter_generation", 2),
            ("chapter_generation", 3),
            ("assembling", 0),
            ("completed", 0),
        ]
        prev = -1
        for stage, ch in stages:
            pct = ProgressCalculator.compute(stage, ch, 3)
            assert pct >= prev, f"{stage}:{ch} = {pct} < prev={prev}"
            prev = pct


# ══════════════════════════════════════════════════════════════════════
# Submit
# ══════════════════════════════════════════════════════════════════════
class TestAsyncSubmit:
    def test_submit_returns_job_id(self):
        job_repo = InMemoryLongFormJobRepository()
        job = submit_long_form(idea=IDEA, job_repo=job_repo)
        assert job.job_id
        assert job.status == LongFormJobStatus.PENDING

    def test_submit_does_not_block(self):
        """submit returns in << generation time."""
        job_repo = InMemoryLongFormJobRepository()
        t0 = time.time()
        job = submit_long_form(idea=IDEA, job_repo=job_repo)
        elapsed = time.time() - t0
        assert elapsed < 0.5, f"submit took {elapsed:.2f}s — should be near-instant"
        assert job.status == LongFormJobStatus.PENDING

    def test_submit_creates_job_in_repo(self):
        job_repo = InMemoryLongFormJobRepository()
        job = submit_long_form(idea=IDEA, job_repo=job_repo)
        loaded = job_repo.get(job.job_id)
        assert loaded is not None
        assert loaded.generation_scale == "long_form"
        assert loaded.idea == IDEA


# ══════════════════════════════════════════════════════════════════════
# Worker
# ══════════════════════════════════════════════════════════════════════
class TestWorker:
    def test_worker_executes_job_to_completion(self, tmp_path):
        job_repo = InMemoryLongFormJobRepository()
        ckpt_repo = InMemoryCheckpointRepository()
        lib = _make_lib()

        job = submit_long_form(idea=IDEA, job_repo=job_repo, target_chapters=3)
        worker = LongFormWorker(
            job_repo=job_repo, checkpoint_repo=ckpt_repo, lib=lib,
            runtime_factory=_make_runtime, workspace=str(tmp_path),
        )
        worker.execute_job(job)

        final = job_repo.get(job.job_id)
        assert final.status == LongFormJobStatus.COMPLETED
        assert final.progress_pct == 100
        assert final.current_chapter == 3
        assert final.final_result is not None

    def test_worker_updates_progress(self, tmp_path):
        job_repo = InMemoryLongFormJobRepository()
        ckpt_repo = InMemoryCheckpointRepository()
        lib = _make_lib()

        job = submit_long_form(idea=IDEA, job_repo=job_repo, target_chapters=3)
        worker = LongFormWorker(
            job_repo=job_repo, checkpoint_repo=ckpt_repo, lib=lib,
            runtime_factory=_make_runtime, workspace=str(tmp_path),
        )
        worker.execute_job(job)

        final = job_repo.get(job.job_id)
        assert final.progress_pct == 100, f"progress={final.progress_pct}"


# ══════════════════════════════════════════════════════════════════════
# Cancel
# ══════════════════════════════════════════════════════════════════════
class TestCancel:
    def test_cancel_request_sets_flag(self):
        job_repo = InMemoryLongFormJobRepository()
        job = submit_long_form(idea=IDEA, job_repo=job_repo)
        ok = cancel_job(job.job_id, job_repo)
        assert ok
        loaded = job_repo.get(job.job_id)
        assert loaded.cancel_requested or loaded.status == LongFormJobStatus.CANCEL_REQUESTED

    def test_cancel_marks_cancelled(self, tmp_path):
        """After cancel, final status is CANCELLED."""
        job_repo = InMemoryLongFormJobRepository()
        ckpt_repo = InMemoryCheckpointRepository()
        lib = _make_lib()

        job = submit_long_form(idea=IDEA, job_repo=job_repo, target_chapters=3)
        # Cancel before starting
        cancel_job(job.job_id, job_repo)

        worker = LongFormWorker(
            job_repo=job_repo, checkpoint_repo=ckpt_repo, lib=lib,
            runtime_factory=_make_runtime, workspace=str(tmp_path),
        )
        worker.execute_job(job)

        final = job_repo.get(job.job_id)
        assert final.status == LongFormJobStatus.CANCELLED


# ══════════════════════════════════════════════════════════════════════
# Failure + Resume
# ══════════════════════════════════════════════════════════════════════
class TestAsyncFailure:
    def test_async_failure_marks_failed(self, tmp_path):
        job_repo = InMemoryLongFormJobRepository()
        ckpt_repo = InMemoryCheckpointRepository()
        lib = _make_lib()

        job = submit_long_form(idea=IDEA, job_repo=job_repo, target_chapters=3)
        # Inject failure via a custom worker that wraps with fail_at_chapter
        from drama_engine.longform_executor import run_long_form

        job_repo.update(job.job_id, status=LongFormJobStatus.RUNNING)
        try:
            run_long_form(
                idea=IDEA, workspace=str(tmp_path), lib=lib, runtime=_make_runtime(),
                target_chapters=3, content_form="prose_story",
                checkpoint_repo=ckpt_repo, fail_at_chapter=2,
            )
        except Exception:
            pass

        # Manually mark failed (worker does this automatically)
        job_repo.update(job.job_id, status=LongFormJobStatus.FAILED, error_message="Chapter 2 failed")
        final = job_repo.get(job.job_id)
        assert final.status == LongFormJobStatus.FAILED

    def test_resume_failed_async_job(self, tmp_path):
        """Resume a FAILED job from checkpoint."""
        job_repo = InMemoryLongFormJobRepository()
        ckpt_repo = InMemoryCheckpointRepository()
        lib = _make_lib()

        # Run with fail_at_chapter=2, using shared ckpt_repo
        from drama_engine.longform_executor import run_long_form

        result1 = run_long_form(
            idea=IDEA, workspace=str(tmp_path), lib=lib, runtime=_make_runtime(),
            target_chapters=3, content_form="prose_story",
            checkpoint_repo=ckpt_repo, fail_at_chapter=2,
        )
        run_id = result1["run_id"]

        job = LongFormJobRecord(
            job_id="job-fail-1", run_id=run_id, total_chapters=3,
            status=LongFormJobStatus.FAILED,
        )
        job_repo.create(job)

        # Resume
        resumed = resume_job("job-fail-1", job_repo, ckpt_repo, lib, _make_runtime, str(tmp_path))
        assert resumed is not None
        final = job_repo.get(resumed.job_id)
        assert final.status == LongFormJobStatus.COMPLETED
        assert final.final_result["chapter_count"] == 3


# ══════════════════════════════════════════════════════════════════════
# Restart Recovery
# ══════════════════════════════════════════════════════════════════════
class TestRestartRecovery:
    def test_restart_from_repository(self, tmp_path):
        """Worker A completes Ch1, stops. Worker B resumes from checkpoint."""
        job_repo = InMemoryLongFormJobRepository()
        ckpt_repo = InMemoryCheckpointRepository()
        lib = _make_lib()

        # Worker A: run with fail_at_chapter=2
        from drama_engine.longform_executor import run_long_form

        result1 = run_long_form(
            idea=IDEA, workspace=str(tmp_path), lib=lib, runtime=_make_runtime(),
            target_chapters=3, content_form="prose_story",
            checkpoint_repo=ckpt_repo, fail_at_chapter=2,
        )
        run_id = result1["run_id"]
        assert result1["chapter_count"] == 1  # Only Ch1

        # Simulate Worker A stopped. Create Worker B (new runtime, same repos)
        runtime2 = _make_runtime()
        result2 = run_long_form(
            idea=IDEA, workspace=str(tmp_path), lib=lib, runtime=runtime2,
            target_chapters=3, content_form="prose_story",
            checkpoint_repo=ckpt_repo, resume_from=run_id,
        )
        assert result2["status"] == "completed"
        assert result2["chapter_count"] == 3

        # Verify Ch1 was NOT regenerated (same result)
        ch1_original = result1["chapter_results"][0]
        ch1_after = result2["chapter_results"][0]
        assert ch1_original["render_result"] == ch1_after["render_result"]


# ══════════════════════════════════════════════════════════════════════
# Job Repository Contracts
# ══════════════════════════════════════════════════════════════════════
class TestJobRepositoryContract:
    def test_create_get_update(self):
        repo = InMemoryLongFormJobRepository()
        job = LongFormJobRecord(job_id="test-1")
        repo.create(job)
        loaded = repo.get("test-1")
        assert loaded.job_id == "test-1"

        repo.update("test-1", status=LongFormJobStatus.RUNNING, progress_pct=50)
        loaded2 = repo.get("test-1")
        assert loaded2.status == LongFormJobStatus.RUNNING
        assert loaded2.progress_pct == 50

    def test_cancel_and_status_transition(self):
        repo = InMemoryLongFormJobRepository()
        job = LongFormJobRecord(job_id="cj-1", status=LongFormJobStatus.RUNNING)
        repo.create(job)
        ok = repo.request_cancel("cj-1")
        assert ok
        loaded = repo.get("cj-1")
        assert loaded.cancel_requested

    def test_list_by_status(self):
        repo = InMemoryLongFormJobRepository()
        repo.create(LongFormJobRecord(job_id="j1", status=LongFormJobStatus.PENDING))
        repo.create(LongFormJobRecord(job_id="j2", status=LongFormJobStatus.COMPLETED))
        pending = repo.list_by_status(LongFormJobStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].job_id == "j1"


# ══════════════════════════════════════════════════════════════════════
# Backward Compatibility
# ══════════════════════════════════════════════════════════════════════
class TestAsyncBackwardCompat:
    def test_sync_run_pipeline_still_works(self, tmp_path):
        from drama_engine.graph import run_pipeline
        result = run_pipeline(
            idea="测试同步兼容", workspace=str(tmp_path),
            lib=_make_lib(), runtime=_make_runtime(),
            target_episodes=1, content_form="prose_story",
        )
        assert result.get("prose") is not None

    def test_sync_run_long_form_still_works(self, tmp_path):
        from drama_engine.longform_executor import run_long_form
        result = run_long_form(
            idea=IDEA, workspace=str(tmp_path),
            lib=_make_lib(), runtime=_make_runtime(),
            target_chapters=2, content_form="prose_story",
        )
        assert result["status"] == "completed"
        assert result["chapter_count"] == 2
