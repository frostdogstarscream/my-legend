"""Job Repository —— Task 7: 轻量任务状态管理。

Phase 1 使用 InMemoryJobRepository（无外部数据库依赖）。
Phase 4 迁移至 PostgreSQL。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class JobRecord:
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    request_id: str = ""
    status: JobStatus = JobStatus.PENDING
    current_stage: str = ""
    error_code: str = ""
    error_message: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    progress_pct: int = 0

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "request_id": self.request_id,
            "status": self.status.value,
            "current_stage": self.current_stage,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress_pct": self.progress_pct,
        }


class JobRepository(Protocol):
    """任务存储接口。"""

    def create(self, request_id: str) -> JobRecord: ...
    def get(self, job_id: str) -> JobRecord | None: ...
    def update(self, job_id: str, **fields) -> JobRecord | None: ...
    def list_recent(self, limit: int = 20) -> list[JobRecord]: ...


class InMemoryJobRepository:
    """内存版 Job 存储。"""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}

    def create(self, request_id: str = "") -> JobRecord:
        job = JobRecord(request_id=request_id, status=JobStatus.PENDING)
        self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def update(self, job_id: str, **fields) -> JobRecord | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        for key, value in fields.items():
            if hasattr(job, key):
                setattr(job, key, value)
        return job

    def list_recent(self, limit: int = 20) -> list[JobRecord]:
        return sorted(
            self._jobs.values(),
            key=lambda j: j.started_at or 0,
            reverse=True,
        )[:limit]