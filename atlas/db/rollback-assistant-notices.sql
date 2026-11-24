-- Run by hand, after a pg_dump and only after the code that writes these tables
-- (atlas/modules/assistant/notices.py) is reverted. ATLAS never runs this file.
DROP TABLE IF EXISTS assistant_notice_state;
DROP INDEX IF EXISTS assistant_notices_sent;
DROP INDEX IF EXISTS assistant_notices_dedupe;
DROP TABLE IF EXISTS assistant_notices;
