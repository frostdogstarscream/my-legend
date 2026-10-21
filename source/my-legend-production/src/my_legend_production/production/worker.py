"""Persistent production scheduler. No browser or GET request drives execution."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time
from typing import Callable
from uuid import uuid4

from .artifacts import ArtifactStore
from .contracts import ProductionRequest, StageResult, fingerprint
from .repository import ProductionError, ProductionRepository

log = logging.getLogger(__name__)


@dataclass
class StageContext:
    repository: ProductionRepository
    store: ArtifactStore
    stage: dict

    @property
    def request(self):
        return ProductionRequest.model_validate(self.stage["request"])

    def check_active(self):
        try:
            self.repository.check_active(self.stage)
        except ProductionError:
            raise
        except Exception:
            raise ProductionError("STAGE_CONTROL_UNAVAILABLE") from None

    def effect(self, key: str, value, operation: Callable, *, units=0, replay_safe=False):
        """Persist intent first. Only upstream-guaranteed idempotent effects may replay."""
        call_key = self.stage["name"] + ":" + key
        call = self.repository.reserve_call(self.stage, call_key, fingerprint(value), units)
        if call["state"] == "completed":
            return call["result"]
        if not call.get("new") and not replay_safe:
            self.repository.unknown_call(self.stage, call_key)
            raise ProductionError("EXTERNAL_RESULT_UNKNOWN")
        if call["state"] == "unknown":
            raise ProductionError("EXTERNAL_RESULT_UNKNOWN")
        try:
            result = operation()
        except Exception:
            if not replay_safe:
                self.repository.unknown_call(self.stage, call_key)
            raise ProductionError("UPSTREAM_RETRYABLE" if replay_safe else "EXTERNAL_RESULT_UNKNOWN") from None
        self.repository.complete_call(self.stage, call_key, result, actual_units=units)
        return result


class ProductionWorker:
    def __init__(self, repository, store, handlers: dict[str, Callable], *, worker_id=None, lease_seconds=30):
        self.repository, self.store, self.handlers = repository, store, handlers
        self.worker_id = worker_id or "worker-" + str(uuid4())
        self.lease_seconds = lease_seconds

    def tick(self):
        stage = self.repository.claim(self.worker_id, self.lease_seconds)
        if not stage:
            return False
        stopped = threading.Event()

        def heartbeat():
            while not stopped.wait(self.lease_seconds / 3):
                try:
                    if not self.repository.heartbeat(stage, self.lease_seconds):
                        return
                except Exception:
                    # Finish is fenced independently; an uncertain lease cannot commit stale output.
                    log.warning("PRODUCTION_HEARTBEAT_UNAVAILABLE")

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            handler = self.handlers.get(stage["name"])
            if handler is None:
                raise ProductionError("MODULE_NOT_CONFIGURED")
            result = StageResult.model_validate(handler(StageContext(self.repository, self.store, stage)))
            for artifact in result.artifacts:
                self.store.verify(artifact)
            self.repository.finish(stage, result)
        except ProductionError as exc:
            if exc.code != "LEASE_LOST":
                status = "needs_input" if exc.code in ("EXTERNAL_RESULT_UNKNOWN", "MODULE_NOT_CONFIGURED") else "failed"
                try:
                    self.repository.finish(stage, StageResult(status=status, checkpoint=stage["checkpoint"], error_code=exc.code))
                except ProductionError as nested:
                    if nested.code != "LEASE_LOST":
                        raise
        except Exception as exc:
            # Provider exceptions may contain secrets or prompts; persist only a fixed public code.
            log.error("PRODUCTION_STAGE_EXCEPTION type=%s", type(exc).__name__)
            try:
                self.repository.finish(stage, StageResult(status="failed", checkpoint=stage["checkpoint"], error_code="STAGE_INTERNAL_ERROR"))
            except ProductionError as nested:
                if nested.code != "LEASE_LOST":
                    raise
        finally:
            stopped.set()
            thread.join(timeout=2)
        return True

    def run(self, stop=None, idle_seconds=.5):
        stop = stop or threading.Event()
        while not stop.is_set():
            if not self.tick():
                stop.wait(idle_seconds)
