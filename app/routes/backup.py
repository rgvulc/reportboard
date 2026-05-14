import tempfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, request, send_file, url_for

from .. import backup_json, exporter, importer
from ..db import get_db


bp = Blueprint("backup", __name__)


def _send_zip(zip_path: Path, basename: str):
    download_name = f"{basename}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    response = send_file(
        zip_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=download_name,
    )

    @response.call_on_close
    def _cleanup():
        zip_path.unlink(missing_ok=True)

    return response


# ============================================================================
#  Export
# ============================================================================

@bp.get("/settings/backup/export-json")
def export_json():
    conn = get_db()
    attachments_root = Path(current_app.config["ATTACHMENTS_DIR"])
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    zip_path = Path(tmp.name)
    backup_json.export_to_zip(conn, attachments_root, zip_path)
    return _send_zip(zip_path, "reportboard")


@bp.get("/settings/backup/export-markdown")
def export_markdown():
    conn = get_db()
    attachments_root = Path(current_app.config["ATTACHMENTS_DIR"])
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    zip_path = Path(tmp.name)
    exporter.export_to_zip(conn, attachments_root, zip_path)
    return _send_zip(zip_path, "reportboard-md")


# ============================================================================
#  Import
# ============================================================================

def _save_upload(field_name: str) -> Path | None:
    upload = request.files.get(field_name)
    if upload is None or not upload.filename:
        return None
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        upload.save(tmp.name)
        return Path(tmp.name)


@bp.post("/settings/backup/import-json")
def import_json():
    tmp_path = _save_upload("archive")
    if tmp_path is None:
        flash("Choose a JSON backup .zip file to import.", "error")
        return redirect(url_for("settings.index")), 400

    conn = get_db()
    attachments_root = Path(current_app.config["ATTACHMENTS_DIR"])
    try:
        try:
            backup_json.import_from_zip(conn, attachments_root, tmp_path)
        except backup_json.ImportError as e:
            flash(f"JSON import failed: {e}", "error")
            return redirect(url_for("settings.index")), 400
    finally:
        tmp_path.unlink(missing_ok=True)

    flash("Imported JSON backup. All previous data was replaced.", "success")
    return redirect(url_for("settings.index"))


@bp.post("/settings/backup/import-markdown")
def import_markdown():
    tmp_path = _save_upload("archive")
    if tmp_path is None:
        flash("Choose a markdown backup .zip file to import.", "error")
        return redirect(url_for("settings.index")), 400

    conn = get_db()
    attachments_root = Path(current_app.config["ATTACHMENTS_DIR"])
    try:
        try:
            importer.import_from_zip(conn, attachments_root, tmp_path)
        except importer.ImportError as e:
            flash(f"Markdown import failed: {e}", "error")
            return redirect(url_for("settings.index")), 400
    finally:
        tmp_path.unlink(missing_ok=True)

    flash("Imported markdown backup. All previous data was replaced.", "success")
    return redirect(url_for("settings.index"))
