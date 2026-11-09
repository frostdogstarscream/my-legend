ALTER TABLE production_flows ADD COLUMN IF NOT EXISTS auto_title text;
INSERT INTO schema_versions(version) VALUES(5) ON CONFLICT DO NOTHING;
