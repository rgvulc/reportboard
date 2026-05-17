"""Tests for the `verify-export` CLI command.

It reads a JSON backup zip and reports any report whose Delta does not
round-trip cleanly through markdown (md_to_delta ∘ delta_to_md != id).
"""

import json
import zipfile
from pathlib import Path

from app import backup_json
from app.db import get_db
from app.delta_md import md_to_delta


def _delta_json(md: str) -> str:
    return json.dumps(md_to_delta(md), ensure_ascii=False)


def _seed_clean(app):
    """Seed a workspace with content fully inside the markdown-safe subset."""
    with app.app_context():
        db = get_db()
        with db:
            db.execute(
                "INSERT INTO workspace (id, name, position) VALUES (1, 'WS', 0)"
            )
            todo_id = db.execute(
                "SELECT id FROM board WHERE name='Todo'"
            ).fetchone()["id"]
            db.execute(
                "INSERT INTO report (id, workspace_id, board_id, title, "
                "content_delta, position) VALUES (1, 1, ?, 'r1', ?, 0)",
                (todo_id, _delta_json("# Hi\n\n**bold** and *italic*")),
            )
            db.execute(
                "INSERT INTO report (id, workspace_id, board_id, title, "
                "content_delta, position) VALUES (2, 1, ?, 'r2', ?, 1)",
                (todo_id, _delta_json("- a\n- b")),
            )


def _export(app, tmp_path) -> Path:
    zip_path = tmp_path / "out.zip"
    with app.app_context():
        backup_json.export_to_zip(
            get_db(), Path(app.config["ATTACHMENTS_DIR"]), zip_path,
        )
    return zip_path


class TestVerifyExport:
    def test_clean_export_passes(self, app, runner, tmp_path):
        _seed_clean(app)
        zip_path = _export(app, tmp_path)
        result = runner.invoke(args=["verify-export", str(zip_path)])
        assert result.exit_code == 0, result.output
        assert "OK" in result.output
        assert "2 report(s) round-trip cleanly" in result.output

    def test_dirty_delta_is_flagged(self, app, runner, tmp_path):
        """Inject an attribute that's not in the markdown-safe subset
        (e.g. `underline: true`) and verify the command flags it."""
        _seed_clean(app)
        # Hand-edit the exported data.json to add a dirty attribute, then
        # repack — simulates a Delta produced before paste matchers existed.
        zip_path = _export(app, tmp_path)
        with zipfile.ZipFile(zip_path) as zf:
            data = json.loads(zf.read("data.json"))
        r1 = next(r for ws in data["workspaces"]
                  for r in ws["reports"] if r["id"] == 1)
        # Add an `underline` attribute to one op — not representable in markdown.
        for op in r1["content_delta"]["ops"]:
            if isinstance(op.get("insert"), str) and "Hi" in op["insert"]:
                op.setdefault("attributes", {})["underline"] = True
                break

        dirty_zip = tmp_path / "dirty.zip"
        with zipfile.ZipFile(dirty_zip, "w") as zout:
            zout.writestr("data.json",
                          json.dumps(data, indent=2, ensure_ascii=False) + "\n")

        result = runner.invoke(args=["verify-export", str(dirty_zip)])
        assert result.exit_code != 0
        assert "FAIL" in result.output
        assert "report 1" in result.output

    def test_missing_zip_returns_validation_error(self, app, runner, tmp_path):
        """A zip without data.json is rejected up-front."""
        zip_path = tmp_path / "bad.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("junk.txt", "x")
        result = runner.invoke(args=["verify-export", str(zip_path)])
        assert result.exit_code != 0
        assert "data.json" in result.output
