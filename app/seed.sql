-- Default board set, in left-to-right column order
INSERT INTO board (name, position) VALUES
    ('To Organize', 0),
    ('In Progress', 1),
    ('Todo', 2),
    ('On Hold', 3),
    ('On Deck', 4),
    ('Backlog', 5),
    ('Abandoned', 6),
    ('Done', 7);

-- Default importance levels
INSERT INTO importance_level (name, position) VALUES
    ('Low', 0),
    ('Medium', 1),
    ('High', 2);
