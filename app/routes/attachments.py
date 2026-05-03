from pathlib import Path

from flask import Blueprint, abort, jsonify, request, send_file, url_for

from ..attachments import attachment_path, store_upload
from ..db import get_db


bp = Blueprint("attachments", __name__)


@bp.post("/reports/<int:report_id>/attachments")
def upload(report_id: int):
    db = get_db()
    if db.execute("SELECT 1 FROM report WHERE id = ?", (report_id,)).fetchone() is None:
        abort(404)

    file_storage = request.files.get("file")
    if file_storage is None or not file_storage.filename:
        abort(400)

    filename, size_bytes = store_upload(report_id, file_storage)
    mime_type = file_storage.mimetype or "application/octet-stream"
    original_name = Path(file_storage.filename).name or filename

    with db:
        db.execute(
            "INSERT INTO attachment (report_id, filename, original_name, mime_type, size_bytes) "
            "VALUES (?, ?, ?, ?, ?)",
            (report_id, filename, original_name, mime_type, size_bytes),
        )

    is_image = mime_type.startswith("image/")
    url = url_for("attachments.serve", report_id=report_id, filename=filename)
    label = original_name.replace("[", "(").replace("]", ")")
    md_ref = f"![{label}]({url})" if is_image else f"[{label}]({url})"

    return jsonify({
        "filename": filename,
        "url": url,
        "markdown_ref": md_ref,
        "mime_type": mime_type,
        "is_image": is_image,
    })


@bp.get("/attachments/<int:report_id>/<filename>")
def serve(report_id: int, filename: str):
    db = get_db()
    row = db.execute(
        "SELECT mime_type FROM attachment WHERE report_id = ? AND filename = ?",
        (report_id, filename),
    ).fetchone()
    if row is None:
        abort(404)

    return send_file(
        str(attachment_path(report_id, filename)),
        mimetype=row["mime_type"],
    )
