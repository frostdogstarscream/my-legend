"""Durable call reservations; replays never reset a revision's spending window."""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

from .registry import CapabilityError


class BudgetExceeded(CapabilityError):
    pass


class CallBudget:
    def __init__(self, root: Path):
        self.path = root / "calls.sqlite"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, batch TEXT NOT NULL, kind TEXT NOT NULL, created REAL NOT NULL)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def limits(config: dict):
        if not isinstance(config, dict) or set(config) - {"max_calls", "batch_max_calls", "max_by_kind", "batch_max_by_kind"}:
            raise CapabilityError("Unknown call budget fields")
        limits = {"max_calls": 128, "batch_max_calls": 64,
                  "max_by_kind": {"text": 64, "speech": 64, "sfx": 32}, "batch_max_by_kind": {}} | config
        if not isinstance(limits["max_by_kind"], dict) or set(limits["max_by_kind"]) - {"text", "speech", "sfx"}:
            raise CapabilityError("Invalid budget categories")
        if not isinstance(limits["batch_max_by_kind"], dict) or set(limits["batch_max_by_kind"]) - {"text", "speech", "sfx"}:
            raise CapabilityError("Invalid revision budget categories")
        values = [limits["max_calls"], limits["batch_max_calls"]] + list(limits["max_by_kind"].values()) + list(limits["batch_max_by_kind"].values())
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in values):
            raise CapabilityError("Call budgets must be nonnegative integers")
        return limits

    def reserve(self, batch: str, kind: str, config: dict):
        limits = self.limits(config)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            total = db.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
            incremental = db.execute("SELECT COUNT(*) FROM calls WHERE batch=?", (batch,)).fetchone()[0]
            provider = db.execute("SELECT COUNT(*) FROM calls WHERE kind=?", (kind,)).fetchone()[0]
            revision_provider = db.execute("SELECT COUNT(*) FROM calls WHERE batch=? AND kind=?", (batch, kind)).fetchone()[0]
            for scope, actual, maximum in [("production", total, limits["max_calls"]),
                                           ("revision", incremental, limits["batch_max_calls"]),
                                           (kind, provider, limits["max_by_kind"].get(kind, limits["max_calls"])),
                                           (kind + "_revision", revision_provider, limits["batch_max_by_kind"].get(kind, limits["batch_max_calls"]))]:
                if actual >= maximum:
                    exc = BudgetExceeded("External call budget exhausted")
                    exc.diagnostics = {"category": "budget_exhausted", "scope": scope, "used": actual, "limit": maximum}
                    raise exc
            identity = "call_" + uuid.uuid4().hex
            db.execute("INSERT INTO calls VALUES(?,?,?,?)", (identity, batch, kind, time.time()))
            return identity

    def stats(self, batch: str):
        with self.connect() as db:
            kinds = dict(db.execute("SELECT kind,COUNT(*) FROM calls GROUP BY kind"))
            incremental = db.execute("SELECT COUNT(*) FROM calls WHERE batch=?", (batch,)).fetchone()[0]
            batch_kinds = dict(db.execute("SELECT kind,COUNT(*) FROM calls WHERE batch=? GROUP BY kind", (batch,)))
        return {"batch_id": batch, "production_calls": sum(kinds.values()), "batch_calls": incremental,
                "by_kind": kinds, "batch_by_kind": batch_kinds, "basis": "reserved_external_submissions_including_retries"}
