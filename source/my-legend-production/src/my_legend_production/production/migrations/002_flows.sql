CREATE TABLE IF NOT EXISTS production_flows (
    id text PRIMARY KEY, owner_id text NOT NULL, work_id text NOT NULL,
    parent_id text REFERENCES production_flows(id),
    idempotency_key text NOT NULL, request_hash text NOT NULL,
    request jsonb NOT NULL, casting jsonb NOT NULL,
    status text NOT NULL DEFAULT 'queued', revision integer NOT NULL DEFAULT 1,
    error_code text, cancel_requested boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS production_flow_jobs (
    flow_id text NOT NULL REFERENCES production_flows(id),
    phase text NOT NULL CHECK(phase IN ('sound','stage')),
    job_id text NOT NULL REFERENCES jobs(id),
    PRIMARY KEY(flow_id,phase)
);
CREATE INDEX IF NOT EXISTS flow_jobs_job ON production_flow_jobs(job_id);
CREATE INDEX IF NOT EXISTS flows_owner_created ON production_flows(owner_id,created_at DESC);
INSERT INTO schema_versions(version) VALUES(2) ON CONFLICT DO NOTHING;
