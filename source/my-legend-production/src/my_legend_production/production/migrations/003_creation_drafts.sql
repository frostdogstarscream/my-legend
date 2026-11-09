CREATE TABLE IF NOT EXISTS creation_drafts (
 id text PRIMARY KEY, owner_id text NOT NULL, work_id text NOT NULL,
 idempotency_key text NOT NULL, request_hash text NOT NULL, payload jsonb NOT NULL,
 revision integer NOT NULL DEFAULT 1, submitted_revision integer,
 flow_id text REFERENCES production_flows(id), legacy_story_id text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,idempotency_key), UNIQUE(owner_id,legacy_story_id)
);
CREATE INDEX IF NOT EXISTS creation_drafts_owner ON creation_drafts(owner_id,updated_at DESC);
INSERT INTO schema_versions(version) VALUES(3) ON CONFLICT DO NOTHING;
