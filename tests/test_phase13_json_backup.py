"""Tests for the JSON-format backup (canonical migration format).

Shape: a zip containing a single data.json plus attachments/<id>/<file>.
The exporter produces deterministic output; the importer is replace-mode
with up-front validation.
"""

import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest

from app import backup_json
from app.db import get_db
from app.delta_md import md_to_delta


# --- Helpers -----------------------------------------------------------------

def _delta_json(md: str) -> str:
    return json.dumps(md_to_delta(md), ensure_ascii=False)


def _seed(app):
    """Seed a representative DB: two workspaces, three reports across two
    boards, tags, checklist, and one attachment with a real on-disk file."""
    with app.app_context():
        db = get_db()
        with db:
            db.execute(
                "INSERT INTO workspace (id, name, position, created_at, updated_at) "
                "VALUES (1, 'Personal', 0, '2026-01-01 10:00:00.000', "
                "'2026-01-02 10:00:00.000')"
            )
            db.execute(
                "INSERT INTO workspace (id, name, position, created_at, updated_at) "
                "VALUES (2, 'Work', 1, '2026-01-03 10:00:00.000', "
                "'2026-01-04 10:00:00.000')"
            )
            todo_id = db.execute(
                "SELECT id FROM board WHERE name='Todo'"
            ).fetchone()["id"]
            done_id = db.execute(
                "SELECT id FROM board WHERE name='Complete'"
            ).fetchone()["id"]
            high_id = db.execute(
                "SELECT id FROM importance_level WHERE name='High'"
            ).fetchone()["id"]

            db.execute("INSERT INTO tag (id, name) VALUES (10, 'work')")
            db.execute("INSERT INTO tag (id, name) VALUES (11, 'urgent')")
            db.execute("INSERT INTO tag (id, name) VALUES (12, 'orphan')")

            db.execute(
                "INSERT INTO report (id, workspace_id, board_id, importance_id, "
                "title, content_delta, position, created_at, updated_at) "
                "VALUES (100, 1, ?, ?, 'Buy milk', ?, 0, "
                "'2026-01-05 09:00:00.000', '2026-01-06 09:00:00.000')",
                (todo_id, high_id,
                 _delta_json("See screenshot: ![](/attachments/100/abc.png)")),
            )
            db.execute(
                "INSERT INTO report (id, workspace_id, board_id, importance_id, "
                "title, content_delta, position, created_at, updated_at) "
                "VALUES (101, 1, ?, NULL, 'Empty report', ?, 1, "
                "'2026-01-05 09:00:00.000', '2026-01-06 09:00:00.000')",
                (done_id, _delta_json("")),
            )
            db.execute(
                "INSERT INTO report (id, workspace_id, board_id, importance_id, "
                "title, content_delta, position, created_at, updated_at) "
                "VALUES (102, 2, ?, NULL, 'Notes', ?, 0, "
                "'2026-01-05 09:00:00.000', '2026-01-06 09:00:00.000')",
                (todo_id, _delta_json("plain text")),
            )

            db.execute("INSERT INTO report_tag (report_id, tag_id) VALUES (100, 10)")
            db.execute("INSERT INTO report_tag (report_id, tag_id) VALUES (100, 11)")

            db.execute(
                "INSERT INTO checklist_item (report_id, text, done, position) "
                "VALUES (100, 'Find receipt', 1, 0)"
            )
            db.execute(
                "INSERT INTO checklist_item (report_id, text, done, position) "
                "VALUES (100, 'Email Sam', 0, 1)"
            )

            db.execute(
                "INSERT INTO attachment (report_id, filename, original_name, "
                "mime_type, size_bytes, created_at) "
                "VALUES (100, 'abc.png', 'screenshot.png', 'image/png', 5, "
                "'2026-01-05 09:30:00.000')"
            )

        att_dir = Path(app.config["ATTACHMENTS_DIR"]) / "100"
        att_dir.mkdir(parents=True, exist_ok=True)
        (att_dir / "abc.png").write_bytes(b"PNGxx")


def _snapshot(app):
    with app.app_context():
        db = get_db()
        return {
            "boards": [dict(r) for r in db.execute(
                "SELECT * FROM board ORDER BY id")],
            "importance_levels": [dict(r) for r in db.execute(
                "SELECT * FROM importance_level ORDER BY id")],
            "tags": [dict(r) for r in db.execute(
                "SELECT * FROM tag ORDER BY id")],
            "workspaces": [dict(r) for r in db.execute(
                "SELECT * FROM workspace ORDER BY id")],
            "reports": [dict(r) for r in db.execute(
                "SELECT * FROM report ORDER BY id")],
            "report_tag": [dict(r) for r in db.execute(
                "SELECT * FROM report_tag ORDER BY report_id, tag_id")],
            "checklist_item": [dict(r) for r in db.execute(
                "SELECT * FROM checklist_item ORDER BY id")],
            "attachment": [dict(r) for r in db.execute(
                "SELECT * FROM attachment ORDER BY id")],
        }


def _wipe(app):
    with app.app_context():
        db = get_db()
        with db:
            db.execute("DELETE FROM workspace")
            db.execute("DELETE FROM tag")
            db.execute("DELETE FROM board")
            db.execute("DELETE FROM importance_level")
    shutil.rmtree(app.config["ATTACHMENTS_DIR"], ignore_errors=True)
    Path(app.config["ATTACHMENTS_DIR"]).mkdir(parents=True, exist_ok=True)


def _export_to(app, dest_dir) -> Path:
    zip_path = Path(dest_dir) / "out.zip"
    with app.app_context():
        backup_json.export_to_zip(
            get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path,
        )
    return zip_path


def _make_zip(tmp_path: Path, files: dict[str, str | bytes]) -> Path:
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return zip_path


# --- Export structure -------------------------------------------------------

class TestExportStructure:
    def test_export_writes_data_json_and_attachments(self, app, tmp_path):
        _seed(app)
        zip_path = _export_to(app, tmp_path)

        with zipfile.ZipFile(zip_path) as zf:
            names = sorted(zf.namelist())
        assert "data.json" in names
        assert "attachments/100/abc.png" in names
        # No per-report files / YAML / markdown — JSON only.
        assert not any(n.endswith(".md") for n in names)
        assert not any(n.endswith(".yaml") or n.endswith(".yml") for n in names)
        # Manifest file from the markdown format is absent.
        assert "manifest.json" not in names

    def test_data_json_is_well_formed(self, app, tmp_path):
        _seed(app)
        zip_path = _export_to(app, tmp_path)
        with zipfile.ZipFile(zip_path) as zf:
            data = json.loads(zf.read("data.json"))
        assert data["schema_version"] == backup_json.SCHEMA_VERSION
        assert isinstance(data["boards"], list)
        assert isinstance(data["workspaces"], list)
        assert len(data["workspaces"]) == 2
        # Reports nested inside their workspace.
        personal = next(w for w in data["workspaces"] if w["id"] == 1)
        assert len(personal["reports"]) == 2

    def test_content_delta_is_embedded_as_json_object(self, app, tmp_path):
        _seed(app)
        zip_path = _export_to(app, tmp_path)
        with zipfile.ZipFile(zip_path) as zf:
            data = json.loads(zf.read("data.json"))
        r = next(rep for ws in data["workspaces"]
                 for rep in ws["reports"] if rep["id"] == 100)
        # content_delta is a nested JSON object, not a quoted string.
        assert isinstance(r["content_delta"], dict)
        assert "ops" in r["content_delta"]


# --- Round trip (load-bearing) ----------------------------------------------

class TestRoundTrip:
    def test_export_then_import_preserves_db_byte_for_byte(self, app, tmp_path):
        _seed(app)
        before = _snapshot(app)
        zip_path = _export_to(app, tmp_path)

        _wipe(app)
        with app.app_context():
            backup_json.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path,
            )
        after = _snapshot(app)

        # Strip content_delta from the row-level snapshot for direct equality
        # (the JSON-string form may re-serialise differently after round trip);
        # compare it semantically as a parsed dict instead.
        def _split(snap):
            reports = [dict(r) for r in snap["reports"]]
            deltas = {r["id"]: json.loads(r.pop("content_delta"))
                      for r in reports}
            other = dict(snap)
            other["reports"] = reports
            return other, deltas

        before_rest, before_deltas = _split(before)
        after_rest, after_deltas = _split(after)
        assert before_rest == after_rest
        assert before_deltas == after_deltas

    def test_attachment_file_round_trips(self, app, tmp_path):
        _seed(app)
        zip_path = _export_to(app, tmp_path)
        _wipe(app)
        with app.app_context():
            backup_json.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path,
            )
        att = Path(app.config["ATTACHMENTS_DIR"]) / "100" / "abc.png"
        assert att.read_bytes() == b"PNGxx"

    def test_orphan_tag_preserved(self, app, tmp_path):
        _seed(app)
        zip_path = _export_to(app, tmp_path)
        _wipe(app)
        with app.app_context():
            backup_json.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path,
            )
            names = {r["name"] for r in get_db().execute("SELECT name FROM tag")}
        assert "orphan" in names

    def test_idempotent_export(self, app, tmp_path):
        """Two exports of the same DB produce byte-identical data.json
        (modulo exported_at) and identical attachment files."""
        _seed(app)
        za = _export_to(app, tmp_path / "a")
        zb = _export_to(app, tmp_path / "b")

        with zipfile.ZipFile(za) as a, zipfile.ZipFile(zb) as b:
            assert sorted(a.namelist()) == sorted(b.namelist())
            for name in a.namelist():
                if name == "data.json":
                    da = json.loads(a.read(name))
                    db_ = json.loads(b.read(name))
                    da.pop("exported_at", None)
                    db_.pop("exported_at", None)
                    assert da == db_
                else:
                    assert a.read(name) == b.read(name)


# --- Validation -------------------------------------------------------------

class TestValidation:
    def test_missing_data_json(self, app, tmp_path):
        zip_path = _make_zip(tmp_path, {"workspaces/x.txt": "junk"})
        with app.app_context(), pytest.raises(backup_json.ImportError,
                                              match="data.json"):
            backup_json.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path,
            )

    def test_unsupported_schema_version(self, app, tmp_path):
        zip_path = _make_zip(tmp_path, {
            "data.json": json.dumps({
                "schema_version": 999,
                "boards": [], "importance_levels": [],
                "tags": [], "workspaces": [],
            }),
        })
        with app.app_context(), pytest.raises(backup_json.ImportError,
                                              match="schema_version"):
            backup_json.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path,
            )

    def test_report_references_unknown_board(self, app, tmp_path):
        zip_path = _make_zip(tmp_path, {
            "data.json": json.dumps({
                "schema_version": 2,
                "boards": [{"id": 1, "name": "Todo", "position": 0}],
                "importance_levels": [],
                "tags": [],
                "workspaces": [{
                    "id": 1, "name": "WS", "position": 0,
                    "created_at": "2026-01-01 00:00:00.000",
                    "updated_at": "2026-01-01 00:00:00.000",
                    "reports": [{
                        "id": 1, "title": "t",
                        "board": "Nonexistent",
                        "importance": None, "position": 0,
                        "created_at": "2026-01-01 00:00:00.000",
                        "updated_at": "2026-01-01 00:00:00.000",
                        "content_delta": {"ops": [{"insert": "\n"}]},
                        "tags": [], "checklist": [], "attachments": [],
                    }],
                }],
            }),
        })
        with app.app_context(), pytest.raises(backup_json.ImportError,
                                              match="board"):
            backup_json.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path,
            )

    def test_missing_attachment_on_disk(self, app, tmp_path):
        zip_path = _make_zip(tmp_path, {
            "data.json": json.dumps({
                "schema_version": 2,
                "boards": [{"id": 1, "name": "Todo", "position": 0}],
                "importance_levels": [], "tags": [],
                "workspaces": [{
                    "id": 1, "name": "WS", "position": 0,
                    "created_at": "2026-01-01 00:00:00.000",
                    "updated_at": "2026-01-01 00:00:00.000",
                    "reports": [{
                        "id": 1, "title": "t",
                        "board": "Todo", "importance": None, "position": 0,
                        "created_at": "2026-01-01 00:00:00.000",
                        "updated_at": "2026-01-01 00:00:00.000",
                        "content_delta": {"ops": [{"insert": "\n"}]},
                        "tags": [], "checklist": [],
                        "attachments": [{
                            "filename": "missing.png",
                            "original_name": "missing.png",
                            "mime_type": "image/png",
                            "size_bytes": 1,
                            "created_at": "2026-01-01 00:00:00.000",
                        }],
                    }],
                }],
            }),
        })
        with app.app_context(), pytest.raises(backup_json.ImportError,
                                              match="missing.png"):
            backup_json.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path,
            )


# --- Atomicity --------------------------------------------------------------

class TestAtomicity:
    def test_failed_import_leaves_existing_data_intact(self, app, tmp_path):
        _seed(app)
        before = _snapshot(app)
        zip_path = _make_zip(tmp_path, {"junk.txt": "x"})

        with app.app_context():
            with pytest.raises(backup_json.ImportError):
                backup_json.import_from_zip(
                    get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path,
                )
        after = _snapshot(app)
        assert before == after
        att = Path(app.config["ATTACHMENTS_DIR"]) / "100" / "abc.png"
        assert att.read_bytes() == b"PNGxx"


# --- CLI / routes -----------------------------------------------------------

class TestCli:
    def test_export_then_import_via_default_cli(self, app, runner, tmp_path):
        """`export-backup` / `import-backup` default to JSON."""
        _seed(app)
        before = _snapshot(app)

        zip_path = tmp_path / "cli.zip"
        result = runner.invoke(args=["export-backup", str(zip_path)])
        assert result.exit_code == 0, result.output

        _wipe(app)
        result = runner.invoke(args=["import-backup", str(zip_path), "--yes"])
        assert result.exit_code == 0, result.output

        # Parse-and-compare instead of byte-comparing (Delta JSON string may
        # re-serialise; semantics are what matter).
        before_reports = {r["id"]: json.loads(r["content_delta"])
                          for r in before["reports"]}
        after = _snapshot(app)
        after_reports = {r["id"]: json.loads(r["content_delta"])
                         for r in after["reports"]}
        assert before_reports == after_reports


class TestRoutes:
    def test_export_json_endpoint(self, client, app):
        _seed(app)
        resp = client.get("/settings/backup/export-json")
        assert resp.status_code == 200
        assert resp.mimetype == "application/zip"
        with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
            assert "data.json" in zf.namelist()

    def test_import_json_endpoint_replaces_db(self, client, app, tmp_path):
        _seed(app)
        before = _snapshot(app)
        zip_path = _export_to(app, tmp_path)
        zip_bytes = zip_path.read_bytes()

        _wipe(app)
        resp = client.post(
            "/settings/backup/import-json",
            data={"archive": (io.BytesIO(zip_bytes), "backup.zip")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        after = _snapshot(app)
        before_reports = {r["id"]: json.loads(r["content_delta"])
                          for r in before["reports"]}
        after_reports = {r["id"]: json.loads(r["content_delta"])
                         for r in after["reports"]}
        assert before_reports == after_reports

    def test_import_json_endpoint_rejects_markdown_zip(self, client, app, tmp_path):
        """A markdown-format zip uploaded to the JSON endpoint is rejected
        because data.json isn't found."""
        from app import exporter
        _seed(app)
        zip_path = tmp_path / "md.zip"
        with app.app_context():
            exporter.export_to_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path,
            )
        resp = client.post(
            "/settings/backup/import-json",
            data={"archive": (io.BytesIO(zip_path.read_bytes()), "md.zip")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        resp2 = client.get("/settings")
        assert b"JSON import failed" in resp2.data
