CREATE TABLE Players (id INT, name VARCHAR(20), team_id INT, points INT);
CREATE TABLE Teams (id INT, name VARCHAR(20));

INSERT INTO Players VALUES (1, 'Amy', 1, 17);
INSERT INTO Players VALUES (2, 'Bob', 2, 24);
INSERT INTO Players VALUES (3, 'Chris', 1, 12);
INSERT INTO Players VALUES (4, 'Drew', 3, 29);
INSERT INTO Teams VALUES (1, 'North');
INSERT INTO Teams VALUES (2, 'South');

SELECT p.name AS player, t.name AS team, p.points
FROM Players p JOIN Teams t ON p.team_id = t.id
WHERE p.points >= 15
ORDER BY p.points DESC;
