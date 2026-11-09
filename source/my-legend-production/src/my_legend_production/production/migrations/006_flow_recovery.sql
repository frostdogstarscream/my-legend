ALTER TABLE production_flows ADD COLUMN IF NOT EXISTS recovery_query jsonb;
INSERT INTO schema_versions(version) VALUES(6) ON CONFLICT DO NOTHING;
