"""Phase 7.1: Async Job Runtime — submit, worker, progress, cancel, restart.

Architecture:
    Async Runtime (THIS MODULE)
        ↓  calls
    Long-form Engine (longform_executor.py)
        ↓  calls
    Phase 6 graph (graph.py)

Layers are separate: Engine remains synchronous; Async Runtime wraps it.

Key entities:
  - LongFormJob: extended StoryJob with long-form fields
  - LongFormWorker: executes jobs in background
  - submit_long_form() → job_id  (non-blocking)
  - get_job(job_id) → status/progress
  - cancel_job(job_id) → cooperative cancel
  - resume_job(job_id) → restart from checkpoint
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .core.job_repo import InMemoryJobRepository, JobRecord, JobRepository, JobStatus


# ═══════════════════════════════════════════════════════════════════
# Extended Job Status
# ═══════════════════════════════════════════════════════════════════
class LongFormJobStatus(str, Enum):
    """Extended statuses reusing Phase 1 JobStatus + CANCEL_REQUESTED."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


# Map to/from core JobStatus
_STATUS_MAP = {
    LongFormJobStatus.PENDING: JobStatus.PENDING,
    LongFormJobStatus.RUNNING: JobStatus.RUNNING,
    LongFormJobStatus.COMPLETED: JobStatus.SUCCEEDED,
    LongFormJobStatus.FAILED: JobStatus.FAILED,
    LongFormJobStatus.CANCEL_REQUESTED: JobStatus.RUNNING,  # still running
    LongFormJobStatus.CANCELLED: JobStatus.CANCELLED,
}


# ═══════════════════════════════════════════════════════════════════
# Long-form Job Record
# ═══════════════════════════════════════════════════════════════════
@dataclass
class LongFormJobRecord:
    """Extended Job record for long-form async execution."""
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    request_id: str = ""
    idea: str = ""
    run_id: str = ""
    story_id: str = ""

    # Config
    story_mode: str = "general"
    template_id: str = ""
    template_version: str = ""
    content_form: str = "prose_story"
    generation_scale: str = "long_form"

    # Progress
    status: LongFormJobStatus = LongFormJobStatus.PENDING
    stage: str = ""            # planning | chapter_generation | assembling | persisting
    progress_pct: int = 0
    current_chapter: int = 0
    total_chapters: int = 0
    message: str = ""

    # Cancellation
    cancel_requested: bool = False

    # Error
    error_code: str = ""
    error_message: str = ""

    # Timing
    created_at: float = 0.0
    started_at: float = 0.0
    updated_at: float = 0.0
    completed_at: float = 0.0

    # Result reference
    final_result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "request_id": self.request_id,
            "idea": self.idea,
            "run_id": self.run_id,
            "story_id": self.story_id,
            "story_mode": self.story_mode,
            "template_id": self.template_id,
            "content_form": self.content_form,
            "generation_scale": self.generation_scale,
            "status": self.status.value,
            "stage": self.stage,
            "progress_pct": self.progress_pct,
            "current_chapter": self.current_chapter,
            "total_chapters": self.total_chapters,
            "message": self.message,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


# ═══════════════════════════════════════════════════════════════════
# Progress Calculator
# ═══════════════════════════════════════════════════════════════════
class ProgressCalculator:
    """Compute progress_pct based on stage and chapter progress.

    Weight distribution:
        planning:         5%
        chapter_gen:     75%  (split across chapters)
        assembling:      10%
        persisting:       5%
        completed:      100%
    """

    WEIGHTS = {
        "planning": 5,
        "chapter_generation": 75,
        "assembling": 10,
        "persisting": 5,
    }

    @classmethod
    def compute(cls, stage: str, current_chapter: int, total_chapters: int) -> int:
        if stage == "completed":
            return 100
        if stage == "planning":
            return cls.WEIGHTS["planning"]
        if stage == "chapter_generation":
            if total_chapters <= 0:
                return cls.WEIGHTS["planning"]
            # planning + proportional chapter progress (current_chapter = last COMPLETED)
            chapter_pct = current_chapter / total_chapters * cls.WEIGHTS["chapter_generation"]
            return cls.WEIGHTS["planning"] + int(chapter_pct)
        if stage == "assembling":
            return cls.WEIGHTS["planning"] + cls.WEIGHTS["chapter_generation"] + cls.WEIGHTS["assembling"] // 2
        if stage == "persisting":
            return sum(cls.WEIGHTS.values()) - 2
        return 0


# ═══════════════════════════════════════════════════════════════════
# Long-form Job Repository
# ═══════════════════════════════════════════════════════════════════
class InMemoryLongFormJobRepository:
    """In-memory repository for LongFormJobRecord."""

    def __init__(self):
        self._jobs: dict[str, LongFormJobRecord] = {}

    def create(self, job: LongFormJobRecord) -> LongFormJobRecord:
        job.created_at = time.time()
        self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> LongFormJobRecord | None:
        return self._jobs.get(job_id)

    def get_by_run_id(self, run_id: str) -> LongFormJobRecord | None:
        for job in self._jobs.values():
            if job.run_id == run_id:
                return job
        return None

    def update(self, job_id: str, **fields) -> LongFormJobRecord | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        for key, value in fields.items():
            if hasattr(job, key):
                setattr(job, key, value)
        job.updated_at = time.time()
        return job

    def request_cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.status in (LongFormJobStatus.RUNNING, LongFormJobStatus.PENDING):
            job.cancel_requested = True
            job.status = LongFormJobStatus.CANCEL_REQUESTED
            job.updated_at = time.time()
            return True
        return False

    def list_by_status(self, status: LongFormJobStatus | None = None) -> list[LongFormJobRecord]:
        jobs = list(self._jobs.values())
        if status:
            jobs = [j for j in jobs if j.status == status]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)


# ═══════════════════════════════════════════════════════════════════
# Async Submit
# ═══════════════════════════════════════════════════════════════════
def submit_long_form(
    idea: str,
    story_mode: str = "general",
    story_template_id: str | None = None,
    story_template_version: int | None = None,
    content_form: str = "prose_story",
    target_chapters: int = 3,
    job_repo: InMemoryLongFormJobRepository | None = None,
    checkpoint_repo: Any = None,
    workspace: str = "",
    lib: Any = None,
    runtime: Any = None,
    thread_id: str = "local",
) -> LongFormJobRecord:
    """Submit a long-form generation job — returns immediately with job_id.

    Does NOT block until completion. The job is executed by a Worker.
    """
    if job_repo is None:
        job_repo = InMemoryLongFormJobRepository()

    job = LongFormJobRecord(
        request_id=thread_id,
        idea=idea,
        run_id=uuid.uuid4().hex,
        story_id=uuid.uuid4().hex,
        story_mode=story_mode,
        template_id=story_template_id or "",
        template_version=str(story_template_version or ""),
        content_form=content_form,
        generation_scale="long_form",
        total_chapters=target_chapters,
        status=LongFormJobStatus.PENDING,
        stage="planning",
        message="Job submitted",
    )
    job_repo.create(job)
    return job


def get_job(
    job_id: str,
    job_repo: InMemoryLongFormJobRepository,
) -> dict[str, Any] | None:
    """Query job status and progress."""
    job = job_repo.get(job_id)
    if job is None:
        return None
    return job.to_dict()


def cancel_job(
    job_id: str,
    job_repo: InMemoryLongFormJobRepository,
) -> bool:
    """Request cooperative cancellation."""
    return job_repo.request_cancel(job_id)


# ═══════════════════════════════════════════════════════════════════
# Worker
# ═══════════════════════════════════════════════════════════════════
class LongFormWorker:
    """Minimal in-process worker for async long-form execution.

    Production would use a task queue; this implementation runs jobs
    in a background thread for testing and single-machine deployment.
    """

    def __init__(
        self,
        job_repo: InMemoryLongFormJobRepository,
        checkpoint_repo: Any = None,
        lib: Any = None,
        runtime_factory: Callable[[], Any] | None = None,
        workspace: str = "",
        story_record_repo: Any = None,
    ):
        self.job_repo = job_repo
        self.checkpoint_repo = checkpoint_repo
        self.lib = lib
        self._runtime_factory = runtime_factory
        self.workspace = workspace
        self.story_record_repo = story_record_repo

    def execute_job(self, job: LongFormJobRecord) -> None:
        """Execute a single job synchronously (called by run_job_async)."""
        from .longform_executor import run_long_form, InMemoryCheckpointRepository

        if self.checkpoint_repo is None:
            self.checkpoint_repo = InMemoryCheckpointRepository()

        runtime = self._runtime_factory() if self._runtime_factory else None

        # Mark RUNNING
        self.job_repo.update(job.job_id,
            status=LongFormJobStatus.RUNNING,
            stage="planning",
            progress_pct=ProgressCalculator.compute("planning", 0, job.total_chapters),
            started_at=time.time(),
            message="Planning started",
        )

        try:
            # Phase 1: Planning
            self._check_cancel(job)

            # Run long-form (sync, but in worker thread)
            result = run_long_form(
                idea=job.idea or "long-form job",
                workspace=self.workspace,
                lib=self.lib,
                runtime=runtime,
                story_mode=job.story_mode,
                story_template_id=job.template_id or None,
                story_template_version=int(job.template_version) if job.template_version else None,
                content_form=job.content_form,
                target_chapters=job.total_chapters,
                checkpoint_repo=self.checkpoint_repo,
                thread_id=job.request_id,
            )

            # Update progress per chapter from result
            chapter_results = result.get("chapter_results") or []

            # A returned dict is not proof of success.  The long-form engine
            # deliberately returns resumable failed results, so validate the
            # result before persisting a StoryRecord or marking the job done.
            result_status = result.get("status")
            completed_chapters = result.get("chapter_count", len(chapter_results))
            all_chapters_completed = all(
                (cr.get("status") if isinstance(cr, dict) else getattr(cr, "status", None))
                == "completed"
                for cr in chapter_results
            )
            if (
                result_status != "completed"
                or completed_chapters != job.total_chapters
                or len(chapter_results) != job.total_chapters
                or not all_chapters_completed
            ):
                self.job_repo.update(
                    job.job_id,
                    status=LongFormJobStatus.FAILED,
                    stage="failed",
                    error_code="GENERATION_INCOMPLETE",
                    error_message=(
                        f"Long-form result not complete: status={result_status}, "
                        f"chapters={completed_chapters}/{job.total_chapters}"
                    ),
                    completed_at=time.time(),
                    final_result=result,
                )
                return
            for cr in chapter_results:
                self._check_cancel(job)
                ci = cr.get("chapter_index", 0)
                self.job_repo.update(job.job_id,
                    stage="chapter_generation",
                    current_chapter=ci,
                    progress_pct=ProgressCalculator.compute(
                        "chapter_generation", ci, job.total_chapters),
                    message=f"Chapter {ci} complete",
                )

            # Assemble
            self._check_cancel(job)
            self.job_repo.update(job.job_id,
                stage="assembling",
                progress_pct=ProgressCalculator.compute("assembling", 0, 0),
                message="Assembling final result",
            )

            # Persist StoryRecord BEFORE marking job COMPLETED
            story_id = result.get("story_id", job.story_id)
            if self.story_record_repo is not None:
                try:
                    from drama_engine.persistence.repository import StoryRecord
                    record = StoryRecord(
                        story_id=story_id,
                        request_id=job.request_id,
                        job_id=job.job_id,
                        story_mode=job.story_mode,
                        title="",
                        content_json={
                            "generation_scale": "long_form",
                            "chapter_results": result.get("chapter_results", []),
                            "plan": result.get("plan", {}),
                            "content_form": job.content_form,
                            "template_id": job.template_id,
                        },
                        output_json=result,
                        engine_version="0.2.1",
                        content_form=job.content_form,
                        template_ids=[job.template_id] if job.template_id else [],
                    )
                    self.story_record_repo.create(record)
                except Exception:
                    # StoryRecord persist failed → job FAILED, not COMPLETED
                    from drama_engine.core.errors import EngineErrorCode
                    self.job_repo.update(job.job_id,
                        status=LongFormJobStatus.FAILED,
                        error_code=EngineErrorCode.PERSISTENCE_ERROR.value,
                        error_message="StoryRecord persistence failed",
                        completed_at=time.time(),
                    )
                    return

            # Complete — only after StoryRecord persisted
            self.job_repo.update(job.job_id,
                status=LongFormJobStatus.COMPLETED,
                stage="completed",
                progress_pct=100,
                current_chapter=job.total_chapters,
                completed_at=time.time(),
                message="Job completed",
                story_id=story_id,
                run_id=result.get("run_id", job.run_id),
                final_result=result,
            )

        except _CancelException:
            self.job_repo.update(job.job_id,
                status=LongFormJobStatus.CANCELLED,
                message="Job cancelled by user",
                completed_at=time.time(),
            )
        except Exception as exc:
            self.job_repo.update(job.job_id,
                status=LongFormJobStatus.FAILED,
                stage="failed",
                error_code=type(exc).__name__,
                error_message=str(exc)[:500],
                completed_at=time.time(),
                message=f"Job failed: {exc}",
            )

    def _check_cancel(self, job: LongFormJobRecord) -> None:
        """Raise _CancelException if job has been cancelled."""
        current = self.job_repo.get(job.job_id)
        if current and (current.cancel_requested or current.status == LongFormJobStatus.CANCEL_REQUESTED):
            raise _CancelException()

    def run_job_async(self, job: LongFormJobRecord) -> threading.Thread:
        """Execute job in a background thread. Returns the thread."""
        t = threading.Thread(target=self.execute_job, args=(job,), daemon=True)
        t.start()
        return t


class _CancelException(Exception):
    """Internal signal for cooperative cancellation."""
    pass


# ═══════════════════════════════════════════════════════════════════
# Resume helper
# ═══════════════════════════════════════════════════════════════════
def resume_job(
    job_id: str,
    job_repo: InMemoryLongFormJobRepository,
    checkpoint_repo: Any = None,
    lib: Any = None,
    runtime_factory: Callable[[], Any] | None = None,
    workspace: str = "",
) -> LongFormJobRecord | None:
    """Resume a cancelled or failed job from its latest checkpoint.

    Creates a new job record linked to the original run_id.
    """
    from .longform_executor import run_long_form, InMemoryCheckpointRepository

    original = job_repo.get(job_id)
    if original is None:
        return None

    if checkpoint_repo is None:
        checkpoint_repo = InMemoryCheckpointRepository()

    # Verify checkpoint exists
    latest = checkpoint_repo.get_latest(original.run_id)
    if latest is None or latest.status not in ("resumable", "completed"):
        return None

    runtime = runtime_factory() if runtime_factory else None

    # Create new job record for the resumed run
    resume_job = LongFormJobRecord(
        request_id=original.request_id,
        idea=original.idea,
        run_id=original.run_id,
        story_id=original.story_id,
        story_mode=original.story_mode,
        template_id=original.template_id,
        template_version=original.template_version,
        content_form=original.content_form,
        generation_scale="long_form",
        total_chapters=original.total_chapters,
        status=LongFormJobStatus.PENDING,
        stage="resuming",
        message=f"Resuming from chapter {latest.next_chapter}",
    )
    job_repo.create(resume_job)

    # Run resume
    result = run_long_form(
        idea=original.idea or "long-form job",
        workspace=workspace,
        lib=lib,
        runtime=runtime,
        story_mode=original.story_mode,
        story_template_id=original.template_id or None,
        story_template_version=int(original.template_version) if original.template_version else None,
        content_form=original.content_form,
        target_chapters=original.total_chapters,
        checkpoint_repo=checkpoint_repo,
        resume_from=original.run_id,
        thread_id=original.request_id,
    )

    job_repo.update(resume_job.job_id,
        status=LongFormJobStatus.COMPLETED if result.get("status") == "completed" else LongFormJobStatus.FAILED,
        stage="completed" if result.get("status") == "completed" else "failed",
        progress_pct=100 if result.get("status") == "completed" else 0,
        current_chapter=result.get("chapter_count", 0),
        completed_at=time.time(),
        message="Resume completed" if result.get("status") == "completed" else "Resume failed",
        final_result=result,
    )
    return resume_job
