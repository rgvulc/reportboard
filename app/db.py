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


@click.command("migrate-content-columns")
@with_appcontext
def migrate_content_columns_command() -> None:
    """Add content_delta + content_html columns to an existing report table.

    No-op on a fresh schema (init-db already includes them). Use this on a
    DB that was created before triple-format storage was introduced.
    """
    conn = get_db()
    existing = {row["name"] for row in conn.execute(
        "PRAGMA table_info(report)"
    )}
    added = []
    if "content_delta" not in existing:
        conn.execute(
            "ALTER TABLE report ADD COLUMN content_delta TEXT NOT NULL DEFAULT ''"
        )
        added.append("content_delta")
    if "content_html" not in existing:
        conn.execute(
            "ALTER TABLE report ADD COLUMN content_html TEXT NOT NULL DEFAULT ''"
        )
        added.append("content_html")
    conn.commit()
    if added:
        click.echo(f"Added columns: {', '.join(added)}")
    else:
        click.echo("No new columns needed; schema already current.")


def register(app) -> None:
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(migrate_content_columns_command)
