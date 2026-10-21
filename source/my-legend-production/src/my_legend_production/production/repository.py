"""PostgreSQL queue with fenced leases, atomic budgets and immutable source bindings."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .contracts import DeliverySource, ProductionRequest, StageResult, REQUIRED_ARTIFACTS, fingerprint, stage_graph


class ProductionError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ProductionRepository:
    def __init__(self, dsn: str, schema="my_legend_production"):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            raise ValueError("INVALID_DATABASE_SCHEMA")
        self.dsn, self.schema = dsn, schema

    @contextmanager
    def transaction(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as db:
            db.execute(sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(sql.Identifier(self.schema)))
            yield db

    def migrate(self):
        with self.transaction() as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("legend-production-migrate:" + self.schema,))
            db.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
            db.execute((Path(__file__).parent / "migrations/001.sql").read_text(encoding="utf-8"))

    @staticmethod
    def event(db, job_id, kind, details):
        db.execute("INSERT INTO events(job_id,event_type,details) VALUES(%s,%s,%s)", (job_id, kind, Jsonb(details)))

    def create(self, owner_id: str, request: ProductionRequest):
        digest = fingerprint(request)
        with self.transaction() as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (owner_id + ":" + request.idempotency_key,))
            existing = db.execute("SELECT id,request_hash FROM jobs WHERE owner_id=%s AND idempotency_key=%s",
                                  (owner_id, request.idempotency_key)).fetchone()
            if existing:
                if existing["request_hash"] != digest:
                    raise ProductionError("IDEMPOTENCY_CONFLICT")
                return existing["id"]
            if isinstance(request.source, DeliverySource):
                source_refs = [request.source.delivery, request.source.manifest]
                if request.audio_revision:
                    source_refs.append(request.audio_revision.cache)
                    source_refs.extend(ref for ref in (request.audio_revision.arrangement, request.audio_revision.review) if ref is not None)
                for ref in source_refs:
                    accessible = db.execute("""SELECT 1 FROM artifacts a JOIN jobs j ON j.id=a.job_id
                        WHERE j.owner_id=%s AND a.kind=%s AND a.sha256=%s AND a.source_version_id=%s LIMIT 1""",
                        (owner_id, ref.kind, ref.sha256, ref.source_version_id)).fetchone()
                    if not accessible:
                        raise ProductionError("SOURCE_ARTIFACT_NOT_OWNED")
            job_id = str(uuid4())
            db.execute("""INSERT INTO jobs(id,owner_id,work_id,idempotency_key,request_hash,request,status,deadline)
                VALUES(%s,%s,%s,%s,%s,%s,'queued',now()+%s*interval '1 second')""",
                (job_id, owner_id, request.work_id or str(uuid4()), request.idempotency_key, digest,
                 Jsonb(request.model_dump(mode="json")), request.budget.max_elapsed_seconds))
            for name, deps in stage_graph(request).items():
                db.execute("INSERT INTO stages(job_id,name,dependencies) VALUES(%s,%s,%s)", (job_id, name, Jsonb(deps)))
            self.event(db, job_id, "created", {"request_hash": digest})
            return job_id

    def get(self, job_id: str, owner_id: str):
        with self.transaction() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=%s AND owner_id=%s", (job_id, owner_id)).fetchone()
            if not job:
                raise ProductionError("JOB_NOT_FOUND")
            job["stages"] = db.execute("""SELECT name,dependencies,status,attempts,error_code,technical_quality,human_review,checkpoint
                FROM stages WHERE job_id=%s ORDER BY name""", (job_id,)).fetchall()
            for stage in job["stages"]:
                checkpoint = stage.pop("checkpoint")
                if stage["status"] == "needs_input" and checkpoint.get("phase") == "plan_ready":
                    plan = checkpoint["plan"]
                    stage["input_request"] = {"plan_id": plan["plan_id"], "revision": plan["plan_revision"],
                                              "fingerprint": plan["plan_fingerprint"], "outline": plan["plan"]}
            job["unsettled_calls"] = db.execute("SELECT count(*) AS n FROM calls WHERE job_id=%s AND state<>'completed'", (job_id,)).fetchone()["n"]
            job["unknown_calls"] = db.execute("SELECT count(*) AS n FROM calls WHERE job_id=%s AND state='unknown'", (job_id,)).fetchone()["n"]
            job["artifacts"] = [r["metadata"] for r in db.execute("SELECT metadata FROM artifacts WHERE job_id=%s ORDER BY stage_name,kind", (job_id,))]
            job["publication"] = "not_requested"
            return job

    def list_jobs(self, owner_id, *, limit=30, offset=0, work_id=None):
        with self.transaction() as db:
            rows = db.execute("""SELECT id FROM jobs WHERE owner_id=%s AND (%s::text IS NULL OR work_id=%s)
                ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s""", (owner_id, work_id, work_id, limit + 1, offset)).fetchall()
        return {"items": [self.get(row["id"], owner_id) for row in rows[:limit]],
                "next_offset": offset + limit if len(rows) > limit else None}

    def execution_evidence(self, job_id, owner_id):
        job = self.get(job_id, owner_id)
        with self.transaction() as db:
            calls = db.execute("""SELECT call_key,stage_name,input_hash,state,reserved_units,actual_units
                FROM calls WHERE job_id=%s ORDER BY call_key""", (job_id,)).fetchall()
            events = db.execute("SELECT event_type,details FROM events WHERE job_id=%s ORDER BY id", (job_id,)).fetchall()
        story_repairs = sum(e["event_type"] == "story_model_intent" and e["details"].get("repair") is True for e in events)
        # A repair may redo the plan or only its malformed review; count each
        # bounded attempt once even when it contains both model calls.
        visual_repairs = len({c['call_key'].rsplit(':',1)[-1] for c in calls
            if c['call_key'].startswith(('visual_plan:plan:','visual_plan:review:'))
            and c['call_key'].rsplit(':',1)[-1] in ('1','2')})
        sound_repairs = len({('classification' if ':classify:' in c['call_key'] else 'plan', c['call_key'].rsplit(':',1)[-1]) for c in calls
            if c['call_key'].startswith(('sound_plan:classify:','sound_plan:classify-repair:','sound_plan:simple-plan:','sound_plan:simple-review:'))
            and c['call_key'].rsplit(':',1)[-1] in ('1','2')})
        return {"schema_version": "production_execution_evidence/1.0", "job_id": job_id,
                "source_version_id": job["source_version_id"], "request_hash": job["request_hash"],
                "provider_calls": job["actual_calls"], "reserved_calls": job["reserved_calls"],
                "unknown_calls": job["unknown_calls"], "policy_repairs": story_repairs + visual_repairs + sound_repairs,
                "resume_events": sum(e["event_type"] == "resumed" for e in events),
                "reconciliation_events": sum(e["event_type"] == "call_reconciled" for e in events),
                "cancel_events": sum(e["event_type"] == "cancel_requested" for e in events), "calls": calls,
                "session_interventions": None, "manual_artifact_edits": None,
                "out_of_band_context_injections": None, "evidence_basis": "server_observations_only",
                "human_content_acceptance": "pending"}

    def sweep(self, db):
        # Expired jobs are terminal, even when no client polls them.
        expired = db.execute("""UPDATE jobs SET status=CASE WHEN cancel_requested THEN 'cancelled' ELSE 'failed' END,
            error_code=CASE WHEN cancel_requested THEN NULL ELSE 'DEADLINE_EXCEEDED' END, updated_at=now()
            WHERE status IN ('queued','running','cancelling') AND deadline<=now() RETURNING id,status""").fetchall()
        for row in expired:
            db.execute("""UPDATE stages SET status=%s,lease_token=NULL,lease_until=NULL,error_code='DEADLINE_EXCEEDED'
                WHERE job_id=%s AND status IN ('queued','running')""", (row["status"], row["id"]))
            self.event(db, row["id"], "deadline", {"status": row["status"]})
        # Cancelled workers which died cannot leave a job in cancelling forever.
        db.execute("""UPDATE stages s SET status='cancelled',lease_token=NULL,lease_until=NULL FROM jobs j
            WHERE s.job_id=j.id AND j.cancel_requested AND s.status IN ('queued','running')
            AND (s.lease_until IS NULL OR s.lease_until<now())""")
        db.execute("""UPDATE jobs j SET status='cancelled',updated_at=now() WHERE status='cancelling'
            AND NOT EXISTS(SELECT 1 FROM stages s WHERE s.job_id=j.id AND s.status='running')""")

    def claim(self, worker_id: str, lease_seconds=30):
        if lease_seconds < 1:
            raise ValueError("INVALID_LEASE")
        with self.transaction() as db:
            self.sweep(db)
            job = db.execute("""SELECT j.* FROM jobs j
                WHERE j.status IN ('queued','running') AND NOT j.cancel_requested AND j.deadline>now()
                AND EXISTS(SELECT 1 FROM stages s WHERE s.job_id=j.id
                    AND s.status IN ('queued','running') AND s.available_at<=now()
                    AND (s.lease_until IS NULL OR s.lease_until<now())
                    AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(s.dependencies) d(name)
                        LEFT JOIN stages p ON p.job_id=s.job_id AND p.name=d.name WHERE p.status IS DISTINCT FROM 'succeeded'))
                ORDER BY j.created_at FOR UPDATE OF j SKIP LOCKED LIMIT 1""").fetchone()
            if not job:
                return None
            stage = db.execute("""SELECT s.* FROM stages s WHERE s.job_id=%s
                AND s.status IN ('queued','running') AND s.available_at<=now()
                AND (s.lease_until IS NULL OR s.lease_until<now())
                AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(s.dependencies) d(name)
                    LEFT JOIN stages p ON p.job_id=s.job_id AND p.name=d.name WHERE p.status IS DISTINCT FROM 'succeeded')
                ORDER BY s.name FOR UPDATE LIMIT 1""", (job["id"],)).fetchone()
            stage.update({k: job[k] for k in ("request", "request_hash", "owner_id", "work_id", "source_version_id", "resolution", "deadline")})
            token = str(uuid4())
            db.execute("""UPDATE stages SET status='running',lease_token=%s,lease_owner=%s,
                lease_until=now()+%s*interval '1 second',attempts=attempts+1 WHERE job_id=%s AND name=%s""",
                (token, worker_id, lease_seconds, stage["job_id"], stage["name"]))
            db.execute("UPDATE jobs SET status='running',updated_at=now() WHERE id=%s", (stage["job_id"],))
            stage["lease_token"] = token
            stage["artifacts"] = [r["metadata"] for r in db.execute("SELECT metadata FROM artifacts WHERE job_id=%s", (stage["job_id"],))]
            self.event(db, stage["job_id"], "claimed", {"stage": stage["name"], "worker": worker_id})
            return stage

    def heartbeat(self, stage, lease_seconds=30):
        with self.transaction() as db:
            row = db.execute("""UPDATE stages SET lease_until=now()+%s*interval '1 second'
                WHERE job_id=%s AND name=%s AND lease_token=%s AND lease_until>now() AND status='running' RETURNING job_id""",
                (lease_seconds, stage["job_id"], stage["name"], stage["lease_token"])).fetchone()
            return bool(row)

    def check_active(self, stage):
        """Read-only execution guard used while a contained child is running."""
        with self.transaction() as db:
            row = db.execute("""SELECT j.cancel_requested, j.deadline<=now() AS expired,
                s.status, s.lease_token, s.lease_until>now() AS leased
                FROM jobs j JOIN stages s ON s.job_id=j.id WHERE j.id=%s AND s.name=%s""",
                (stage["job_id"], stage["name"])).fetchone()
        if not row:
            raise ProductionError("LEASE_LOST")
        if row["cancel_requested"]:
            raise ProductionError("CANCEL_REQUESTED")
        if row["expired"]:
            raise ProductionError("DEADLINE_EXCEEDED")
        if row["status"] != "running" or row["lease_token"] != stage["lease_token"] or not row["leased"]:
            raise ProductionError("LEASE_LOST")

    @staticmethod
    def fence(db, stage):
        row = db.execute("""SELECT * FROM stages WHERE job_id=%s AND name=%s AND lease_token=%s
            AND lease_until>now() AND status='running' FOR UPDATE""",
            (stage["job_id"], stage["name"], stage["lease_token"])).fetchone()
        if not row:
            raise ProductionError("LEASE_LOST")
        return row

    def finish(self, stage, result: StageResult):
        with self.transaction() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=%s FOR UPDATE", (stage["job_id"],)).fetchone()
            self.fence(db, stage)
            version = result.source_version_id or job["source_version_id"]
            if job["source_version_id"] and version != job["source_version_id"]:
                raise ProductionError("SOURCE_VERSION_MISMATCH")
            if any(a.source_version_id != version for a in result.artifacts):
                raise ProductionError("SOURCE_VERSION_MISMATCH")
            if result.status == "succeeded" and not REQUIRED_ARTIFACTS[stage["name"]] <= {a.kind for a in result.artifacts}:
                raise ProductionError("REQUIRED_ARTIFACT_MISSING")
            for artifact in result.artifacts:
                existing = db.execute("SELECT sha256 FROM artifacts WHERE job_id=%s AND stage_name=%s AND kind=%s",
                                      (stage["job_id"], stage["name"], artifact.kind)).fetchone()
                if existing and existing["sha256"] != artifact.sha256:
                    raise ProductionError("ARTIFACT_CHANGED")
                db.execute("""INSERT INTO artifacts VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                           (stage["job_id"], stage["name"], artifact.kind, artifact.sha256, version, Jsonb(artifact.model_dump())))
            status = "cancelled" if job["cancel_requested"] else result.status
            db.execute("""UPDATE stages SET status=%s,checkpoint=%s,error_code=%s,technical_quality=%s,human_review=%s,
                lease_token=NULL,lease_owner=NULL,lease_until=NULL,available_at=now()+%s*interval '1 second'
                WHERE job_id=%s AND name=%s""", (status, Jsonb(result.checkpoint), result.error_code, result.technical_quality,
                result.human_review, result.poll_after_seconds, stage["job_id"], stage["name"]))
            states = [r["status"] for r in db.execute("SELECT status FROM stages WHERE job_id=%s", (stage["job_id"],))]
            runnable = db.execute("""SELECT 1 FROM stages s WHERE s.job_id=%s
                AND s.status IN ('queued','running')
                AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(s.dependencies) d(name)
                    LEFT JOIN stages p ON p.job_id=s.job_id AND p.name=d.name WHERE p.status IS DISTINCT FROM 'succeeded')
                LIMIT 1""", (stage["job_id"],)).fetchone()
            if job["cancel_requested"]:
                overall = "cancelling" if "running" in states else "cancelled"
            elif runnable:
                # A failed branch must not stop independent outputs that can still finish.
                overall = "running"
            elif "failed" in states:
                overall = "failed"
            elif "needs_input" in states:
                overall = "needs_input"
            else:
                overall = "succeeded" if all(s == "succeeded" for s in states) else "running"
            error = result.error_code
            if overall in ("failed", "needs_input"):
                failed = db.execute("SELECT error_code FROM stages WHERE job_id=%s AND status=%s ORDER BY name LIMIT 1",
                                    (stage["job_id"], overall)).fetchone()
                error = failed["error_code"] if failed else error
            db.execute("UPDATE jobs SET status=%s,source_version_id=%s,error_code=%s,updated_at=now() WHERE id=%s",
                       (overall, version, error, stage["job_id"]))
            self.event(db, stage["job_id"], "stage_result", {"stage": stage["name"], "status": status, "error_code": result.error_code})

    def cancel(self, job_id, owner_id):
        with self.transaction() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=%s AND owner_id=%s FOR UPDATE", (job_id, owner_id)).fetchone()
            if not job:
                raise ProductionError("JOB_NOT_FOUND")
            if job["status"] in ("succeeded", "cancelled"):
                return
            db.execute("UPDATE jobs SET cancel_requested=true,status='cancelling',updated_at=now() WHERE id=%s", (job_id,))
            db.execute("UPDATE stages SET status='cancelled' WHERE job_id=%s AND status='queued'", (job_id,))
            self.sweep(db)
            self.event(db, job_id, "cancel_requested", {})

    def resume(self, job_id, owner_id, expected_revision, resolution):
        with self.transaction() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=%s AND owner_id=%s FOR UPDATE", (job_id, owner_id)).fetchone()
            if not job:
                raise ProductionError("JOB_NOT_FOUND")
            if job["revision"] != expected_revision or job["status"] not in ("failed", "needs_input"):
                raise ProductionError("RESUME_CONFLICT")
            if db.execute("SELECT 1 FROM calls WHERE job_id=%s AND state='unknown' LIMIT 1", (job_id,)).fetchone():
                raise ProductionError("EXTERNAL_RESULT_UNKNOWN")
            if db.execute("""SELECT 1 FROM calls WHERE job_id=%s AND call_key='audio:generation-budget'
                    AND state='completed' AND result->>'generation_failed'='true' LIMIT 1""", (job_id,)).fetchone():
                raise ProductionError('AUDIO_NEW_REVISION_REQUIRED')
            # Only a proven zero-submission audio preflight failure can reuse its
            # reservation. Preserve this transition in events; never reopen paid or
            # ambiguous results and never increase the reservation on resume.
            rearmed = db.execute("""UPDATE calls c SET state='pending',result=NULL,updated_at=now()
                FROM stages s WHERE c.job_id=%s AND s.job_id=c.job_id AND s.name=c.stage_name
                AND s.status='failed' AND c.stage_name='audio' AND c.call_key='audio:generation-budget'
                AND c.state='completed' AND c.actual_units=0 AND c.result=%s RETURNING c.call_key""",
                (job_id, Jsonb({'provider_calls': 0, 'preflight_failed': True}))).fetchall()
            for call in rearmed:
                self.event(db, job_id, 'zero_call_rearmed', {'call_key': call['call_key'], 'previous_revision': expected_revision})
            db.execute("""UPDATE jobs SET status='queued',error_code=NULL,revision=revision+1,resolution=%s,
                deadline=now()+%s*interval '1 second',updated_at=now() WHERE id=%s""",
                (Jsonb(resolution), job["request"]["budget"]["max_elapsed_seconds"], job_id))
            db.execute("""UPDATE stages SET status='queued',error_code=NULL,lease_token=NULL,lease_until=NULL,
                available_at=now() WHERE job_id=%s AND status IN ('failed','needs_input')""", (job_id,))
            self.event(db, job_id, "resumed", {"revision": expected_revision + 1})

    def reserve_call(self, stage, call_key, input_hash, units):
        if units < 0:
            raise ProductionError("INVALID_CALL_RESERVATION")
        with self.transaction() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=%s FOR UPDATE", (stage["job_id"],)).fetchone()
            self.fence(db, stage)
            if job["cancel_requested"]:
                raise ProductionError("CANCEL_REQUESTED")
            existing = db.execute("SELECT * FROM calls WHERE job_id=%s AND call_key=%s", (stage["job_id"], call_key)).fetchone()
            if existing:
                if existing["input_hash"] != input_hash or existing["reserved_units"] != units:
                    raise ProductionError("CALL_INPUT_CHANGED")
                return existing
            if job["reserved_calls"] + units > job["request"]["budget"]["max_provider_calls"]:
                raise ProductionError("CALL_BUDGET_EXCEEDED")
            db.execute("UPDATE jobs SET reserved_calls=reserved_calls+%s WHERE id=%s", (units, stage["job_id"]))
            return db.execute("""INSERT INTO calls(job_id,call_key,stage_name,input_hash,state,reserved_units)
                VALUES(%s,%s,%s,%s,'pending',%s) RETURNING *""",
                (stage["job_id"], call_key, stage["name"], input_hash, units)).fetchone() | {"new": True}

    def complete_call(self, stage, call_key, result, actual_units=0):
        # A provider response survives worker lease loss; it is still useful on reconciliation.
        with self.transaction() as db:
            db.execute("SELECT id FROM jobs WHERE id=%s FOR UPDATE", (stage["job_id"],))
            row = db.execute("SELECT * FROM calls WHERE job_id=%s AND call_key=%s FOR UPDATE", (stage["job_id"], call_key)).fetchone()
            if not row or actual_units < row["actual_units"] or actual_units > row["reserved_units"]:
                raise ProductionError("CALL_ACCOUNTING_INVALID")
            if row["state"] == "completed":
                if row["result"] != result or row["actual_units"] != actual_units:
                    raise ProductionError("CALL_RESULT_CHANGED")
                return
            db.execute("UPDATE calls SET state='completed',result=%s,actual_units=%s,updated_at=now() WHERE job_id=%s AND call_key=%s",
                       (Jsonb(result), actual_units, stage["job_id"], call_key))
            db.execute("UPDATE jobs SET actual_calls=actual_calls+%s WHERE id=%s", (actual_units - row["actual_units"], stage["job_id"]))

    def observe_call(self, stage, call_key, actual_units, *, unknown=False):
        """Persist cumulative known usage even if generation fails or its result is unknown."""
        with self.transaction() as db:
            db.execute("SELECT id FROM jobs WHERE id=%s FOR UPDATE", (stage["job_id"],))
            row = db.execute("SELECT * FROM calls WHERE job_id=%s AND call_key=%s FOR UPDATE",
                             (stage["job_id"], call_key)).fetchone()
            if not row or not isinstance(actual_units, int) or isinstance(actual_units, bool) or not 0 <= actual_units <= row["reserved_units"]:
                raise ProductionError("CALL_ACCOUNTING_INVALID")
            # Delayed observations cannot subtract newer usage or reopen a settled call.
            if row["state"] == "completed":
                if actual_units > row["actual_units"]:
                    raise ProductionError("CALL_RESULT_CHANGED")
                return
            cumulative = max(actual_units, row["actual_units"])
            db.execute("UPDATE calls SET actual_units=%s,state=%s,updated_at=now() WHERE job_id=%s AND call_key=%s",
                       (cumulative, "unknown" if unknown else row["state"], stage["job_id"], call_key))
            db.execute("UPDATE jobs SET actual_calls=actual_calls+%s WHERE id=%s",
                       (cumulative - row["actual_units"], stage["job_id"]))

    def unknown_call(self, stage, call_key):
        with self.transaction() as db:
            db.execute("UPDATE calls SET state='unknown',updated_at=now() WHERE job_id=%s AND call_key=%s AND state='pending'",
                       (stage["job_id"], call_key))

    def reconciliation_snapshot(self, job_id, owner_id, revision):
        with self.transaction() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=%s AND owner_id=%s FOR UPDATE", (job_id, owner_id)).fetchone()
            if not job:
                raise ProductionError("JOB_NOT_FOUND")
            if job["revision"] != revision or job["status"] not in ("failed", "needs_input", "cancelled"):
                raise ProductionError("RECONCILIATION_CONFLICT")
            if db.execute("SELECT 1 FROM stages WHERE job_id=%s AND status='running'", (job_id,)).fetchone():
                raise ProductionError("RECONCILIATION_CONFLICT")
            calls = db.execute("""SELECT c.*,s.checkpoint FROM calls c JOIN stages s ON s.job_id=c.job_id AND s.name=c.stage_name
                WHERE c.job_id=%s AND c.state<>'completed' ORDER BY c.call_key""", (job_id,)).fetchall()
            return calls

    def settle_reconciliation(self, job_id, owner_id, revision, call, result, units, evidence):
        with self.transaction() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=%s AND owner_id=%s FOR UPDATE", (job_id, owner_id)).fetchone()
            if not job:
                raise ProductionError("JOB_NOT_FOUND")
            if job["revision"] != revision or job["status"] not in ("failed", "needs_input", "cancelled"):
                raise ProductionError("RECONCILIATION_CONFLICT")
            row = db.execute("SELECT * FROM calls WHERE job_id=%s AND call_key=%s FOR UPDATE", (job_id, call["call_key"])).fetchone()
            if not row or row["input_hash"] != call["input_hash"] or isinstance(units, bool) or not isinstance(units, int) or not row["actual_units"] <= units <= row["reserved_units"]:
                raise ProductionError("RECONCILIATION_EVIDENCE_INVALID")
            if row["state"] == "completed":
                if row["result"] != result or row["actual_units"] != units:
                    raise ProductionError("CALL_RESULT_CHANGED")
                return
            db.execute("UPDATE calls SET state='completed',result=%s,actual_units=%s,updated_at=now() WHERE job_id=%s AND call_key=%s",
                       (Jsonb(result), units, job_id, call["call_key"]))
            db.execute("UPDATE jobs SET actual_calls=actual_calls+%s WHERE id=%s", (units-row["actual_units"], job_id))
            self.event(db, job_id, "call_reconciled", {"call_key": call["call_key"], "actual_units": units, **evidence})

    def visual_appearances(self, stage, delivery):
        result = {}
        with self.transaction() as db:
            for character in delivery["character_snapshot"]:
                row = db.execute("SELECT appearance FROM visual_appearances WHERE owner_id=%s AND work_id=%s AND character_id=%s",
                    (stage["owner_id"], stage["work_id"], character["character_id"])).fetchone()
                if row:
                    result[character["role_id"]] = {**row["appearance"], "role_id": character["role_id"]}
        return result

    def bind_visual_appearances(self, stage, delivery, context):
        characters = {c["role_id"]: c["character_id"] for c in delivery["character_snapshot"]}
        with self.transaction() as db:
            db.execute("SELECT id FROM jobs WHERE id=%s FOR UPDATE", (stage["job_id"],))
            self.fence(db, stage)
            db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("visual:" + stage["owner_id"] + ":" + stage["work_id"],))
            for binding in context.appearances:
                identity = characters[binding.role_id]
                appearance = binding.model_dump(exclude={"role_id"})
                row = db.execute("SELECT appearance FROM visual_appearances WHERE owner_id=%s AND work_id=%s AND character_id=%s",
                                 (stage["owner_id"], stage["work_id"], identity)).fetchone()
                if row and row["appearance"] != appearance:
                    raise ProductionError("VISUAL_LOCKED_APPEARANCE_CHANGED")
                db.execute("""INSERT INTO visual_appearances(owner_id,work_id,character_id,appearance,source_version_id)
                    VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (stage["owner_id"], stage["work_id"], identity, Jsonb(appearance), delivery["story_version_id"]))
