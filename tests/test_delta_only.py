"""Tests for delta-only content storage.

After the triple-storage flip, `report.content_delta` is the sole canonical
content column. The save route accepts the `content_delta` JSON field (what
the browser editor posts) and canonicalises it before storing.
"""

import json

from app.db import get_db
from app.delta_md import canonicalize_delta, md_to_delta


def _setup(client, app):
    """Create one workspace + one empty report on Todo. Returns (ws_id, todo_id, report_id)."""
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


def _stored_delta(app, report_id):
    with app.app_context():
        raw = get_db().execute(
            "SELECT content_delta FROM report WHERE id = ?", (report_id,),
        ).fetchone()["content_delta"]
    return json.loads(raw)


class TestSchema:
    def test_init_db_creates_content_delta_only(self, app):
        with app.app_context():
            cols = {row["name"] for row in get_db().execute(
                "PRAGMA table_info(report)"
            )}
        assert "content_delta" in cols
        assert "content" not in cols
        assert "content_html" not in cols


class TestSaveAcceptsDelta:
    def test_save_with_content_delta_stores_canonical(self, client, app):
        _, todo_id, rid = _setup(client, app)
        delta = {
            "ops": [
                {"insert": "Hello "},
                {"insert": "world", "attributes": {"bold": True}},
                {"insert": "\n"},
            ],
        }
        resp = client.post(
            f"/reports/{rid}",
            data={
                "title": "T",
                "board_id": todo_id,
                "tags": "",
                "content_delta": json.dumps(delta),
            },
        )
        assert resp.status_code in (200, 302)
        assert _stored_delta(app, rid) == canonicalize_delta(delta)


class TestSaveContentNormalization:
    def test_save_with_empty_content_stores_canonical_empty_delta(self, client, app):
        _, todo_id, rid = _setup(client, app)
        resp = client.post(
            f"/reports/{rid}",
            data={"title": "T", "board_id": todo_id, "tags": ""},
        )
        assert resp.status_code in (200, 302)
        assert _stored_delta(app, rid) == {"ops": [{"insert": "\n"}]}


class TestSaveRejectsMalformed:
    def test_save_with_invalid_json_returns_400(self, client, app):
        _, todo_id, rid = _setup(client, app)
        resp = client.post(
            f"/reports/{rid}",
            data={
                "title": "T",
                "board_id": todo_id,
                "tags": "",
                "content_delta": "not valid json{",
            },
        )
        assert resp.status_code == 400


class TestDetailHydration:
    def test_detail_renders_delta_textarea(self, client, app):
        _, todo_id, rid = _setup(client, app)
        delta = md_to_delta("# Title\n\nBody **text**")
        client.post(
            f"/reports/{rid}",
            data={
                "title": "T",
                "board_id": todo_id,
                "tags": "",
                "content_delta": json.dumps(delta),
            },
        )
        body = client.get(f"/reports/{rid}").get_data(as_text=True)
        assert 'name="content_delta"' in body
        # No legacy markdown/HTML textareas any more.
        assert 'name="content"' not in body
        assert 'name="content_html"' not in body


class TestAttachmentCleanupOnDeltaContent:
    def test_orphan_attachment_removed_when_image_removed_from_delta(
        self, client, app, tmp_path,
    ):
        """The substring-scan cleanup works on Delta JSON — an image embed
        produces "<id>/<filename>" in the stored content_delta."""
        import io
        from app.attachments import attachment_path

        _, todo_id, rid = _setup(client, app)
        # Upload an image attachment so the file exists on disk and a row
        # exists in the attachment table.
        upload_resp = client.post(
            f"/reports/{rid}/attachments",
            data={"file": (io.BytesIO(b"PNG"), "x.png", "image/png")},
            content_type="multipart/form-data",
        )
        assert upload_resp.status_code == 200
        url = upload_resp.get_json()["url"]            # /attachments/<rid>/<file>
        filename = upload_resp.get_json()["filename"]

        # Save a report whose Delta references the uploaded image.
        delta_with_img = {
            "ops": [
                {"insert": {"image": url}},
                {"insert": "\n"},
            ]
        }
        client.post(
            f"/reports/{rid}",
            data={
                "title": "T", "board_id": todo_id, "tags": "",
                "content_delta": json.dumps(delta_with_img),
            },
        )
        # File should still exist.
        with app.app_context():
            assert attachment_path(rid, filename).exists()

        # Save again with empty content — image is now unreferenced.
        client.post(
            f"/reports/{rid}",
            data={
                "title": "T", "board_id": todo_id, "tags": "",
                "content_delta": json.dumps({"ops": [{"insert": "\n"}]}),
            },
        )
        with app.app_context():
            assert not attachment_path(rid, filename).exists()
            # And the DB row gone too.
            row = get_db().execute(
                "SELECT 1 FROM attachment WHERE report_id = ? AND filename = ?",
                (rid, filename),
            ).fetchone()
            assert row is None
