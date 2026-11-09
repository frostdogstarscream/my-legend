CREATE TABLE IF NOT EXISTS work_archives (
 owner_id text NOT NULL, work_id text NOT NULL, reason text NOT NULL,
 archived_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,work_id)
);
INSERT INTO schema_versions(version) VALUES(4) ON CONFLICT DO NOTHING;
