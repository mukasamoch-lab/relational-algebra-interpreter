-- Run after loading sql_pipeline_demo.sql into the same database.
SET frames = 4;
ANALYZE Players;
ANALYZE Teams;

EXPLAIN
SELECT p.name AS player, t.name AS team, p.points
FROM Players p JOIN Teams t ON p.team_id = t.id
WHERE p.points >= 15 ORDER BY p.points DESC;

EXPLAIN ANALYZE
SELECT p.name AS player, t.name AS team, p.points
FROM Players p JOIN Teams t ON p.team_id = t.id
WHERE p.points >= 15 ORDER BY p.points DESC;
