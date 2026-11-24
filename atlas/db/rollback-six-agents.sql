-- Rollback of the six-agents schema (phases 1-11, feat/local-agency after 7fdf6128).
--
-- Miro runs this, by hand, after a backup (pg_dump). The builder never runs it.
-- It drops only the tables and columns those phases added; nothing that
-- existed before is touched. Revert the phase commits first, so no running
-- code reads these tables:
--   git revert <phase commits>   (Phase 8 also re-registers the `community`
--   roster agent: reverting that commit puts its register(Agent(...)) entry
--   back in atlas/kernel/agents.py; the community job, tiers and posting rules
--   were never changed).
--
--   docker compose exec -T postgres psql -U atlas atlas < db/rollback-six-agents.sql

BEGIN;

-- Research
DROP TABLE IF EXISTS research_client_reports;
DROP TABLE IF EXISTS research_opportunities;
DROP TABLE IF EXISTS research_prices;
DROP TABLE IF EXISTS research_changes;
DROP TABLE IF EXISTS research_watches;
DROP TABLE IF EXISTS competitor_profile_history;
DROP TABLE IF EXISTS competitor_profiles;

-- SEO
DROP TABLE IF EXISTS seo_reports;
DROP TABLE IF EXISTS seo_changes;
DROP TABLE IF EXISTS seo_ranks;
DROP TABLE IF EXISTS seo_keywords;
DROP TABLE IF EXISTS seo_audits;

-- BizOps
DROP TABLE IF EXISTS bizops_automation_ideas;
DROP TABLE IF EXISTS bizops_reminders;
DROP TABLE IF EXISTS bizops_invoices;
DROP TABLE IF EXISTS bizops_onboardings;

-- Content
DROP TABLE IF EXISTS reddit_drafts;
DROP TABLE IF EXISTS academy_comment_drafts;
DROP TABLE IF EXISTS academy_title_tests;
DROP TABLE IF EXISTS academy_content_plan;

-- Sales
DROP TABLE IF EXISTS sales_imports;
DROP TABLE IF EXISTS sales_proposals;
DROP TABLE IF EXISTS sales_research;
ALTER TABLE agency_leads DROP COLUMN IF EXISTS returning_of_lead_id;
ALTER TABLE agency_leads DROP COLUMN IF EXISTS segment;
ALTER TABLE agency_leads DROP COLUMN IF EXISTS source;
ALTER TABLE agency_leads DROP COLUMN IF EXISTS sales_stage;

-- QA gate and the project board
ALTER TABLE agency_sites DROP COLUMN IF EXISTS security_consent_at;
DROP TABLE IF EXISTS qa_results;
DROP TABLE IF EXISTS project_tasks_history;
DROP TABLE IF EXISTS project_tasks;
DROP TABLE IF EXISTS projects;

-- The scraper
DROP TABLE IF EXISTS scrape_pages;
DROP TABLE IF EXISTS scrape_log;

COMMIT;
