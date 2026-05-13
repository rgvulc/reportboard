# Reportboard

A local-only, single-user, kanban-style report board. Workspaces hold reports
arranged in kanban columns; each report has rich-text content (edited in Quill),
attachments, a checklist, tags, and an importance level. Drag-and-drop reorders
cards, columns, workspaces, checklist items, board configurations, and
importance levels.

## Requirements

- Python 3.12+
- The `python3-venv` package (Debian/Ubuntu: `sudo apt install python3.12-venv`)

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install flask markdown pyyaml pytest
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

Then open http://127.0.0.1:5000. Can also run 

```bash
.venv/bin/flask --app app run --host=0.0.0.0 --port=5000 --debug
```

to make available outside the VM.


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
  exporter.py        # DB → folder/zip of markdown files
  importer.py        # Zip → DB (replace mode, validates first)
  cli.py             # export-backup / import-backup commands
  routes/            # Blueprints
    workspaces.py
    reports.py
    attachments.py
    checklist.py
    settings.py
    tags.py
    backup.py
  templates/         # Jinja templates
  static/
    css/app.css      # All styles, light + dark via prefers-color-scheme
    js/app.js        # SortableJS wiring (kanban, workspaces, settings, checklist)
    vendor/          # htmx, quill, sortablejs (vendored, no CDN)
  delta_md.py        # Delta ↔ Markdown converter (server-side, lossless on safe subset)
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

## Backup and restore

The Settings page has a Backup section with a download button and an
upload form. Equivalent CLI commands:

```bash
.venv/bin/flask --app app export-backup path/to/backup.zip
.venv/bin/flask --app app import-backup path/to/backup.zip
```

Import is destructive: it replaces every workspace, report, board,
importance level, tag, checklist item, and attachment with the contents
of the archive. The importer validates the entire archive before touching
the live database, so a malformed zip leaves existing data untouched.

The archive is a zip of markdown files: one `report.md` per report (with
YAML frontmatter for tags, checklist, and attachment metadata), grouped
under `workspaces/<ws>/<board>/<report>/`, with attachment binaries under
each report's `attachments/` subdirectory. A top-level `manifest.json`
records boards, importance levels, tags, and workspace order. Attachment
URLs in the body are stored relative to the report directory and rewritten
back to absolute form on import.

## Notes

- All third-party JS/CSS is vendored under `app/static/vendor/`. The app
  works offline. Vendored versions:

```bash
echo "htmx:" 
curl -fsSL https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js | sha256sum
cat app/static/vendor/htmx.min.js | sha256sum
echo

echo "sortable:"
curl -fsSL https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/Sortable.min.js | sha256sum
cat app/static/vendor/sortable.min.js | sha256sum
echo

echo "quill:"
curl -fsSL https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.js | sha256sum
curl -fsSL https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.snow.css | sha256sum
cat app/static/vendor/quill/quill.js | sha256sum
cat app/static/vendor/quill/quill.snow.css | sha256sum
echo

```

- Each report stores its body as **Quill Delta JSON only** in
  `report.content_delta`. Markdown (for export) and HTML (for any future
  read-only view) are derived on demand via `app/delta_md.py`. The editor
  toolbar is restricted to a markdown-clean subset (bold/italic/strike/code/
  link, headers, lists, blockquote, fenced code, link, image, video) and a
  paste matcher strips any other attributes (color/font/alignment/etc.) so
  the stored Delta stays in the round-trip-safe subset.
- Video embeds are preserved through markdown via a sentinel link title:
  `[video](URL "video-embed")`. `delta_md.md_to_delta` recognises the marker
  and re-emits a video embed on import.
- For databases predating the delta-only schema, run
  `.venv/bin/flask --app app migrate-to-delta-only` once. It backfills
  `content_delta` from legacy markdown and drops the obsolete columns.
  No-op on a fresh database.
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
