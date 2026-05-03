# Reportboard

A local-only, single-user, kanban-style report board. Workspaces hold reports
arranged in kanban columns; each report has markdown content, attachments, a
checklist, tags, and an importance level. Drag-and-drop reorders cards,
columns, workspaces, checklist items, board configurations, and importance
levels.

## Requirements

- Python 3.12+
- The `python3-venv` package (Debian/Ubuntu: `sudo apt install python3.12-venv`)

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install flask markdown pytest
.venv/bin/flask --app app init-db
```

`init-db` is destructive: it removes any existing `data/reportboard.db` and
re-creates the schema with the default boards (To Organize, In Progress, Todo,
On Hold, On Deck, Backlog, Abandoned, Done) and importance levels (Low,
Medium, High).

## Running

```bash
.venv/bin/python run.py
```

Then open http://127.0.0.1:5000.

## Testing

```bash
.venv/bin/python -m pytest
```

The test suite uses an isolated temp database and attachments directory per
test, so it never touches `data/`.

## Project layout

```
app/
  __init__.py        # Flask app factory + blueprint registration
  db.py              # SQLite connection helpers and the init-db CLI
  schema.sql         # CREATE TABLE statements
  seed.sql           # Default boards and importance levels
  attachments.py     # File storage + cleanup helpers (pure + IO functions)
  routes/            # Blueprints
    workspaces.py
    reports.py
    attachments.py
    checklist.py
    settings.py
    tags.py
  templates/         # Jinja templates
  static/
    css/app.css      # All styles, light + dark via prefers-color-scheme
    js/app.js        # SortableJS wiring (kanban, workspaces, settings, checklist)
    vendor/          # htmx, easymde, sortablejs (vendored, no CDN)
data/
  reportboard.db     # SQLite database (gitignored)
  attachments/<report_id>/   # per-report attachment storage (gitignored)
tests/               # pytest suite, one file per phase
run.py               # Dev-server entry point
pyproject.toml       # Package metadata
```

## Reset the database

```bash
rm -rf data/reportboard.db data/attachments
.venv/bin/flask --app app init-db
```

## Notes

- All third-party JS/CSS is vendored under `app/static/vendor/`. The app
  works offline.
- Attachments live on disk under `data/attachments/<report_id>/<uuid>.<ext>`
  and are reference-counted by substring scan against the report's saved
  markdown. Saving a report deletes any attachment whose
  `<report_id>/<filename>` substring no longer appears in its content.
- Boards and importance levels are global (shared across workspaces) and
  configurable from the Settings page. A board or importance level cannot be
  deleted while reports reference it; reassign first.
- Search across reports is intentionally out of scope for v1. The schema
  (normalized tags, plain-text content, fixed-set importance) is friendly to
  adding FTS5 search later without migrations.
