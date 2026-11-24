-- Run by hand, after a pg_dump, to remove the home camera watch tables.
-- ATLAS never runs this file. Face tables first (they point at the clusters).
DROP TABLE IF EXISTS camera_faces;
DROP TABLE IF EXISTS camera_clusters;
DROP TABLE IF EXISTS camera_people;
DROP TABLE IF EXISTS camera_events;
DROP TABLE IF EXISTS camera_state;
