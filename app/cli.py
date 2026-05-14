"""Flask CLI: backup export/import + round-trip verification.

`export-backup` / `import-backup` default to the canonical JSON format
(lossless, used for migration). Pass `--format=markdown` to use the
human-readable markdown format (lossy on round-trip).

`verify-export` reads a JSON backup and reports any reports whose Delta
doesn't round-trip cleanly through markdown — useful before sharing or
deleting your local DB.
"""

import json
import tempfile
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from . import backup_json, exporter, importer
from .db import get_db
from .delta_md import canonicalize_delta, delta_to_md, md_to_delta


# ============================================================================
#  Export / import
# ============================================================================

_FORMAT_CHOICES = click.Choice(["json", "markdown"], case_sensitive=False)


@click.command("export-backup")
@click.argument("zip_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--format", "fmt", type=_FORMAT_CHOICES, default="json",
              show_default=True, help="Backup format.")
@with_appcontext
def export_backup_command(zip_path: Path, fmt: str) -> None:
    """Export the entire database to ZIP_PATH."""
    conn = get_db()
    attachments_root = Path(current_app.config["ATTACHMENTS_DIR"])
    if fmt == "json":
        backup_json.export_to_zip(conn, attachments_root, zip_path)
    else:
        exporter.export_to_zip(conn, attachments_root, zip_path)
    click.echo(f"Wrote {fmt} backup to {zip_path}")


@click.command("import-backup")
@click.argument(
    "zip_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--format", "fmt", type=_FORMAT_CHOICES, default="json",
              show_default=True, help="Backup format.")
@click.confirmation_option(
    prompt="Importing will REPLACE all existing data. Continue?"
)
@with_appcontext
def import_backup_command(zip_path: Path, fmt: str) -> None:
    """Replace the entire database with the contents of ZIP_PATH."""
    conn = get_db()
    attachments_root = Path(current_app.config["ATTACHMENTS_DIR"])
    try:
        if fmt == "json":
            backup_json.import_from_zip(conn, attachments_root, zip_path)
        else:
            importer.import_from_zip(conn, attachments_root, zip_path)
    except (backup_json.ImportError, importer.ImportError) as e:
        raise click.ClickException(str(e))
    click.echo(f"Imported {fmt} backup from {zip_path}")


# ============================================================================
#  Verify
# ============================================================================

@click.command("verify-export")
@click.argument(
    "zip_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@with_appcontext
def verify_export_command(zip_path: Path) -> None:
    """Verify a JSON backup round-trips cleanly through markdown.

    For each report in the export, runs:
        md_to_delta(delta_to_md(content_delta))
    and compares the canonicalised result against the original canonicalised
    Delta. Any mismatch is flagged.
    """
    with tempfile.TemporaryDirectory() as tmp:
        extracted = Path(tmp) / "extracted"
        extracted.mkdir()
        try:
            importer.safe_extract_zip(zip_path, extracted)
            archive = backup_json.load_archive(extracted)
        except (backup_json.ImportError, importer.ImportError) as e:
            raise click.ClickException(str(e))

    total = 0
    issues = []
    for ws in archive.data.get("workspaces", []):
        for report in ws.get("reports", []):
            total += 1
            delta = report["content_delta"]
            canonical = canonicalize_delta(delta)
            try:
                md = delta_to_md(delta)
            except Exception as e:
                issues.append((report["id"], report.get("title", ""),
                               f"delta_to_md failed: {e}"))
                continue
            try:
                round_tripped = canonicalize_delta(md_to_delta(md))
            except Exception as e:
                issues.append((report["id"], report.get("title", ""),
                               f"md_to_delta failed: {e}"))
                continue
            if canonical != round_tripped:
                issues.append((
                    report["id"], report.get("title", ""),
                    "Delta differs after delta→md→delta",
                ))

    if issues:
        click.echo(
            f"FAIL — {len(issues)} of {total} report(s) do not round-trip "
            f"cleanly:"
        )
        for rid, title, msg in issues:
            click.echo(f"  - report {rid} ({title!r}): {msg}")
        raise click.ClickException("verification failed")
    click.echo(f"OK — all {total} report(s) round-trip cleanly through markdown")


def register(app) -> None:
    app.cli.add_command(export_backup_command)
    app.cli.add_command(import_backup_command)
    app.cli.add_command(verify_export_command)
