import sqlite3
from pathlib import Path

import click
from flask import current_app, g


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


def register(app) -> None:
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
