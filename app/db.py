import sqlite3
from pathlib import Path

import click
from flask import current_app, g
from flask.cli import with_appcontext


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db() -> None:
    conn = get_db()
    sql_dir = Path(current_app.root_path)
    with conn:
        conn.executescript((sql_dir / "schema.sql").read_text())
        conn.executescript((sql_dir / "seed.sql").read_text())


@click.command("init-db")
def init_db_command() -> None:
    """Drop and recreate the database, then seed defaults."""
    db_path = Path(current_app.config["DATABASE"])
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db()
    click.echo(f"Initialized database at {db_path}")


@click.command("migrate-to-delta-only")
@with_appcontext
def migrate_to_delta_only_command() -> None:
    """Migrate the `report` table to single-column Delta storage.

    Handles every prior schema this project has shipped:
      - legacy (only `content`)
      - triple-storage (content + content_delta + content_html)
      - already-migrated (only content_delta)

    For rows missing a Delta, the legacy markdown `content` is parsed via
    delta_md.md_to_delta and serialised as JSON. Then content + content_html
    columns are dropped.
    """
    import json as _json
    from .delta_md import md_to_delta

    conn = get_db()
    existing = {row["name"] for row in conn.execute(
        "PRAGMA table_info(report)"
    )}

    if "content_delta" not in existing:
        conn.execute(
            "ALTER TABLE report ADD COLUMN content_delta TEXT NOT NULL DEFAULT ''"
        )

    # Backfill content_delta from markdown content for any row missing it.
    backfilled = 0
    rows = conn.execute(
        "SELECT id, content, content_delta FROM report"
        if "content" in existing else
        "SELECT id, content_delta FROM report"
    ).fetchall()
    for row in rows:
        existing_delta = (row["content_delta"] or "").strip()
        if existing_delta:
            continue
        md = row["content"] if "content" in row.keys() else ""
        delta = md_to_delta(md or "")
        conn.execute(
            "UPDATE report SET content_delta = ? WHERE id = ?",
            (_json.dumps(delta, ensure_ascii=False), row["id"]),
        )
        backfilled += 1

    # Drop the now-redundant columns. SQLite 3.35+ supports DROP COLUMN.
    dropped = []
    if "content" in existing:
        conn.execute("ALTER TABLE report DROP COLUMN content")
        dropped.append("content")
    if "content_html" in existing:
        conn.execute("ALTER TABLE report DROP COLUMN content_html")
        dropped.append("content_html")

    conn.commit()
    click.echo(
        f"Backfilled {backfilled} row(s); dropped columns: "
        f"{', '.join(dropped) if dropped else '(none)'}"
    )


def register(app) -> None:
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(migrate_to_delta_only_command)
