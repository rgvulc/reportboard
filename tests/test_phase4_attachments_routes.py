"""Integration tests for the attachments routes and the save-time cleanup."""

import io
from pathlib import Path

from app.attachments import attachment_path, report_dir
from app.db import get_db


# --- helpers ---

def _setup(client, app):
    """Create one workspace and one empty report. Returns (ws_id, todo_id, report_id)."""
    client.post("/workspaces", data={"name": "WS"})
    with app.app_context():
        db = get_db()
        ws_id = db.execute("SELECT id FROM workspace WHERE name='WS'").fetchone()["id"]
        todo_id = db.execute("SELECT id FROM board WHERE name='Todo'").fetchone()["id"]
    client.post(
        f"/workspaces/{ws_id}/reports",
        data={"title": "T", "board_id": todo_id},
    )
    with app.app_context():
        report_id = get_db().execute("SELECT id FROM report").fetchone()["id"]
    return ws_id, todo_id, report_id


def _upload(client, report_id, filename, content=b"x", mimetype="image/png"):
    return client.post(
        f"/reports/{report_id}/attachments",
        data={"file": (io.BytesIO(content), filename, mimetype)},
        content_type="multipart/form-data",
    )


def _save_report(client, report_id, board_id, content):
    return client.post(
        f"/reports/{report_id}",
        data={"title": "T", "board_id": board_id, "tags": "", "content": content},
    )


def _attachments_in_db(app, report_id):
    with app.app_context():
        return [
            r["filename"]
            for r in get_db().execute(
                "SELECT filename FROM attachment WHERE report_id = ?", (report_id,)
            ).fetchall()
        ]


# --- upload route ---

def test_upload_persists_row_and_writes_file_and_returns_markdown_ref(client, app):
    _, _, report_id = _setup(client, app)

    response = _upload(client, report_id, "photo.png", b"PNGDATA", "image/png")
    assert response.status_code == 200

    payload = response.get_json()
    assert payload["mime_type"] == "image/png"
    assert payload["is_image"] is True
    assert payload["filename"].endswith(".png")
    assert payload["url"].endswith(payload["filename"])
    assert f"/attachments/{report_id}/{payload['filename']}" in payload["url"]
    assert payload["markdown_ref"].startswith("![")
    assert payload["markdown_ref"].endswith(f"]({payload['url']})")

    assert _attachments_in_db(app, report_id) == [payload["filename"]]

    with app.app_context():
        on_disk = attachment_path(report_id, payload["filename"])
        assert on_disk.exists()
        assert on_disk.read_bytes() == b"PNGDATA"


def test_non_image_upload_returns_link_style_ref(client, app):
    _, _, report_id = _setup(client, app)

    response = _upload(client, report_id, "doc.pdf", b"%PDF-1.4", "application/pdf")
    payload = response.get_json()

    assert payload["is_image"] is False
    assert payload["markdown_ref"].startswith("[")
    assert not payload["markdown_ref"].startswith("![")


def test_upload_to_unknown_report_returns_404(client, app):
    response = _upload(client, 999, "photo.png")
    assert response.status_code == 404


def test_upload_without_file_returns_400(client, app):
    _, _, report_id = _setup(client, app)
    response = client.post(
        f"/reports/{report_id}/attachments",
        data={},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_upload_with_path_traversal_in_original_name_lands_in_correct_directory(client, app):
    _, _, report_id = _setup(client, app)

    response = _upload(
        client, report_id, "../../etc/passwd.png", b"DATA", "image/png"
    )
    assert response.status_code == 200
    payload = response.get_json()

    # On-disk filename is uuid-based and lives only inside the report's dir.
    with app.app_context():
        on_disk = attachment_path(report_id, payload["filename"])
        assert on_disk.exists()

        report_root = report_dir(report_id).resolve()
        assert on_disk.resolve().is_relative_to(report_root)

    # Stored original_name has the path components stripped.
    with app.app_context():
        original = get_db().execute(
            "SELECT original_name FROM attachment WHERE report_id = ?",
            (report_id,),
        ).fetchone()["original_name"]
    assert "/" not in original and ".." not in original


# --- serve route ---

def test_serve_returns_file_with_recorded_mime_type(client, app):
    _, _, report_id = _setup(client, app)
    payload = _upload(client, report_id, "photo.png", b"PNGDATA", "image/png").get_json()

    response = client.get(f"/attachments/{report_id}/{payload['filename']}")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data == b"PNGDATA"


def test_serve_filename_not_in_db_returns_404(client, app):
    _, _, report_id = _setup(client, app)
    response = client.get(f"/attachments/{report_id}/nonexistent.png")
    assert response.status_code == 404


def test_serve_does_not_expose_files_owned_by_other_reports(client, app):
    _, todo_id, report_a = _setup(client, app)

    # Make a second report.
    with app.app_context():
        db = get_db()
        ws_id = db.execute("SELECT id FROM workspace").fetchone()["id"]
    client.post(
        f"/workspaces/{ws_id}/reports",
        data={"title": "B", "board_id": todo_id},
    )
    with app.app_context():
        report_b = get_db().execute(
            "SELECT id FROM report WHERE title = 'B'"
        ).fetchone()["id"]

    payload = _upload(client, report_a, "secret.png", b"SECRET").get_json()

    # Same filename queried under report B's id must 404, even though the
    # file exists on disk under report A's directory.
    response = client.get(f"/attachments/{report_b}/{payload['filename']}")
    assert response.status_code == 404


# --- save-time cleanup ---

def test_save_keeps_attachment_when_content_references_it(client, app):
    _, todo_id, report_id = _setup(client, app)
    payload = _upload(client, report_id, "photo.png").get_json()

    _save_report(client, report_id, todo_id, f"see ![]({payload['url']})")

    assert _attachments_in_db(app, report_id) == [payload["filename"]]
    with app.app_context():
        assert attachment_path(report_id, payload["filename"]).exists()


def test_save_deletes_orphaned_row_and_file(client, app):
    _, todo_id, report_id = _setup(client, app)
    payload = _upload(client, report_id, "photo.png").get_json()

    _save_report(client, report_id, todo_id, "no reference here")

    assert _attachments_in_db(app, report_id) == []
    with app.app_context():
        assert not attachment_path(report_id, payload["filename"]).exists()


def test_save_with_renamed_reference_drops_old_keeps_new(client, app):
    _, todo_id, report_id = _setup(client, app)
    p1 = _upload(client, report_id, "old.png").get_json()
    p2 = _upload(client, report_id, "new.png").get_json()

    _save_report(client, report_id, todo_id, f"![](.{p2['url']})".replace("..", "."))

    remaining = _attachments_in_db(app, report_id)
    assert remaining == [p2["filename"]]
    with app.app_context():
        assert not attachment_path(report_id, p1["filename"]).exists()
        assert attachment_path(report_id, p2["filename"]).exists()


def test_save_cleanup_only_considers_this_reports_attachments(client, app):
    """Cross-report leak guard: A's content referencing B's file does not save A's own files."""
    _, todo_id, report_a = _setup(client, app)

    # Second report
    with app.app_context():
        ws_id = get_db().execute("SELECT id FROM workspace").fetchone()["id"]
    client.post(
        f"/workspaces/{ws_id}/reports",
        data={"title": "B", "board_id": todo_id},
    )
    with app.app_context():
        report_b = get_db().execute(
            "SELECT id FROM report WHERE title = 'B'"
        ).fetchone()["id"]

    a_payload = _upload(client, report_a, "a.png").get_json()
    b_payload = _upload(client, report_b, "b.png").get_json()

    # A's content references B's file but not its own.
    _save_report(client, report_a, todo_id, f"see ![]({b_payload['url']})")

    # A's own attachment should be cleaned up (orphaned).
    assert _attachments_in_db(app, report_a) == []
    with app.app_context():
        assert not attachment_path(report_a, a_payload["filename"]).exists()
        # B's attachment should be untouched.
        assert b_payload["filename"] in _attachments_in_db(app, report_b)
        assert attachment_path(report_b, b_payload["filename"]).exists()


def test_save_cleanup_is_idempotent_on_unchanged_content(client, app):
    _, todo_id, report_id = _setup(client, app)
    payload = _upload(client, report_id, "photo.png").get_json()

    content = f"![]({payload['url']})"
    _save_report(client, report_id, todo_id, content)
    _save_report(client, report_id, todo_id, content)

    assert _attachments_in_db(app, report_id) == [payload["filename"]]
    with app.app_context():
        assert attachment_path(report_id, payload["filename"]).exists()


# --- delete report ---

def test_delete_report_removes_attachments_directory(client, app):
    _, _, report_id = _setup(client, app)
    _upload(client, report_id, "a.png")
    _upload(client, report_id, "b.pdf", b"PDFDATA", "application/pdf")

    with app.app_context():
        rdir = report_dir(report_id)
        assert rdir.exists()
        assert any(rdir.iterdir())

    response = client.post(f"/reports/{report_id}/delete")
    assert response.status_code in (200, 302)

    with app.app_context():
        assert not report_dir(report_id).exists()
