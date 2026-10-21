CREATE TABLE IF NOT EXISTS schema_versions (
    version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS jobs (
    id text PRIMARY KEY, owner_id text NOT NULL, work_id text NOT NULL,
    idempotency_key text NOT NULL, request_hash text NOT NULL, request jsonb NOT NULL,
    status text NOT NULL CHECK(status IN ('queued','running','needs_input','succeeded','failed','cancelling','cancelled')),
    revision integer NOT NULL DEFAULT 1, source_version_id text,
    reserved_calls integer NOT NULL DEFAULT 0, actual_calls integer NOT NULL DEFAULT 0,
    deadline timestamptz NOT NULL, cancel_requested boolean NOT NULL DEFAULT false,
    error_code text, resolution jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS stages (
    job_id text NOT NULL REFERENCES jobs(id), name text NOT NULL,
    dependencies jsonb NOT NULL, status text NOT NULL DEFAULT 'queued',
    checkpoint jsonb NOT NULL DEFAULT '{}', attempts integer NOT NULL DEFAULT 0,
    lease_token text, lease_owner text, lease_until timestamptz,
    available_at timestamptz NOT NULL DEFAULT now(), error_code text,
    technical_quality text NOT NULL DEFAULT 'not_checked', human_review text NOT NULL DEFAULT 'not_required',
    PRIMARY KEY(job_id,name)
);
CREATE INDEX IF NOT EXISTS stages_claim_idx ON stages(status,available_at,lease_until);
CREATE TABLE IF NOT EXISTS artifacts (
    job_id text NOT NULL REFERENCES jobs(id), stage_name text NOT NULL,
    kind text NOT NULL, sha256 text NOT NULL, source_version_id text NOT NULL,
    metadata jsonb NOT NULL, PRIMARY KEY(job_id,stage_name,kind)
);
CREATE TABLE IF NOT EXISTS calls (
    job_id text NOT NULL REFERENCES jobs(id), call_key text NOT NULL, stage_name text NOT NULL,
    input_hash text NOT NULL, state text NOT NULL CHECK(state IN ('pending','completed','unknown')),
    reserved_units integer NOT NULL CHECK(reserved_units>=0), actual_units integer NOT NULL DEFAULT 0,
    result jsonb, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(job_id,call_key)
);
CREATE TABLE IF NOT EXISTS events (
    id bigserial PRIMARY KEY, job_id text NOT NULL REFERENCES jobs(id),
    event_type text NOT NULL, details jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO schema_versions(version) VALUES(1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS visual_appearances (
    owner_id text NOT NULL, work_id text NOT NULL, character_id text NOT NULL,
    appearance jsonb NOT NULL, source_version_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(owner_id,work_id,character_id)
);
