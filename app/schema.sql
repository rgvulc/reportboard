-- Global app config
CREATE TABLE board (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    position  INTEGER NOT NULL
);

CREATE TABLE importance_level (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    position  INTEGER NOT NULL
);

-- Workspaces
CREATE TABLE workspace (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    position    INTEGER NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
    updated_at  TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
);

-- Reports (the core unit)
CREATE TABLE report (
    id             INTEGER PRIMARY KEY,
    workspace_id   INTEGER NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    board_id       INTEGER NOT NULL REFERENCES board(id),
    importance_id  INTEGER REFERENCES importance_level(id),
    title          TEXT NOT NULL,
    content        TEXT NOT NULL DEFAULT '',
    position       INTEGER NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
    updated_at     TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
);
CREATE INDEX idx_report_board ON report(workspace_id, board_id, position);

-- Tags (normalized so future search is cheap)
CREATE TABLE tag (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE report_tag (
    report_id  INTEGER NOT NULL REFERENCES report(id) ON DELETE CASCADE,
    tag_id     INTEGER NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
    PRIMARY KEY (report_id, tag_id)
);

-- Flat checklist
CREATE TABLE checklist_item (
    id         INTEGER PRIMARY KEY,
    report_id  INTEGER NOT NULL REFERENCES report(id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    position   INTEGER NOT NULL
);

-- Attachments (any file type)
CREATE TABLE attachment (
    id             INTEGER PRIMARY KEY,
    report_id      INTEGER NOT NULL REFERENCES report(id) ON DELETE CASCADE,
    filename       TEXT NOT NULL,
    original_name  TEXT NOT NULL,
    mime_type      TEXT NOT NULL,
    size_bytes     INTEGER NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
    UNIQUE(report_id, filename)
);
