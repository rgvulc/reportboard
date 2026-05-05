"""Flask CLI commands: export-backup and import-backup."""

from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from . import exporter, importer
from .db import get_db


@click.command("export-backup")
@click.argument("zip_path", type=click.Path(dir_okay=False, path_type=Path))
@with_appcontext
def export_backup_command(zip_path: Path) -> None:
    """Export the entire database to ZIP_PATH."""
    conn = get_db()
    attachments_root = Path(current_app.config["ATTACHMENTS_DIR"])
    exporter.export_to_zip(conn, attachments_root, zip_path)
    click.echo(f"Wrote backup to {zip_path}")


@click.command("import-backup")
@click.argument(
    "zip_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.confirmation_option(
    prompt="Importing will REPLACE all existing data. Continue?"
)
@with_appcontext
def import_backup_command(zip_path: Path) -> None:
    """Replace the entire database with the contents of ZIP_PATH."""
    conn = get_db()
    attachments_root = Path(current_app.config["ATTACHMENTS_DIR"])
    try:
        importer.import_from_zip(conn, attachments_root, zip_path)
    except importer.ImportError as e:
        raise click.ClickException(str(e))
    click.echo(f"Imported backup from {zip_path}")


def register(app) -> None:
    app.cli.add_command(export_backup_command)
    app.cli.add_command(import_backup_command)
