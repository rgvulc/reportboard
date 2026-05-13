"""Backup export/import tests: round-trip, validation, atomicity, CLI."""

import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest
import yaml

from app import exporter, importer
from app.db import get_db, init_db
from app.delta_md import md_to_delta


def _delta_json(md: str) -> str:
    """Helper: build a canonical Delta JSON string from a markdown fixture."""
    return json.dumps(md_to_delta(md), ensure_ascii=False)


# --- Helpers ----------------------------------------------------------------

def _seed_complex(app):
    """Build a small but representative DB. Returns a snapshot dict."""
    with app.app_context():
        db = get_db()
        with db:
            # Workspaces
            db.execute(
                "INSERT INTO workspace (id, name, position, created_at, updated_at) "
                "VALUES (1, 'Personal', 0, '2026-01-01 10:00:00.000', '2026-01-02 10:00:00.000')"
            )
            db.execute(
                "INSERT INTO workspace (id, name, position, created_at, updated_at) "
                "VALUES (2, 'Work', 1, '2026-01-03 10:00:00.000', '2026-01-04 10:00:00.000')"
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
            db.execute("INSERT INTO tag (id, name) VALUES (12, 'orphan')")  # unreferenced

            r100_delta = _delta_json(
                "See screenshot: ![s](/attachments/100/abc.png) and notes."
            )
            r101_delta = _delta_json("")
            r102_delta = _delta_json("plain")
            db.execute(
                "INSERT INTO report (id, workspace_id, board_id, importance_id, "
                "title, content_delta, position, created_at, updated_at) "
                "VALUES (100, 1, ?, ?, 'Buy milk', ?, "
                "0, '2026-01-05 09:00:00.000', '2026-01-06 09:00:00.000')",
                (todo_id, high_id, r100_delta),
            )
            db.execute(
                "INSERT INTO report (id, workspace_id, board_id, importance_id, "
                "title, content_delta, position, created_at, updated_at) "
                "VALUES (101, 1, ?, NULL, 'Empty report', ?, 1, "
                "'2026-01-05 09:00:00.000', '2026-01-06 09:00:00.000')",
                (done_id, r101_delta),
            )
            db.execute(
                "INSERT INTO report (id, workspace_id, board_id, importance_id, "
                "title, content_delta, position, created_at, updated_at) "
                "VALUES (102, 2, ?, NULL, 'Weird/title: foo', ?, 0, "
                "'2026-01-05 09:00:00.000', '2026-01-06 09:00:00.000')",
                (todo_id, r102_delta),
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

        # Write the actual attachment file on disk.
        att_dir = Path(app.config["ATTACHMENTS_DIR"]) / "100"
        att_dir.mkdir(parents=True, exist_ok=True)
        (att_dir / "abc.png").write_bytes(b"PNGxx")


def _snapshot(app) -> dict:
    """Capture full DB contents for equality checks."""
    with app.app_context():
        db = get_db()
        return {
            "boards": [dict(r) for r in db.execute(
                "SELECT * FROM board ORDER BY id"
            )],
            "importance_levels": [dict(r) for r in db.execute(
                "SELECT * FROM importance_level ORDER BY id"
            )],
            "tags": [dict(r) for r in db.execute(
                "SELECT * FROM tag ORDER BY id"
            )],
            "workspaces": [dict(r) for r in db.execute(
                "SELECT * FROM workspace ORDER BY id"
            )],
            "reports": [dict(r) for r in db.execute(
                "SELECT * FROM report ORDER BY id"
            )],
            "report_tag": [dict(r) for r in db.execute(
                "SELECT * FROM report_tag ORDER BY report_id, tag_id"
            )],
            "checklist_item": [dict(r) for r in db.execute(
                "SELECT * FROM checklist_item ORDER BY id"
            )],
            "attachment": [dict(r) for r in db.execute(
                "SELECT * FROM attachment ORDER BY id"
            )],
        }


def _wipe(app):
    """Clear every row, mirroring what the importer does on apply."""
    with app.app_context():
        db = get_db()
        with db:
            db.execute("DELETE FROM workspace")
            db.execute("DELETE FROM tag")
            db.execute("DELETE FROM board")
            db.execute("DELETE FROM importance_level")
    shutil.rmtree(app.config["ATTACHMENTS_DIR"], ignore_errors=True)
    Path(app.config["ATTACHMENTS_DIR"]).mkdir(parents=True, exist_ok=True)


def _export_to_tmp(app, tmp_path) -> Path:
    zip_path = tmp_path / "out.zip"
    with app.app_context():
        exporter.export_to_zip(
            get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path
        )
    return zip_path


# --- Pure helpers -----------------------------------------------------------

class TestSlugify:
    def test_basic(self):
        assert exporter.slugify("Hello World") == "Hello-World"

    def test_strips_unsafe(self):
        assert exporter.slugify("foo/bar:baz") == "foo-bar-baz"

    def test_unicode_folds(self):
        assert exporter.slugify("Café") == "Cafe"

    def test_empty_falls_back_to_hash(self):
        s = exporter.slugify("🎉🎉🎉")
        assert len(s) == 8 and s.isalnum()

    def test_long_truncates_with_hash(self):
        name = "x" * 200
        s = exporter.slugify(name)
        assert len(s) <= exporter._SLUG_MAX_LEN
        assert "-" in s


class TestUrlRewrite:
    def test_export_strips_prefix(self):
        out = exporter.rewrite_urls_for_export(
            "see ![](/attachments/42/foo.png) and [x](/attachments/42/y.pdf)",
            42,
        )
        assert out == "see ![](attachments/foo.png) and [x](attachments/y.pdf)"

    def test_export_does_not_touch_other_report_ids(self):
        out = exporter.rewrite_urls_for_export(
            "![](/attachments/99/foo.png)", 42
        )
        assert out == "![](/attachments/99/foo.png)"

    def test_import_inverts_export(self):
        original = "![](/attachments/42/foo.png) [x](/attachments/42/y.pdf)"
        exported = exporter.rewrite_urls_for_export(original, 42)
        restored = importer.rewrite_urls_for_import(exported, 42)
        assert restored == original


class TestFrontmatterParse:
    def test_round_trip(self):
        text = "---\nid: 1\ntitle: foo\n---\nbody here\n"
        fm, body = importer.parse_frontmatter(text)
        assert fm == {"id": 1, "title": "foo"}
        assert body == "body here\n"

    def test_missing_frontmatter_raises(self):
        with pytest.raises(importer.ImportError):
            importer.parse_frontmatter("just a body\n")

    def test_invalid_yaml_raises(self):
        with pytest.raises(importer.ImportError):
            importer.parse_frontmatter("---\n: : :\n  bad\n---\nbody")

    def test_non_mapping_raises(self):
        with pytest.raises(importer.ImportError):
            importer.parse_frontmatter("---\n- a\n- b\n---\nbody")


# --- Round-trip end-to-end --------------------------------------------------

class TestRoundTrip:
    def test_full_round_trip_preserves_db(self, app, tmp_path):
        _seed_complex(app)
        before = _snapshot(app)

        zip_path = _export_to_tmp(app, tmp_path)
        _wipe(app)
        with app.app_context():
            importer.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path
            )
        after = _snapshot(app)

        assert before == after

    def test_attachment_file_round_trips(self, app, tmp_path):
        _seed_complex(app)
        zip_path = _export_to_tmp(app, tmp_path)
        _wipe(app)
        with app.app_context():
            importer.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path
            )
        att = Path(app.config["ATTACHMENTS_DIR"]) / "100" / "abc.png"
        assert att.read_bytes() == b"PNGxx"

    def test_orphan_tags_preserved(self, app, tmp_path):
        _seed_complex(app)
        zip_path = _export_to_tmp(app, tmp_path)
        _wipe(app)
        with app.app_context():
            importer.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path
            )
            names = [r["name"] for r in get_db().execute(
                "SELECT name FROM tag ORDER BY name"
            )]
        assert "orphan" in names

    def test_report_content_substring_invariant(self, app, tmp_path):
        """The cleanup invariant `<id>/<filename>` must hold post-import."""
        _seed_complex(app)
        zip_path = _export_to_tmp(app, tmp_path)
        _wipe(app)
        with app.app_context():
            importer.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path
            )
            content = get_db().execute(
                "SELECT content_delta FROM report WHERE id = 100"
            ).fetchone()["content_delta"]
        assert "100/abc.png" in content


class TestIdempotency:
    def test_export_twice_is_identical(self, app, tmp_path):
        _seed_complex(app)
        zip_a = _export_to_tmp(app, tmp_path / "a")
        zip_b = _export_to_tmp(app, tmp_path / "b")

        # manifest.exported_at differs; compare every other file byte-for-byte.
        with zipfile.ZipFile(zip_a) as za, zipfile.ZipFile(zip_b) as zb:
            names_a = sorted(za.namelist())
            names_b = sorted(zb.namelist())
            assert names_a == names_b
            for name in names_a:
                if name == "manifest.json":
                    continue
                assert za.read(name) == zb.read(name), name

    def test_round_trip_then_export_matches(self, app, tmp_path):
        _seed_complex(app)
        zip_a = _export_to_tmp(app, tmp_path / "a")

        _wipe(app)
        with app.app_context():
            importer.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_a
            )

        zip_b = _export_to_tmp(app, tmp_path / "b")
        with zipfile.ZipFile(zip_a) as za, zipfile.ZipFile(zip_b) as zb:
            for name in za.namelist():
                if name == "manifest.json":
                    continue
                assert za.read(name) == zb.read(name), name


# --- Validation rejects -----------------------------------------------------

def _make_zip(tmp_path: Path, files: dict[str, str | bytes]) -> Path:
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, data in files.items():
            if isinstance(data, str):
                zf.writestr(name, data)
            else:
                zf.writestr(name, data)
    return zip_path


class TestValidation:
    def test_missing_manifest(self, app, tmp_path):
        zip_path = _make_zip(tmp_path, {"workspaces/x/_workspace.json": "{}"})
        with app.app_context(), pytest.raises(importer.ImportError, match="manifest"):
            importer.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path
            )

    def test_unsupported_schema_version(self, app, tmp_path):
        manifest = {
            "schema_version": 999,
            "boards": [], "importance_levels": [], "tags": [], "workspaces": [],
        }
        zip_path = _make_zip(tmp_path, {"manifest.json": json.dumps(manifest)})
        with app.app_context(), pytest.raises(importer.ImportError, match="schema_version"):
            importer.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path
            )

    def test_report_references_unknown_board(self, app, tmp_path):
        manifest = {
            "schema_version": 1,
            "boards": [{"id": 1, "name": "Todo", "position": 0}],
            "importance_levels": [],
            "tags": [],
            "workspaces": [{
                "id": 1, "name": "WS", "position": 0,
                "created_at": "2026-01-01 00:00:00.000",
                "updated_at": "2026-01-01 00:00:00.000",
            }],
        }
        report_md = (
            "---\n"
            "id: 1\nboard: Nonexistent\nimportance: null\nposition: 0\n"
            "title: t\ncreated_at: '2026-01-01 00:00:00.000'\n"
            "updated_at: '2026-01-01 00:00:00.000'\n"
            "tags: []\nchecklist: []\nattachments: []\n"
            "---\n"
        )
        zip_path = _make_zip(tmp_path, {
            "manifest.json": json.dumps(manifest),
            "workspaces/001-WS/_workspace.json": json.dumps({
                "id": 1, "name": "WS", "position": 0,
                "created_at": "2026-01-01 00:00:00.000",
                "updated_at": "2026-01-01 00:00:00.000",
            }),
            "workspaces/001-WS/Todo/001-t/report.md": report_md,
        })
        with app.app_context(), pytest.raises(importer.ImportError, match="board"):
            importer.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path
            )

    def test_attachment_file_missing_on_disk(self, app, tmp_path):
        report_md = (
            "---\n"
            "id: 1\nboard: Todo\nimportance: null\nposition: 0\n"
            "title: t\ncreated_at: '2026-01-01 00:00:00.000'\n"
            "updated_at: '2026-01-01 00:00:00.000'\n"
            "tags: []\nchecklist: []\n"
            "attachments:\n"
            "  - filename: missing.png\n"
            "    original_name: missing.png\n"
            "    mime_type: image/png\n"
            "    size_bytes: 1\n"
            "    created_at: '2026-01-01 00:00:00.000'\n"
            "---\n"
        )
        manifest = {
            "schema_version": 1,
            "boards": [{"id": 1, "name": "Todo", "position": 0}],
            "importance_levels": [], "tags": [],
            "workspaces": [{
                "id": 1, "name": "WS", "position": 0,
                "created_at": "2026-01-01 00:00:00.000",
                "updated_at": "2026-01-01 00:00:00.000",
            }],
        }
        zip_path = _make_zip(tmp_path, {
            "manifest.json": json.dumps(manifest),
            "workspaces/001-WS/_workspace.json": json.dumps({
                "id": 1, "name": "WS", "position": 0,
                "created_at": "2026-01-01 00:00:00.000",
                "updated_at": "2026-01-01 00:00:00.000",
            }),
            "workspaces/001-WS/Todo/001-t/report.md": report_md,
        })
        with app.app_context(), pytest.raises(importer.ImportError, match="missing.png"):
            importer.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path
            )

    def test_zip_slip_rejected(self, app, tmp_path):
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../escape.txt", "pwned")
            zf.writestr("manifest.json", json.dumps({
                "schema_version": 1,
                "boards": [], "importance_levels": [],
                "tags": [], "workspaces": [],
            }))
        with app.app_context(), pytest.raises(importer.ImportError, match="outside"):
            importer.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path
            )


# --- Atomicity --------------------------------------------------------------

class TestAtomicity:
    def test_failed_import_leaves_existing_data_intact(self, app, tmp_path):
        _seed_complex(app)
        before = _snapshot(app)

        # Build an obviously broken archive.
        zip_path = _make_zip(tmp_path, {"not-a-manifest.txt": "garbage"})
        with app.app_context():
            with pytest.raises(importer.ImportError):
                importer.import_from_zip(
                    get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path
                )

        after = _snapshot(app)
        assert before == after

        # Attachment file still on disk.
        att = Path(app.config["ATTACHMENTS_DIR"]) / "100" / "abc.png"
        assert att.read_bytes() == b"PNGxx"


# --- CLI --------------------------------------------------------------------

class TestCli:
    def test_export_then_import_via_cli(self, app, runner, tmp_path):
        _seed_complex(app)
        before = _snapshot(app)

        zip_path = tmp_path / "cli.zip"
        result = runner.invoke(args=["export-backup", str(zip_path)])
        assert result.exit_code == 0, result.output
        assert zip_path.exists()

        _wipe(app)
        result = runner.invoke(args=["import-backup", str(zip_path), "--yes"])
        assert result.exit_code == 0, result.output

        assert _snapshot(app) == before

    def test_import_cli_surfaces_validation_error(self, app, runner, tmp_path):
        zip_path = _make_zip(tmp_path, {"junk.txt": "x"})
        result = runner.invoke(
            args=["import-backup", str(zip_path), "--yes"]
        )
        assert result.exit_code != 0
        assert "manifest" in result.output.lower()


# --- Slug collisions --------------------------------------------------------

class TestSlugCollisions:
    def test_two_workspaces_with_colliding_slugs(self, app, tmp_path):
        with app.app_context():
            db = get_db()
            with db:
                db.execute(
                    "INSERT INTO workspace (id, name, position, created_at, updated_at) "
                    "VALUES (1, 'foo/bar', 0, '2026-01-01 10:00:00.000', '2026-01-01 10:00:00.000')"
                )
                db.execute(
                    "INSERT INTO workspace (id, name, position, created_at, updated_at) "
                    "VALUES (2, 'foo bar', 1, '2026-01-01 10:00:00.000', '2026-01-01 10:00:00.000')"
                )

        zip_path = _export_to_tmp(app, tmp_path)
        with zipfile.ZipFile(zip_path) as zf:
            ws_dirs = sorted({
                n.split("/", 2)[1] for n in zf.namelist()
                if n.startswith("workspaces/")
            })
        # Two distinct directories, even though slugify maps both to similar names.
        assert len(ws_dirs) == 2

        _wipe(app)
        with app.app_context():
            importer.import_from_zip(
                get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path
            )
            names = sorted(r["name"] for r in get_db().execute(
                "SELECT name FROM workspace"
            ))
        assert names == ["foo bar", "foo/bar"]


# --- HTTP routes ------------------------------------------------------------

class TestRoutes:
    def test_export_endpoint_returns_zip(self, client, app):
        _seed_complex(app)
        resp = client.get("/settings/backup/export")
        assert resp.status_code == 200
        assert resp.mimetype == "application/zip"
        # The returned bytes are a real zip with manifest.json inside.
        with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
            assert "manifest.json" in zf.namelist()

    def test_import_endpoint_replaces_db(self, client, app, tmp_path):
        _seed_complex(app)
        before = _snapshot(app)
        zip_path = _export_to_tmp(app, tmp_path)
        zip_bytes = zip_path.read_bytes()

        _wipe(app)
        resp = client.post(
            "/settings/backup/import",
            data={"archive": (io.BytesIO(zip_bytes), "backup.zip")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert _snapshot(app) == before

    def test_import_endpoint_rejects_missing_file(self, client):
        resp = client.post(
            "/settings/backup/import",
            data={},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_import_endpoint_surfaces_validation_error(self, client, tmp_path):
        zip_path = _make_zip(tmp_path, {"junk.txt": "x"})
        resp = client.post(
            "/settings/backup/import",
            data={"archive": (io.BytesIO(zip_path.read_bytes()), "bad.zip")},
            content_type="multipart/form-data",
        )
        # Same convention as the rest of the app: redirect body + 400.
        assert resp.status_code == 400
        # Flash survives in the session; render the next page to read it.
        resp2 = client.get("/settings")
        assert b"Import failed" in resp2.data
