-- Rollback of the local-agency upgrades schema (Part 0, 2026-11-12).
--
-- Miro runs this, by hand, after a backup (pg_dump). Code never runs it.
-- Revert the merge commits first so no running code reads these objects.
-- It drops only what Part 0 added; nothing that existed before is touched.
--
--   docker compose exec -T postgres psql -U atlas atlas < db/rollback-local-agency-upgrades.sql

DROP TABLE IF EXISTS reply_timings;

DROP INDEX IF EXISTS memories_live_ns_kind;
DROP INDEX IF EXISTS memories_content_trgm;
ALTER TABLE memories DROP COLUMN IF EXISTS source;
ALTER TABLE memories DROP COLUMN IF EXISTS deleted_at;
ALTER TABLE memories DROP COLUMN IF EXISTS business;

ALTER TABLE project_tasks DROP COLUMN IF EXISTS follow_up_at;
ALTER TABLE projects DROP COLUMN IF EXISTS business;
