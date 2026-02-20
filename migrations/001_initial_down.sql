-- Rollback migration 001: Drop all tables.

DROP TABLE IF EXISTS webhook_deliveries;
DROP TABLE IF EXISTS queue_locks;
DROP TABLE IF EXISTS risk_policies;
DROP TABLE IF EXISTS compliance_thresholds;
DROP TABLE IF EXISTS agent_policies;
DROP TABLE IF EXISTS intents;
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS schema_migrations;
