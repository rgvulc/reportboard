"""Triple-format content storage (markdown + Delta + HTML).

The client computes all three on save; the server stores them verbatim and
serves all three on detail. The editor picks the highest-fidelity form
available (Delta > HTML > markdown) when hydrating.
"""

import json

from app.db import get_db


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


def _get_report(app, report_id):
    with app.app_context():
        return dict(get_db().execute(
            "SELECT content, content_delta, content_html FROM report WHERE id = ?",
            (report_id,),
        ).fetchone())


class TestTripleStorageSave:
    def test_save_stores_all_three_columns(self, client, app):
        _, todo_id, rid = _setup(client, app)
        delta = json.dumps({"ops": [
            {"insert": "Hello "},
            {"insert": "world", "attributes": {"bold": True}},
            {"insert": "\n"},
        ]})
        html = "<p>Hello <strong>world</strong></p>"
        md = "Hello **world**"

        resp = client.post(
            f"/reports/{rid}",
            data={
                "title": "T",
                "board_id": todo_id,
                "tags": "",
                "content": md,
                "content_delta": delta,
                "content_html": html,
            },
        )
        assert resp.status_code in (200, 302)

        row = _get_report(app, rid)
        assert row["content"] == md
        assert row["content_delta"] == delta
        assert row["content_html"] == html

    def test_save_without_new_fields_keeps_backwards_compat(self, client, app):
        """Existing test patterns that POST only `content` still work; the
        new columns just stay empty."""
        _, todo_id, rid = _setup(client, app)
        resp = client.post(
            f"/reports/{rid}",
            data={"title": "T", "board_id": todo_id, "tags": "",
                  "content": "Plain old markdown"},
        )
        assert resp.status_code in (200, 302)

        row = _get_report(app, rid)
        assert row["content"] == "Plain old markdown"
        assert row["content_delta"] == ""
        assert row["content_html"] == ""

    def test_save_overwrites_previous_values(self, client, app):
        _, todo_id, rid = _setup(client, app)
        # First save
        client.post(f"/reports/{rid}", data={
            "title": "T", "board_id": todo_id, "tags": "",
            "content": "v1",
            "content_delta": '{"ops":[{"insert":"v1\\n"}]}',
            "content_html": "<p>v1</p>",
        })
        # Second save with different values
        client.post(f"/reports/{rid}", data={
            "title": "T", "board_id": todo_id, "tags": "",
            "content": "v2",
            "content_delta": '{"ops":[{"insert":"v2\\n"}]}',
            "content_html": "<p>v2</p>",
        })

        row = _get_report(app, rid)
        assert row["content"] == "v2"
        assert "v2" in row["content_delta"]
        assert row["content_html"] == "<p>v2</p>"


class TestDetailHydration:
    def test_detail_renders_all_three_textareas(self, client, app):
        _, todo_id, rid = _setup(client, app)
        client.post(f"/reports/{rid}", data={
            "title": "T", "board_id": todo_id, "tags": "",
            "content": "Hello **world**",
            "content_delta": '{"ops":[{"insert":"Hello "},'
                             '{"insert":"world","attributes":{"bold":true}},'
                             '{"insert":"\\n"}]}',
            "content_html": "<p>Hello <strong>world</strong></p>",
        })

        body = client.get(f"/reports/{rid}").get_data(as_text=True)
        # Each form has its own hidden textarea.
        assert 'name="content"' in body
        assert 'name="content_delta"' in body
        assert 'name="content_html"' in body
        # The Quill init script is present and references the new columns.
        assert "new Quill" in body
        assert "setContents" in body

    def test_detail_works_for_legacy_markdown_only_report(self, client, app):
        """A report saved before this change has only markdown; the editor
        should still hydrate via the marked.parse fallback path."""
        _, todo_id, rid = _setup(client, app)
        # Direct DB write that mimics legacy state — only `content` populated.
        with app.app_context():
            db = get_db()
            with db:
                db.execute(
                    "UPDATE report SET content = ?, content_delta = '', "
                    "content_html = '' WHERE id = ?",
                    ("# Legacy heading\n\nSome **bold** text", rid),
                )

        body = client.get(f"/reports/{rid}").get_data(as_text=True)
        assert "# Legacy heading" in body
        # The fallback path uses marked → dangerouslyPasteHTML.
        assert "marked.parse" in body


class TestInitDbSchema:
    def test_init_db_creates_content_columns(self, app):
        with app.app_context():
            cols = {row["name"] for row in get_db().execute(
                "PRAGMA table_info(report)"
            )}
        assert "content" in cols
        assert "content_delta" in cols
        assert "content_html" in cols

    def test_new_columns_default_to_empty_string(self, client, app):
        _, _, rid = _setup(client, app)
        row = _get_report(app, rid)
        # Freshly-created report (no save yet) has empty new columns.
        assert row["content_delta"] == ""
        assert row["content_html"] == ""


class TestMigrationCommand:
    def test_migrate_is_noop_when_columns_present(self, app, runner):
        # Fresh init-db already has the columns, so the command should report
        # nothing to do.
        result = runner.invoke(args=["migrate-content-columns"])
        assert result.exit_code == 0
        assert "already current" in result.output.lower() \
            or "no new columns" in result.output.lower()

    def test_migrate_adds_missing_columns(self, app, runner):
        # Drop and recreate the table without the new columns to simulate
        # a pre-migration DB. Foreign keys must be off so child tables don't
        # cascade-delete during the swap.
        with app.app_context():
            db = get_db()
            db.execute("PRAGMA foreign_keys = OFF")
            with db:
                db.execute("DROP TABLE report")
                db.execute("""
                    CREATE TABLE report (
                        id INTEGER PRIMARY KEY,
                        workspace_id INTEGER NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
                        board_id INTEGER NOT NULL REFERENCES board(id),
                        importance_id INTEGER REFERENCES importance_level(id),
                        title TEXT NOT NULL,
                        content TEXT NOT NULL DEFAULT '',
                        position INTEGER NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
                        updated_at TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
                    )
                """)

        result = runner.invoke(args=["migrate-content-columns"])
        assert result.exit_code == 0
        assert "content_delta" in result.output
        assert "content_html" in result.output

        with app.app_context():
            cols = {row["name"] for row in get_db().execute(
                "PRAGMA table_info(report)"
            )}
        assert "content_delta" in cols
        assert "content_html" in cols
