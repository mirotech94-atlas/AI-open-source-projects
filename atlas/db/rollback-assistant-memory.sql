-- Run by hand, after a pg_dump and only after the code that writes `channel` and
-- `private` is reverted, to remove the per-channel chat memory columns.
-- ATLAS never runs this file. Rows from other channels are kept as plain rows.
DROP INDEX IF EXISTS whatsapp_chat_memory_channel_at;
ALTER TABLE whatsapp_chat_memory DROP COLUMN IF EXISTS private;
ALTER TABLE whatsapp_chat_memory DROP COLUMN IF EXISTS channel;
