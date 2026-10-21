from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from .io import file_hash, under


class TransientFailure(Exception):
    """Confirmed retryable failure, not an ambiguous external submission."""


class UnknownResult(Exception):
    pass


class CapabilityError(Exception):
    pass


class Registry:
    """Persistent result ledger, independent of graph checkpoints and interrupt replays."""
    def __init__(self, root: Path):
        self.root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "tasks.sqlite"
        self.locks: dict[str, threading.Lock] = {}
        self.guard = threading.Lock()
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, result TEXT, error TEXT, updated REAL NOT NULL)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def get(self, task_id: str):
        with self.connect() as db:
            row = db.execute("SELECT status, attempts, result, error FROM tasks WHERE id=?", (task_id,)).fetchone()
        return None if row is None else dict(task_id=task_id, status=row[0], attempts=row[1], result=json.loads(row[2]) if row[2] else None, error=row[3])

    def list(self):
        with self.connect() as db:
            return [dict(task_id=r[0], status=r[1], attempts=r[2], error=r[3]) for r in db.execute("SELECT id,status,attempts,error FROM tasks ORDER BY id")]

    def set(self, task_id: str, status: str, attempts: int, result=None, error=None):
        with self.connect() as db:
            db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,attempts=excluded.attempts,result=excluded.result,error=excluded.error,updated=excluded.updated", (task_id, status, attempts, json.dumps(result) if result else None, error, time.time()))

    def run(self, task_id: str, external: bool, action: Callable[[], dict], verify: Callable[[dict], None], retry_delay_seconds=2):
        with self.guard:
            lock = self.locks.setdefault(task_id, threading.Lock())
        with lock:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT status,attempts,result FROM tasks WHERE id=?", (task_id,)).fetchone()
                if row and row[0] == "success":
                    result = json.loads(row[2])
                    verify(result)
                    return result | {"cache_reused": True}
                if row and row[0] in {"unknown", "running"} and external:
                    exc = UnknownResult(f"Task {task_id} needs result recovery; automatic resubmission disabled")
                    exc.task_id = task_id
                    raise exc
                attempts = row[1] if row else 0
                if attempts >= 3:
                    raise CapabilityError("Automatic retry budget exhausted; use a new reviewed candidate")
                if row and row[0] in {"configuration_error", "failed"}:
                    raise CapabilityError(f"Task {task_id}: {row[0]}; change configuration or create a new candidate")
                db.execute("INSERT INTO tasks VALUES(?, 'running', ?, NULL, NULL, ?) ON CONFLICT(id) DO UPDATE SET status='running',updated=excluded.updated", (task_id, attempts, time.time()))
            while attempts < 3:
                attempts += 1
                self.set(task_id, "running", attempts)
                try:
                    result = action()
                    verify(result)
                    self.set(task_id, "success", attempts, result)
                    return result | {"cache_reused": False}
                except TransientFailure as exc:
                    self.set(task_id, "retryable" if attempts < 3 else "failed", attempts, error="confirmed_transient_failure")
                    if attempts < 3:
                        time.sleep(min(60, max(float(getattr(exc, "retry_after_seconds", 0)), retry_delay_seconds * 2 ** (attempts - 1))))
                except UnknownResult as exc:
                    details = getattr(exc, "diagnostics", None)
                    self.set(task_id, "unknown", attempts, error=json.dumps(details) if details else "external_result_unknown")
                    exc.task_id = task_id
                    raise
                except CapabilityError as exc:
                    details = getattr(exc, "diagnostics", None)
                    blocked = details and details.get("category") == "budget_exhausted"
                    self.set(task_id, "budget_blocked" if blocked else "configuration_error", attempts - 1 if blocked else attempts,
                             error=json.dumps(details) if details else "configuration_or_capability_error")
                    raise
                except Exception:
                    # Do not write raw SDK errors: they can contain credentials or signed URLs.
                    self.set(task_id, "unknown" if external else "failed", attempts, error="external_result_unknown" if external else "local_processing_failed")
                    if external:
                        raise UnknownResult(f"Task {task_id}: external result uncertain") from None
                    raise
            raise TransientFailure(f"Task {task_id}: retry limit reached")

    def recover(self, task_id: str, result: dict):
        current = self.get(task_id)
        if not current or current["status"] not in {"running", "unknown"}:
            raise ValueError("Only unknown/running results may be recovered")
        verify_file(self.root, result)
        self.set(task_id, "success", current["attempts"], result)

    def confirm_not_executed(self, task_id: str):
        current = self.get(task_id)
        if not current or current["status"] not in {"unknown", "running"}:
            raise ValueError("Task is not awaiting recovery")
        # Deliberate operator action, never invoked automatically on timeout.
        self.set(task_id, "retryable", current["attempts"], error="operator_confirmed_no_result")


def verify_file(root: Path, result: dict):
    if file_hash(under(root, result["file"])) != result["sha256"]:
        raise ValueError("Cached media hash mismatch")
