ALTER TABLE jobs ADD COLUMN IF NOT EXISTS error_detail jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE stages ADD COLUMN IF NOT EXISTS error_detail jsonb NOT NULL DEFAULT '{}'::jsonb;
INSERT INTO schema_versions(version) VALUES(7) ON CONFLICT DO NOTHING;
