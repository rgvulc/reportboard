-- Default board set, in left-to-right column order
INSERT INTO board (name, position) VALUES
    ('Todo', 0),
    ('In Progress', 1),
    ('Complete', 2);
    

-- Default importance levels
INSERT INTO importance_level (name, position) VALUES
    ('Low', 0),
    ('Medium', 1),
    ('High', 2),
    ('Backlog', 3),
    ('Abandoned', 4);
