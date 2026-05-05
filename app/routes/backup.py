import tempfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, request, send_file, url_for

from .. import exporter, importer
from ..db import get_db


bp = Blueprint("backup", __name__)


@bp.get("/settings/backup/export")
def export():
    conn = get_db()
    attachments_root = Path(current_app.config["ATTACHMENTS_DIR"])

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    zip_path = Path(tmp.name)
    exporter.export_to_zip(conn, attachments_root, zip_path)

    download_name = f"reportboard-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
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


@bp.post("/settings/backup/import")
def import_():
    upload = request.files.get("archive")
    if upload is None or not upload.filename:
        flash("Choose a backup .zip file to import.", "error")
        return redirect(url_for("settings.index")), 400

    conn = get_db()
    attachments_root = Path(current_app.config["ATTACHMENTS_DIR"])

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        upload.save(tmp.name)
        tmp_path = Path(tmp.name)

    try:
        try:
            importer.import_from_zip(conn, attachments_root, tmp_path)
        except importer.ImportError as e:
            flash(f"Import failed: {e}", "error")
            return redirect(url_for("settings.index")), 400
    finally:
        tmp_path.unlink(missing_ok=True)

    flash("Imported backup. All previous data was replaced.", "success")
    return redirect(url_for("settings.index"))
